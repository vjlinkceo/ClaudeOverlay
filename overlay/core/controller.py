"""Application logic between the worker threads and the window, with no toolkit code.

The window implements `View`; the controller never touches widgets, so it is tested with a fake view
and can be hosted by any toolkit.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from PIL import Image

from overlay.ai.base import AnswerStats
from overlay.ai.client import AIChat
from overlay.ai.prompts import (
    DEEPER_EFFORT,
    DEEPER_PROFILE,
    FOLLOWUPS,
    MODE_STYLES,
    build_context,
    build_recap,
    build_summary_content,
    build_user_content,
)
from overlay.config import MODEL_PROFILES, mode_preset, provider_for_model, save_config
from overlay.core.autoanswer import AutoAnswer
from overlay.core.meeting import MeetingLog
from overlay.core.pricing import (
    OUTPUT_TOKENS,
    THINKING_TOKENS,
    estimate_cost,
    estimate_question,
    estimate_tokens,
    format_cost,
    image_tokens,
    system_tokens,
)
from overlay.core.profiles import SETTING_KEYS, Profile, apply_to_config, load_profile, system_block
from overlay.core.regions import region_on_screen, validate_region
from overlay.core.scheduler import Scheduler
from overlay.core.uiqueue import UiQueue
from overlay.i18n import t
from overlay.speech import audio
from overlay.speech.audio import Listener, SpeechEvent
from overlay.speech.stt import Transcriber, download_progress, glossary_terms
from overlay.speech.transcript import Line, Transcript, Utterance
from overlay.speech.voice import VoiceRecorder
from overlay.win.hotkeys import HotkeyThread, pretty_hotkey
from overlay.win.screen import encode_image, grab, monitor_under_cursor, scaled_pixels, screen_monitors

log = logging.getLogger(__name__)

SHOWN_TRANSCRIPT_CHARS = 400
ROLLOVER_KEEP = 2
IDLE_CHECK_MS = 60_000
HAPPY_MS = 1500
PROGRESS_POLL_MS = 500


class Unsent(NamedTuple):
    """What sending took from the window, put back if the request fails."""

    typed: str
    heard: list[Utterance]
    image: str | None
    image_label: str
    image_pixels: int = 0


@dataclass
class AnswerMeta:
    """Shown under a finished answer."""

    stats: AnswerStats
    notice: str
    cost: float | None = None
    kind: str = "answer"  # or "summary": the window offers to save it


class View(Protocol):
    """What the controller needs from a window. Every call happens on the UI thread."""

    def set_status(self, text: str, kind: str = "info") -> None: ...

    def set_mascot(self, state: str) -> None: ...

    def set_listening(self, on: bool) -> None: ...

    def set_level(self, level: float) -> None: ...

    def set_progress(self, fraction: float | None) -> None: ...

    def set_session_cost(self, usd: float) -> None: ...

    def set_estimate(self, costs: dict[str, float | None], tokens_in: int, tokens_out: int) -> None: ...

    def set_context_usage(self, fraction: float, tokens: int, limit: int) -> None: ...

    def set_budget(self, spent: float, budget: float) -> None: ...

    def set_option(self, key: str, value: str) -> None: ...

    def refresh_heard(self, lines: list[Line], labeled: bool, reset: bool = False) -> None: ...

    def heard_override(self) -> str | None: ...

    def typed(self) -> str: ...

    def clear_typed(self) -> None: ...

    def restore_typed(self, text: str) -> None: ...

    def set_attachment(self, label: str | None) -> None: ...

    def show_user_message(self, shown: str) -> None: ...

    def begin_answer(self) -> None: ...

    def update_answer(self, markdown: str) -> None: ...

    def finish_answer(self, meta: AnswerMeta | None, error: str | None) -> None: ...

    def note(self, text: str) -> None: ...

    def clear_chat(self) -> None: ...

    def hide_for_grab(self, fn: Callable[[], None]) -> None: ...

    def select_region(self, on_done: Callable[[dict | None], None]) -> None: ...

    def toggle_window(self) -> None: ...

    def toggle_compact(self) -> None: ...

    def set_voice_recording(self, on: bool) -> None: ...


class AppController:
    def __init__(self, cfg: dict, scheduler: Scheduler, view: View, chat: AIChat | None = None):
        self.cfg = cfg
        self.scheduler = scheduler
        self.view = view
        self.ui = UiQueue(scheduler, on_tick=self._tick, on_error=self._on_ui_error)
        self.chat = chat if chat is not None else AIChat(cfg)
        self.transcript = Transcript()
        self.pending_image: str | None = None
        self.pending_label = ""
        self.pending_pixels = 0
        self.unsent: Unsent | None = None
        self._system_tokens = system_tokens()
        self._budget_warned = False
        self.listeners: dict[str, Listener] = {}
        self.transcriber: Transcriber | None = None
        self.stt_ready = False
        self.voice_recorder: VoiceRecorder | None = None
        self._voice_pending = False
        self._voice_gen = 0
        self._voice_cancel = threading.Event()
        self._voice_interval = (0.0, 0.0)
        self.speaking: dict[str, bool] = {}
        self.levels: dict[str, float] = {}
        self.stt_latency: float | None = None
        self.answer_gen = 0
        self.answering = False
        self.answer_raw = ""
        self.answer_dirty = False
        self.answer_notice = ""
        self.sent_context = ""
        self.context_before_send = ""
        self.pending_recap = ""
        self.messages = 0
        self.session_cost = 0.0
        self.meeting = MeetingLog()
        self.profile: Profile | None = None
        self.answer_kind = "answer"
        self.last_activity = time.monotonic()
        self.hotkeys: HotkeyThread | None = None
        self._current_kind = "answer"
        self._idle_job = None
        self._progress_job = None
        self.auto = AutoAnswer(
            scheduler,
            is_busy=self._in_flight,
            is_talking=self._questioner_talking,
            fire=lambda: self.send(include_typed=False),
        )
        self.auto.enabled = self.cfg["auto_answer"]

    def start(self) -> None:
        """Polling, global hotkeys and the idle timer, once the window exists."""
        self.ui.start()
        self._load_profile()
        self._start_hotkeys()
        self._idle_job = self.scheduler.after(IDLE_CHECK_MS, self._check_idle)
        if not self.chat.configured:
            self.set_status(t("no_key"), "error")
        else:
            self.set_idle_status()
        self._update_mascot()
        self._update_meters()

    def shutdown(self) -> None:
        self.cancel_voice()
        save_config(self.cfg)
        self.stop_listen()
        if self.transcriber:
            self.transcriber.stop()
        self.chat.cancel()
        if self.hotkeys:
            self.hotkeys.stop()
        self.ui.stop()
        if self._idle_job is not None:
            self.scheduler.after_cancel(self._idle_job)
        self._stop_progress()

    def _start_hotkeys(self) -> None:
        self.hotkeys = HotkeyThread(
            self.cfg["hotkeys"],
            on_hotkey=lambda name: self.post(self.on_hotkey, name),
            on_error=lambda msg: self.post(self.set_status, msg, "error"),
        )
        self.hotkeys.start()

    def restart_hotkeys(self) -> None:
        """After a combination was changed in the settings. The old registrations go first."""
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys.join(timeout=1.0)
        self._start_hotkeys()

    def post(self, fn, *args) -> None:
        """Thread-safe: hands a call to the UI thread."""
        self.ui.post(fn, *args)

    def _tick(self) -> None:
        if self.answer_dirty:
            self.answer_dirty = False
            self.view.update_answer(self.answer_raw)

    def _on_ui_error(self, error: Exception) -> None:
        self.set_status(t("ui_error", error=error), "error")

    # --- status ---

    def set_status(self, text: str, kind: str = "info") -> None:
        self.view.set_status(text, kind)

    def set_idle_status(self) -> None:
        if self._voice_pending:
            self.set_status(t("voice_recording" if self.voice_recorder else "voice_transcribing"), "busy")
            return
        if not self.listeners:
            hk = self.cfg["hotkeys"]
            self.set_status(t("ready", screenshot=pretty_hotkey(hk["screenshot"]), listen=pretty_hotkey(hk["listen"])))
            return
        if not self.stt_ready:
            self.set_status(t("loading_stt"), "busy")
            return
        source = "both" if len(self.listeners) > 1 else next(iter(self.listeners))
        parts = [t("listening", source=t(f"source_{source}"))]
        if self.transcriber and self.transcriber.device:
            parts.append(t("device_gpu") if self.transcriber.device == "cuda" else t("device_cpu"))
        if self.stt_latency is not None:
            parts.append(t("stt_latency", sec=self.stt_latency))
        self.set_status(" · ".join(parts), "ok")

    def _update_mascot(self) -> None:
        if self._voice_pending:
            state = "hearing" if self.voice_recorder else "thinking"
        elif self.answering:
            state = "talking" if self.answer_raw else "thinking"
        elif any(self.speaking.values()):
            state = "hearing"
        elif self.listeners:
            state = "listening"
        else:
            state = "sleep"
        self.view.set_mascot(state)

    def _in_flight(self) -> bool:
        """From send until the done event is handled. chat.busy alone drops a moment earlier, and a send
        in that gap (auto-answer retries every 300 ms) would discard the done event together with the
        previous question's error and unsent data."""
        return self.answering or self.chat.busy or self._voice_pending

    def _busy(self) -> bool:
        if self._in_flight():
            self.set_status(t("still_answering"), "busy")
            return True
        return False

    # --- options changed in the window ---

    def on_source_change(self) -> None:
        """The audio source or a device changed: listeners are reopened."""
        if self.listeners:
            self.stop_listen()
            self.start_listen()
        self._refresh_heard()

    def on_mode_change(self) -> None:
        model, effort = mode_preset(self.cfg["mode"], self.cfg["model"])
        self.set_option("model", model)
        self.set_option("effort", effort)

    def set_option(self, key: str, value: str) -> None:
        self.cfg[key] = value
        self.view.set_option(key, value)
        self._update_meters()

    def on_speech_lang_change(self) -> None:
        if self.transcriber:
            self.transcriber.reset_languages()

    def on_auto_change(self, enabled: bool) -> None:
        self.cfg["auto_answer"] = self.auto.enabled = enabled
        if not enabled:
            self.auto.cancel()

    # --- listening ---

    def toggle_listen(self) -> None:
        if self.listeners:
            self.stop_listen()
        else:
            self.start_listen()

    def start_listen(self) -> None:
        if not audio.ensure_loaded():
            self.set_status(t("no_audio_devices"), "error")
            return
        if self.transcriber is not None:
            self.transcriber.reset_languages()
        self._ensure_transcriber()
        for source in ("system", "mic") if self.cfg["audio_source"] == "both" else (self.cfg["audio_source"],):
            listener = self._make_listener(source)
            self.listeners[source] = listener
            listener.start()
        self.view.set_listening(True)
        self.set_idle_status()
        self._update_mascot()

    def _ensure_transcriber(self) -> None:
        if self.transcriber is None:
            self.stt_ready = False
            self.transcriber = Transcriber(
                self.cfg,
                on_partial=lambda source, text: self.post(self._on_partial, source, text),
                on_final=lambda source, utt: self.post(self._on_final, source, utt),
                on_status=lambda text, kind: self.post(self._on_stt_status, text, kind),
            )
            self.transcriber.start()

    def start_voice(self) -> None:
        if self._busy():
            return
        if not self.chat.configured:
            self.set_status(t("no_key"), "error")
            return
        if not audio.ensure_loaded():
            self.set_status(t("no_audio_devices"), "error")
            return
        self.auto.cancel()
        self._ensure_transcriber()
        self._voice_gen += 1
        gen = self._voice_gen
        self._voice_cancel = threading.Event()
        self._voice_pending = True
        self._voice_interval = (time.time(), float("inf"))
        self.voice_recorder = VoiceRecorder(
            self.cfg["mic_device"],
            on_ready=lambda: self.post(self._on_voice_ready, gen),
            on_done=lambda samples, error: self.post(self._on_voice_audio, gen, samples, error),
        )
        self.set_status(t("voice_connecting"), "busy")
        self.view.set_voice_recording(True)
        self.voice_recorder.start()
        self._touch()
        self._update_mascot()

    def _on_voice_ready(self, gen: int) -> None:
        if gen == self._voice_gen and self._voice_pending:
            self.set_status(t("voice_recording"), "busy")

    def finish_voice(self) -> None:
        if self.voice_recorder is not None:
            self.voice_recorder.finish()

    def cancel_voice(self) -> bool:
        active = self._voice_pending
        self._voice_gen += 1
        self._voice_cancel.set()
        if self.voice_recorder is not None:
            self.voice_recorder.cancel()
            self.voice_recorder = None
        self._voice_interval = (self._voice_interval[0], time.time())
        self._voice_pending = False
        if active:
            self.view.set_voice_recording(False)
        return active

    def _on_voice_audio(self, gen: int, samples, error: str | None) -> None:
        if gen != self._voice_gen:
            return
        self.voice_recorder = None
        self._voice_interval = (self._voice_interval[0], time.time())
        self.view.set_voice_recording(False)
        if error or samples is None or self.transcriber is None:
            self._voice_pending = False
            self.set_status(error or t("voice_empty"), "error" if error else "info")
            self._update_mascot()
            return
        self.set_status(t("voice_transcribing"), "busy")
        self.transcriber.submit_voice(
            samples,
            on_done=lambda text, error: self.post(self._on_voice_text, gen, text, error),
            cancel_event=self._voice_cancel,
        )
        self._update_mascot()

    def _on_voice_text(self, gen: int, text: str, error: str | None) -> None:
        if gen != self._voice_gen:
            return
        self._voice_pending = False
        if error or not text.strip():
            self.set_status(error or t("voice_empty"), "error" if error else "info")
            self._update_mascot()
            return
        self.send(typed_override=text.strip())

    def _make_listener(self, source: str) -> Listener:
        # The error carries its listener: one stopped by a source switch may still report late.
        listener = Listener(
            source,
            float(self.cfg["vad_threshold"]),
            float(self.cfg["end_silence"]),
            on_event=self._on_speech_event,
            on_error=lambda msg: self.post(self._on_listen_error, listener, msg),
            device_name=self.cfg["mic_device"] if source == "mic" else self.cfg["speaker_device"],
        )
        return listener

    def stop_listen(self) -> None:
        for listener in self.listeners.values():
            listener.stop_event.set()
        self.listeners = {}
        self.speaking.clear()
        self.levels.clear()
        self.view.set_level(0.0)
        self.transcript.partials.clear()
        self._refresh_heard()
        self.view.set_listening(False)
        self.set_idle_status()
        self._update_mascot()

    def _on_listen_error(self, listener: Listener, msg: str) -> None:
        """With PC + mic, a failed source does not stop the other one."""
        if self.listeners.get(listener.source) is not listener:
            return
        del self.listeners[listener.source]
        if self.listeners:
            self.speaking.pop(listener.source, None)
            self.levels.pop(listener.source, None)
            self.transcript.partials.pop(listener.source, None)
            self._refresh_heard()
        else:
            self.stop_listen()
        self.set_status(msg, "error")

    def _on_stt_status(self, text, kind) -> None:
        if kind == "downloading":
            self.set_status(text, "busy")
            self._start_progress()
        elif kind == "ready":
            self._stop_progress()
            self.stt_ready = True
            self.set_idle_status()
        elif kind == "error":
            self._stop_progress()
            self.cancel_voice()
            self.stop_listen()
            self.transcriber = None
            self.set_status(text, "error")
        else:
            self.set_status(text, kind)

    def _start_progress(self) -> None:
        if self._progress_job is None:
            self._progress_job = self.scheduler.after(PROGRESS_POLL_MS, self._poll_progress)

    def _poll_progress(self) -> None:
        self._progress_job = None
        name = self.transcriber.downloading_model if self.transcriber else None
        if not name:
            self.view.set_progress(None)
            return
        self.view.set_progress(download_progress(name))
        self._progress_job = self.scheduler.after(PROGRESS_POLL_MS, self._poll_progress)

    def _stop_progress(self) -> None:
        if self._progress_job is not None:
            self.scheduler.after_cancel(self._progress_job)
            self._progress_job = None
        self.view.set_progress(None)

    def _on_speech_event(self, ev: SpeechEvent) -> None:
        """Called from a recording thread."""
        if ev.kind == "level":
            self.post(self._set_level, ev.source, ev.level)
            return
        if ev.kind == "start":
            self.post(self._set_speaking, ev.source, True)
            return
        if self.transcriber:
            self.transcriber.submit(ev)
        if ev.kind == "final":
            self.post(self._set_speaking, ev.source, False)

    def _set_speaking(self, source: str, on: bool) -> None:
        self.speaking[source] = on
        if not self.answering:
            self._update_mascot()

    def _set_level(self, source: str, level: float) -> None:
        if source in self.listeners:
            self.levels[source] = level
            self.view.set_level(max(self.levels.values(), default=0.0))

    def _on_partial(self, source: str, text: str) -> None:
        if source in self.listeners:
            self.transcript.set_partial(source, text)
            self._refresh_heard()

    def _on_final(self, source: str, utt: Utterance | None) -> None:
        if utt is None:
            self.transcript.set_partial(source, "")
            self._refresh_heard()
            return
        self._touch()
        self.meeting.append(utt)
        self.stt_latency = max(0.0, time.time() - utt.t_end)
        added = self.transcript.add(utt, echo_filter=self._labeled())
        self._refresh_heard()
        if not self._in_flight():
            self.set_idle_status()
        voice_start, voice_end = self._voice_interval
        dictating = source == "mic" and utt.t_end >= voice_start and utt.t_start <= voice_end
        if added and self._is_questioner(source) and not self._voice_pending and not dictating:
            self.auto.heard(added.text)

    def _labeled(self) -> bool:
        return self.cfg["audio_source"] == "both"

    def _is_questioner(self, source: str) -> bool:
        """With PC + mic, questions come from the other side, not from the user."""
        return not self._labeled() or source == "system"

    def _questioner_talking(self) -> bool:
        return any(on for source, on in self.speaking.items() if self._is_questioner(source))

    def _refresh_heard(self, reset: bool = False) -> None:
        self.view.refresh_heard(self.transcript.lines(), self._labeled(), reset)
        self._update_meters()

    def clear_heard(self) -> None:
        self.transcript.wipe()
        self.auto.cancel()
        self._refresh_heard(reset=True)

    # --- images ---

    def attach_image(self, img: Image.Image) -> None:
        max_side = int(self.cfg["screenshot_max_side"])
        self._attach(encode_image(img, max_side), t("image_attached"), scaled_pixels(img.size, max_side))

    def _attach(self, image_b64: str, label: str, pixels: int = 0) -> None:
        self.pending_image = image_b64
        self.pending_label = label
        self.pending_pixels = pixels
        self.view.set_attachment(label)
        self._update_meters()

    def clear_image(self) -> None:
        self.pending_image = None
        self.pending_label = ""
        self.pending_pixels = 0
        self.view.set_attachment(None)
        self._update_meters()

    def screenshot_and_ask(self) -> None:
        if self._busy():
            return
        self.view.hide_for_grab(lambda: self._grab_and_send(lambda: grab(monitor_under_cursor()), t("screenshot_tag")))

    def area_and_ask(self) -> None:
        if self._busy():
            return
        region = validate_region(self.cfg.get("saved_area"))
        if region is not None:
            try:
                visible = region_on_screen(region, screen_monitors())
            except Exception as exc:
                self.set_status(t("screenshot_failed", error=exc), "error")
                return
            if not visible:
                self.set_status(t("saved_area_invalid"), "error")
                return
            self.view.hide_for_grab(lambda: self._grab_and_send(lambda: grab(region), t("area_tag")))
            return
        self.choose_area()

    def choose_area(self) -> None:
        if self._busy():
            return
        self.view.select_region(self._on_area_selected)

    def clear_saved_area(self) -> None:
        self.cfg["saved_area"] = None
        save_config(self.cfg)
        self.set_status(t("saved_area_cleared"))

    def _on_area_selected(self, region: dict | None) -> None:
        region = validate_region(region)
        if region is None:
            return
        self.cfg["saved_area"] = region
        save_config(self.cfg)
        self._grab_and_send(lambda: grab(region), t("area_tag"))

    def _grab_and_send(self, grab_image: Callable[[], Image.Image], tag: str) -> None:
        try:
            img = grab_image()
        except Exception as e:
            log.exception("screenshot failed")
            self.set_status(t("screenshot_failed", error=e), "error")
            return
        self.set_status(t("preparing_screenshot"), "busy")
        max_side = int(self.cfg["screenshot_max_side"])
        pixels = scaled_pixels(img.size, max_side)

        def encode():
            try:
                self.post(self._send_screenshot, encode_image(img, max_side), tag, pixels)
            except Exception as e:
                log.exception("screenshot encode failed")
                self.post(self.set_status, t("screenshot_failed", error=e), "error")

        threading.Thread(target=encode, name="encode", daemon=True).start()

    def _send_screenshot(self, image_b64: str, tag: str, pixels: int = 0) -> None:
        if self._in_flight():
            self._attach(image_b64, t("screenshot_attached"), pixels)
            self.set_status(t("still_answering_attached"), "busy")
            return
        self.send(image_b64=image_b64, image_tag=tag, image_pixels=pixels)

    # --- questions and answers ---

    def _context(self) -> str:
        return build_context(self.cfg.get("context", ""), glossary_terms(self.cfg.get("glossary", "")))

    def send(
        self,
        image_b64: str | None = None,
        include_typed: bool = True,
        image_tag: str | None = None,
        image_pixels: int = 0,
        typed_override: str | None = None,
    ) -> None:
        if self._busy():
            return
        self.auto.cancel()
        typed = typed_override if typed_override is not None else self.view.typed().strip() if include_typed else ""
        max_chars = int(self.cfg["max_transcript_chars"])
        override = self.view.heard_override()
        if override is not None:
            heard = override[-max_chars:].strip()
        else:
            heard = self.transcript.text_for_prompt(self._labeled(), max_chars)
        image = image_b64 or self.pending_image
        if not (typed or heard or image):
            self.set_status(t("nothing_to_send"))
            return
        if not self.chat.configured:
            self.set_status(t("no_key"), "error")
            return

        recap = self._rollover_recap()
        context = self._context()
        content = build_user_content(
            transcript=heard,
            labeled=self._labeled(),
            typed=typed,
            image_b64=image,
            context=context if context != self.sent_context else None,
            answer_language=self.cfg["answer_language"],
            style=MODE_STYLES[self.cfg["mode"]],
            recap=recap,
        )
        self.context_before_send, self.sent_context = self.sent_context, context
        shown_heard = heard if len(heard) <= SHOWN_TRANSCRIPT_CHARS else "…" + heard[-SHOWN_TRANSCRIPT_CHARS:]
        tag = (image_tag or t("screenshot_tag")) if image else ""
        shown = " ".join(p for p in (tag, f"«{shown_heard}»" if heard else "", typed) if p)
        image_label = t("screenshot_attached") if image_b64 else self.pending_label
        pixels = image_pixels if image_b64 else self.pending_pixels
        unsent = Unsent(typed, list(self.transcript.items), image, image_label, pixels)
        if not self._start_answer(content, shown, self.cfg["model"], self.cfg["effort"], unsent):
            return
        if include_typed and typed_override is None:
            self.view.clear_typed()
        self.transcript.clear()
        self._refresh_heard(reset=True)
        self.clear_image()

    def followup(self, kind: str) -> None:
        if self._busy():
            return
        if not self.chat.history and not self.pending_recap:
            self.set_status(t("nothing_to_follow"))
            return
        if not self.chat.configured:
            self.set_status(t("no_key"), "error")
            return
        recap = self._rollover_recap()
        content = build_user_content(typed=FOLLOWUPS[kind], answer_language=self.cfg["answer_language"], recap=recap)
        self.context_before_send = self.sent_context
        if kind == "deeper":
            profile = DEEPER_PROFILE if provider_for_model(self.cfg["model"]) == "anthropic" else "gpt_quality"
            effort = DEEPER_EFFORT
        else:
            profile, effort = self.cfg["model"], self.cfg["effort"]
        self._start_answer(content, f"↳ {t(f'followup_{kind}')}", profile, effort)

    def summarize(self, minutes: int | None) -> None:
        """A recap of the last minutes, or of everything heard when `minutes` is None."""
        if self._busy():
            return
        if not self.chat.configured:
            self.set_status(t("no_key"), "error")
            return
        text = self.meeting.text(minutes, self._labeled(), int(self.cfg["max_transcript_chars"]))
        if not text:
            self.set_status(t("nothing_heard_yet"))
            return
        recap = self._rollover_recap()
        content = build_summary_content(
            text, labeled=self._labeled(), minutes=minutes, answer_language=self.cfg["answer_language"], recap=recap
        )
        self.context_before_send = self.sent_context
        if minutes:
            shown, profile, effort = t("recap_shown", minutes=minutes), self.cfg["model"], "low"
        else:
            shown = t("call_summary_shown")
            profile, effort = mode_preset("explain", self.cfg["model"])
        self.answer_kind = "summary"
        self._start_answer(content, shown, profile, effort)

    def _rollover_recap(self) -> str:
        """Long chats get slower and pricier: start a new one and carry the last answers over as text."""
        if self.chat.history and self.chat.context_tokens > int(self.cfg["max_history_tokens"]):
            log.info("chat restarted at %d context tokens", self.chat.context_tokens)
            self.pending_recap = build_recap(self.chat.rollover(ROLLOVER_KEEP))
            self.sent_context = ""
            self.view.note(t("chat_restarted"))
        return self.pending_recap

    def _start_answer(
        self, content: list[dict], shown: str, profile: str, effort: str, unsent: Unsent | None = None
    ) -> bool:
        """False if the previous request is still running: nothing is shown or taken from the window."""
        gen = self.answer_gen + 1
        started = self.chat.ask(
            content, profile, effort, on_event=lambda kind, data: self.post(self._on_claude, gen, kind, data)
        )
        if not started:
            log.warning("send while the previous request is still running")
            self.set_status(t("still_answering"), "busy")
            return False
        # Events from the new thread wait in the queue until this callback returns, so the state
        # below is in place before the first of them is handled.
        self._touch()
        self.answer_gen = gen
        self.answering = True
        self.unsent = unsent
        self.answer_raw = ""
        self.answer_notice = ""
        self.messages += 1
        kind, self.answer_kind = self.answer_kind, "answer"
        self._current_kind = kind
        self.view.show_user_message(shown)
        self.view.begin_answer()
        self.set_status(t("asking", model=MODEL_PROFILES[profile]["label"]), "busy")
        self._update_mascot()
        return True

    def _on_claude(self, gen: int, kind: str, data) -> None:
        if gen != self.answer_gen:
            return
        if kind == "delta":
            if not self.answer_raw:
                self.set_status(t("answering"), "busy")
                self.answer_raw = data
                self._update_mascot()
            else:
                self.answer_raw += data
            self.answer_dirty = True
        elif kind == "thinking":
            self.set_status(t("thinking"), "busy")
        elif kind == "notice":
            self.answer_notice = data
            self.set_status(data, "busy")
        elif kind == "done":
            self._on_done(*data)

    def _on_done(self, error: str | None, stats: AnswerStats) -> None:
        self.answering = False
        self.answer_dirty = False
        self.view.update_answer(self.answer_raw)
        unsent, self.unsent = self.unsent, None
        if error:
            self.sent_context = self.context_before_send
            self._restore_unsent(unsent)
            self.view.finish_answer(None, error)
            self.set_status(error, "info" if stats.cancelled else "error")
            if stats.cancelled:
                self._update_mascot()
            else:
                self.view.set_mascot("error")
        else:
            self.pending_recap = ""
            cost = estimate_cost(stats)
            exceeded = False
            if cost is not None:
                self.session_cost += cost
                self.view.set_session_cost(self.session_cost)
                exceeded = self._add_spent(cost)
            self.view.finish_answer(AnswerMeta(stats, self.answer_notice, cost, self._current_kind), None)
            self.set_idle_status()
            if exceeded:
                self.set_status(
                    t(
                        "budget_exceeded",
                        spent=format_cost(self.cfg["spent_usd"]),
                        budget=format_cost(self.cfg["budget_usd"]),
                    ),
                    "error",
                )
            self.view.set_mascot("happy")
            self.scheduler.after(HAPPY_MS, self._update_mascot)
        self._update_meters()
        last = self.transcript.items[-1] if self.transcript.items else None
        # After an error or Stop the restored question must not fire again by itself.
        if not error and last and self._is_questioner(last.source):
            self.auto.answer_finished(last.text)

    def _restore_unsent(self, unsent: Unsent | None) -> None:
        """A failed or stopped question goes back to the input, Heard and the attachment, so it can be
        sent again. Whatever the user added meanwhile is kept."""
        if unsent is None:
            return
        if unsent.typed and not self.view.typed().strip():
            self.view.restore_typed(unsent.typed)
        if unsent.heard:
            self.transcript.restore(unsent.heard)
            self._refresh_heard()
        if unsent.image and not self.pending_image:
            self._attach(unsent.image, unsent.image_label, unsent.image_pixels)

    def stop_answer(self) -> None:
        self.auto.cancel()
        if self.cancel_voice():
            self.set_idle_status()
            self._update_mascot()
        if self.chat.busy:
            self.chat.cancel()

    def new_chat(self) -> None:
        self.cancel_voice()
        self.chat.reset()
        # The cancelled request's done event is dropped by the generation check, so it cannot clear this.
        self.answer_gen += 1
        self.answering = False
        self.unsent = None
        self.sent_context = ""
        self.pending_recap = ""
        self.messages = 0
        self.meeting.wipe()
        self.view.clear_chat()
        self.clear_heard()
        self.clear_image()
        if self.transcriber:
            self.transcriber.forget_text()
        self.set_idle_status()
        self._update_mascot()
        self._update_meters()

    # --- profiles ---

    def _load_profile(self) -> None:
        slug = self.cfg.get("profile") or ""
        self.profile = load_profile(slug) if slug else None
        if slug and self.profile is None:
            self.cfg["profile"] = ""
        self.chat.system_extra = system_block(self.profile)

    def set_profile(self, slug: str | None) -> None:
        """Apply a profile (None for none). The system prompt changes, so a running chat restarts."""
        profile = load_profile(slug) if slug else None
        if slug and profile is None:
            self.cfg["profile"] = ""
            return
        extra = system_block(profile)
        if self.chat.history and extra != self.chat.system_extra:
            self.pending_recap = build_recap(self.chat.rollover(ROLLOVER_KEEP))
            self.sent_context = ""
            self.view.note(t("profile_switched", name=profile.name) if profile else t("profile_cleared_note"))
        self.profile = profile
        self.chat.system_extra = extra
        if profile is not None:
            apply_to_config(profile, self.cfg)
            for key in SETTING_KEYS:
                self.view.set_option(key, self.cfg[key])
            self.on_speech_lang_change()
            if self.listeners:
                self.on_source_change()
        else:
            self.cfg["profile"] = ""
        self.set_status(t("profile_applied", name=profile.name) if profile else t("profile_cleared"), "ok")
        self._update_meters()

    def reload_profile(self) -> None:
        """The active profile was edited."""
        if self.profile is not None:
            self.set_profile(self.profile.slug)

    # --- price and budget ---

    def typed_changed(self) -> None:
        self._update_meters()

    def update_meters(self) -> None:
        self._update_meters()

    def estimate(self) -> tuple[dict[str, float | None], int, int]:
        """(cost per model profile, input tokens, output tokens) for the question as it stands now."""
        override = self.view.heard_override()
        if override is None:
            heard = self.transcript.text_for_prompt(self._labeled(), int(self.cfg["max_transcript_chars"]))
        else:
            heard = override
        new = estimate_tokens(heard) + estimate_tokens(self.view.typed()) + image_tokens(self.pending_pixels) + 80
        cached = self._system_tokens + estimate_tokens(self.chat.system_extra) + int(self.chat.context_tokens)
        out = OUTPUT_TOKENS.get(self.cfg["mode"], 200) + THINKING_TOKENS.get(self.cfg["effort"], 0)
        costs = {
            key: estimate_question(p["model"], new_tokens=new, cached_tokens=cached, output_tokens=out, fast=p["fast"])
            for key, p in MODEL_PROFILES.items()
        }
        return costs, new + cached, out

    def _update_meters(self) -> None:
        costs, tokens_in, tokens_out = self.estimate()
        self.view.set_estimate(costs, tokens_in, tokens_out)
        limit = int(self.cfg["max_history_tokens"])
        self.view.set_context_usage(min(1.0, self.chat.context_tokens / limit), int(self.chat.context_tokens), limit)
        self.view.set_budget(float(self.cfg["spent_usd"]), float(self.cfg["budget_usd"]))

    def _add_spent(self, cost: float) -> bool:
        """Keeps the month's spending in the config; True when this answer crossed the budget."""
        month = time.strftime("%Y-%m")
        if self.cfg["spent_month"] != month:
            self.cfg["spent_month"], self.cfg["spent_usd"] = month, 0.0
            self._budget_warned = False
        self.cfg["spent_usd"] = float(self.cfg["spent_usd"]) + cost
        save_config(self.cfg)
        budget = float(self.cfg["budget_usd"])
        if budget > 0 and self.cfg["spent_usd"] >= budget and not self._budget_warned:
            self._budget_warned = True
            return True
        return False

    # --- housekeeping ---

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    def _check_idle(self) -> None:
        """Conversations live only in memory; after a long pause they are wiped from it as well."""
        self._idle_job = self.scheduler.after(IDLE_CHECK_MS, self._check_idle)
        minutes = int(self.cfg["auto_clear_minutes"])
        idle = time.monotonic() - self.last_activity
        has_data = self.chat.history or self.transcript.items or self.messages
        if minutes and idle >= minutes * 60 and has_data and not self._in_flight():
            log.info("chat cleared after %d minutes of inactivity", minutes)
            self.new_chat()
            self.set_status(t("auto_cleared", minutes=minutes))

    def on_hotkey(self, name: str) -> None:
        actions = {
            "answer": self.send,
            "screenshot": self.screenshot_and_ask,
            "area": self.area_and_ask,
            "listen": self.toggle_listen,
            "toggle_window": self.view.toggle_window,
            "summary": lambda: self.summarize(int(self.cfg["summary_minutes"])),
            "voice_start": self.start_voice,
            "voice_stop": self.finish_voice,
            "compact": self.view.toggle_compact,
            "choose_area": self.choose_area,
        }
        if name in actions:
            actions[name]()
