"""Local Whisper (faster-whisper): free, and audio never leaves the computer.

NVIDIA GPU: float16 with `whisper_model`; otherwise CPU int8 with `whisper_model_cpu`.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from overlay.i18n import t
from overlay.speech.audio import RATE, SpeechEvent
from overlay.speech.transcript import Utterance, split_sentences

log = logging.getLogger(__name__)

SPEECH_LANGS = ("ru", "uk", "en")
# Approximate download sizes, for the progress bar only.
MODEL_BYTES = {
    "tiny": 75e6,
    "base": 145e6,
    "small": 486e6,
    "medium": 1.53e9,
    "large-v3": 3.1e9,
    "large-v3-turbo": 1.62e9,
}
# Matched word by word as prefixes, so "субтитр" also covers "субтитров".
HALLUCINATIONS = (
    "продолжение следует",
    "субтитр",
    "спасибо за просмотр",
    "подписывайтесь на канал",
    "подписывайтесь",
    "подпишись на канал",
    "ставьте лайк",
    "редактор субтитров",
    "корректор а.",
    "dimatorzok",
    "amara.org",
    "subtitles by the amara.org community",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "дякую за перегляд",
    "підписуйтесь на канал",
    "підписуйтесь",
    "субтитри",
)
HALLUCINATION_EXTRA_WORDS = 3
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MARKERS = tuple(tuple(_WORD_RE.findall(h)) for h in HALLUCINATIONS)


def _is_hallucination(sentence: str) -> bool:
    """Whisper invents these phrases on silence and noise. A question, or a sentence where they are
    only a small part ("Как бы вы спроектировали сервис субтитров?"), is real speech."""
    if sentence.rstrip("\"'»)").endswith("?"):
        return False
    words = _WORD_RE.findall(sentence.lower())
    covered = [False] * len(words)
    for marker in _MARKERS:
        for i in range(len(words) - len(marker) + 1):
            if all(w.startswith(m) for w, m in zip(words[i : i + len(marker)], marker, strict=True)):
                covered[i : i + len(marker)] = [True] * len(marker)
    hits = covered.count(True)
    extra = len(words) - hits
    return hits > 0 and (extra <= HALLUCINATION_EXTRA_WORDS or hits >= extra)


def clean_text(text: str, *prompts: str) -> str:
    """Drop typical Whisper hallucinations on silence and noise, including echoes of the prompt.
    Only the invented sentences go: the rest of the phrase is kept."""
    sentences = split_sentences(" ".join(text.split()))
    kept = [s for s in sentences if not _is_hallucination(s)]
    text = " ".join(kept)
    # A few words left next to an invented sentence are usually its tail, such as a surname.
    if len(kept) < len(sentences) and len(_WORD_RE.findall(text)) <= HALLUCINATION_EXTRA_WORDS and "?" not in text:
        return ""
    lowered = text.lower()
    if len(text) < 2:
        return ""
    words = _WORD_RE.findall(lowered)
    if not words:
        return ""
    if len(words) >= 8 and len(set(words)) / len(words) < 0.3:
        return ""
    stripped = lowered.strip(" .,")
    if len(stripped) > 10 and any(stripped in p.lower() and len(stripped) * 2 >= len(p) for p in prompts if p):
        return ""
    return text


def download_progress(model_name: str) -> float | None:
    """How much of the model is in the Hugging Face cache so far, 0..1, or None if unknown."""
    try:
        from faster_whisper import utils
        from huggingface_hub import constants

        repo = utils._MODELS.get(model_name, model_name)
        folder = os.path.join(constants.HF_HUB_CACHE, "models--" + repo.replace("/", "--"))
        if not os.path.isdir(folder):
            return 0.0
        total = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(folder) for f in files)
        expected = MODEL_BYTES.get(model_name)
        return min(1.0, total / expected) if expected else None
    except Exception:
        return None


def glossary_terms(glossary: str) -> str:
    """'Kubernetes, PostgreSQL\nDjango' -> 'Kubernetes, PostgreSQL, Django'."""
    return ", ".join(term.strip() for term in re.split(r"[,;\n]+", glossary) if term.strip())


@dataclass
class VoiceTask:
    samples: np.ndarray
    on_done: Callable
    cancel_event: threading.Event


class Transcriber(threading.Thread):
    DETECT_EVERY_CPU = 3

    def __init__(self, cfg: dict, on_partial, on_final, on_status):
        super().__init__(name="stt", daemon=True)
        self.cfg = cfg
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_status = on_status
        self.model = None
        self.device: str | None = None
        self.downloading_model: str | None = None
        self._cond = threading.Condition()
        self._finals: collections.deque[SpeechEvent] = collections.deque()
        self._partials: dict[str, SpeechEvent] = {}
        self._voice_jobs: collections.deque[VoiceTask] = collections.deque()
        self._cache: collections.OrderedDict[int, tuple[int, str, str | None]] = collections.OrderedDict()
        self._finalized: collections.deque[int] = collections.deque(maxlen=64)
        self._running = True
        self.reset_languages()

    def submit(self, ev: SpeechEvent) -> None:
        with self._cond:
            if ev.kind == "final":
                pending = self._partials.get(ev.source)
                if pending and pending.seg_id == ev.seg_id:
                    del self._partials[ev.source]
                self._finals.append(ev)
            elif ev.kind == "partial":
                self._partials[ev.source] = ev
            else:
                return
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify()

    def submit_voice(self, samples: np.ndarray, on_done: Callable, cancel_event: threading.Event) -> None:
        with self._cond:
            self._voice_jobs.append(VoiceTask(samples, on_done, cancel_event))
            self._cond.notify()

    def reset_languages(self) -> None:
        self.lang_scores: dict[str, dict[str, float]] = {}
        self.lang_count: dict[str, int] = {}
        self.last_text: dict[str, str] = {}

    def forget_text(self) -> None:
        """Drop remembered speech (prompt context and cached results) when the chat is cleared."""
        self.last_text = {}
        self._cache.clear()

    def _attempts(self) -> list[tuple[str, str, str]]:
        want = self.cfg.get("whisper_device", "auto")
        attempts = []
        if want in ("auto", "cuda"):
            try:
                import ctranslate2

                has_gpu = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                has_gpu = False
            if has_gpu or want == "cuda":
                attempts.append(("cuda", "float16", self.cfg["whisper_model"]))
        attempts.append(("cpu", "int8", self.cfg["whisper_model_cpu"]))
        return attempts

    def _load(self) -> bool:
        from faster_whisper import WhisperModel

        for device, compute_type, name in self._attempts():
            params = dict(device=device, compute_type=compute_type)
            if device == "cpu":
                params["cpu_threads"] = max(4, (os.cpu_count() or 8) // 2)
            try:
                try:
                    model = WhisperModel(name, local_files_only=True, **params)
                except Exception:
                    size = "~1.6 GB" if "large" in name else "~500 MB"
                    self.downloading_model = name
                    self.on_status(t("stt_downloading", name=name, size=size), "downloading")
                    try:
                        model = WhisperModel(name, **params)
                    finally:
                        self.downloading_model = None
                # The first run checks that the CUDA libraries load and warms up the GPU.
                started = time.perf_counter()
                list(
                    model.transcribe(
                        np.zeros(RATE, dtype=np.float32),
                        language="ru",
                        beam_size=1,
                        vad_filter=False,
                        without_timestamps=True,
                    )[0]
                )
                log.info(
                    "whisper %s on %s/%s ready, warmup %.2fs", name, device, compute_type, time.perf_counter() - started
                )
            except Exception as e:
                log.exception("whisper load failed on %s", device)
                if device == "cuda":
                    self.on_status(t("stt_gpu_fallback", error=e), "busy")
                    continue
                self.on_status(t("stt_failed", error=e), "error")
                return False
            self.model, self.device = model, device
            return True
        return False

    def run(self):
        self.on_status(t("loading_stt"), "busy")
        try:
            ready = self._load()
        except Exception as exc:
            log.exception("speech model initialization failed")
            self.on_status(t("stt_failed", error=exc), "error")
            return
        if not ready:
            return
        self.on_status(None, "ready")
        while True:
            with self._cond:
                while self._running and not self._voice_jobs and not self._finals and not self._partials:
                    self._cond.wait()
                if not self._running:
                    return
                voice = self._voice_jobs.popleft() if self._voice_jobs else None
                if voice is not None:
                    ev, partial = None, False
                elif self._finals:
                    ev, partial = self._finals.popleft(), False
                else:
                    ev, partial = self._partials.pop(next(iter(self._partials))), True
            try:
                if voice is not None:
                    self._handle_voice(voice)
                elif partial:
                    self._handle_partial(ev)
                else:
                    self._handle_final(ev)
            except Exception:
                log.exception("transcribe failed")

    def _handle_voice(self, task: VoiceTask) -> None:
        if task.cancel_event.is_set():
            return
        try:
            text, _ = self._transcribe(task.samples, "voice", final=True)
            if not task.cancel_event.is_set():
                task.on_done(text, None)
        except Exception as exc:
            log.exception("voice transcription failed")
            if not task.cancel_event.is_set():
                task.on_done("", t("stt_failed", error=exc))

    def _handle_partial(self, ev: SpeechEvent) -> None:
        if ev.seg_id in self._finalized:
            return
        if self.device == "cpu" and not ev.speculative:
            return
        # A speculative result becomes the final one, so it gets final-quality settings.
        text, lang = self._transcribe(ev.audio, ev.source, final=ev.speculative)
        self._cache[ev.seg_id] = (len(ev.audio), text, lang)
        while len(self._cache) > 16:
            self._cache.popitem(last=False)
        if ev.seg_id not in self._finalized:
            self.on_partial(ev.source, text)

    def _handle_final(self, ev: SpeechEvent) -> None:
        self._finalized.append(ev.seg_id)
        if ev.audio is None or len(ev.audio) == 0:
            self.on_final(ev.source, None)
            return
        cached = self._cache.pop(ev.seg_id, None)
        reused = bool(cached and cached[0] == len(ev.audio))
        text, lang = (cached[1], cached[2]) if reused else self._transcribe(ev.audio, ev.source, final=True)
        log.info(
            "stt %s: %.1fs audio, %.2fs after speech end%s, lang=%s",
            ev.source,
            len(ev.audio) / RATE,
            time.time() - ev.t_end,
            " (speculative)" if reused else "",
            lang,
        )
        if not text:
            self.on_final(ev.source, None)
            return
        self.last_text[ev.source] = text
        self.on_final(ev.source, Utterance(ev.source, text, ev.t_start, ev.t_end))

    def _language(self, audio: np.ndarray, source: str, decide: bool) -> str | None:
        fixed = self.cfg.get("speech_language", "auto")
        if fixed in SPEECH_LANGS:
            return fixed
        scores = self.lang_scores.setdefault(source, {})
        known = max(scores, key=scores.get) if scores else None
        if known and (not decide or len(audio) < RATE):
            return known
        if known and self.device == "cpu":
            self.lang_count[source] = self.lang_count.get(source, 0) + 1
            if self.lang_count[source] % self.DETECT_EVERY_CPU:
                return known

        # Restricted to ru/uk/en: otherwise Whisper confuses them with Lithuanian and the like.
        _, _, all_probs = self.model.detect_language(audio=audio)
        probs = {lang: p for lang, p in all_probs if lang in SPEECH_LANGS}
        total = sum(probs.values()) or 1.0
        for lang in SPEECH_LANGS:
            scores[lang] = 0.3 * scores.get(lang, 1 / 3) + 0.7 * probs.get(lang, 0.0) / total
        choice = max(scores, key=scores.get)
        if choice != known:
            log.info("speech language for %s: %s -> %s", source, known, choice)
        return choice

    def _prompts(self, source: str) -> tuple[str, str]:
        """(initial prompt, hotwords). They have separate token budgets in faster-whisper."""
        topic = (self.cfg.get("context") or "").strip()[:120]
        previous = self.last_text.get(source, "")[-160:]
        hotwords = glossary_terms(self.cfg.get("glossary") or "")[:400]
        return " ".join(p for p in (topic, previous) if p), hotwords

    def _transcribe(self, audio: np.ndarray, source: str, final: bool) -> tuple[str, str | None]:
        language = self._language(audio, source, final)
        prompt, hotwords = self._prompts(source)
        segments, info = self.model.transcribe(
            audio,
            language=language,
            beam_size=1,
            temperature=[0.0, 0.4] if final else 0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=prompt or None,
            hotwords=hotwords or None,
        )
        parts = [
            seg.text.strip()
            for seg in segments
            if not (seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8) and seg.compression_ratio <= 2.6
        ]
        return clean_text(" ".join(p for p in parts if p), prompt, hotwords), language or info.language
