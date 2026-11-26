"""Audio capture (PC audio or microphone) split into phrases by the Silero VAD.

To show text right after a phrase ends, transcription starts early:
- a speculative partial is sent as soon as a pause begins; if the pause turns into the end of the
  phrase, the final result is reused without transcribing again;
- live partials are sent about once a second during long speech.
"""

from __future__ import annotations

import collections
import contextlib
import ctypes
import logging
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Literal

import numpy as np

from overlay.i18n import t
from overlay.speech.vad import FRAME, StreamingVAD

log = logging.getLogger(__name__)

sc = None
_sc_failed = False


def ensure_loaded() -> bool:
    """Import soundcard on first use, on the main thread: that initializes COM (MTA), which the
    recording threads rely on. The import takes a few hundred ms, so it is not paid at startup."""
    global sc, _sc_failed
    if sc is None and not _sc_failed:
        try:
            import soundcard

            # soundcard sets its own 'always' warning filter on import, so ours must come after it.
            warnings.filterwarnings("ignore", message="data discontinuity in recording")
            sc = soundcard
        except Exception:
            log.exception("soundcard import failed")
            _sc_failed = True
    return sc is not None


RATE = 16000
FRAME_SEC = FRAME / RATE
LEVEL_EVERY = 3


def _find(devices, name: str):
    return next((d for d in devices if name and d.name == name), None)


def list_devices() -> tuple[list[str], list[str]]:
    """(microphone names, output device names) for the settings window."""
    if not ensure_loaded():
        return [], []
    try:
        return [d.name for d in sc.all_microphones()], [d.name for d in sc.all_speakers()]
    except Exception:
        log.exception("device listing failed")
        return [], []


def _frames(seconds: float) -> int:
    return max(1, round(seconds / FRAME_SEC))


@dataclass
class SpeechEvent:
    kind: Literal["start", "partial", "final", "level"]
    source: Literal["system", "mic"]
    seg_id: int
    audio: np.ndarray | None = None
    t_start: float = 0.0
    t_end: float = 0.0
    speculative: bool = False
    level: float = 0.0


class Listener(threading.Thread):
    PREROLL = _frames(0.32)
    TAIL = _frames(0.19)
    # Must be >= TAIL: the speculative partial then carries exactly the audio the final would.
    SPECULATE = _frames(0.22)
    PARTIAL_EVERY = _frames(1.2)
    MIN_PARTIAL = _frames(1.5)
    MAX_SEGMENT = _frames(15.0)
    MIN_VOICED = _frames(0.25)
    DEVICE_CHECK_SEC = 2.0
    REOPEN_DELAY_SEC = 0.5
    REOPEN_TIMEOUT_SEC = 10.0
    _seg_counter = 0
    _seg_lock = threading.Lock()

    def __init__(self, source: str, threshold: float, end_silence: float, on_event, on_error, device_name: str = ""):
        super().__init__(name=f"listener-{source}", daemon=True)
        self.source = source
        self.device_name = device_name
        self._level_count = 0
        self.threshold = threshold
        self.neg_threshold = max(0.05, threshold - 0.15)
        self.end_frames = _frames(end_silence)
        self.on_event = on_event
        self.on_error = on_error
        self.stop_event = threading.Event()
        self.fed_at: float | None = None
        self._reset_segment()

    @classmethod
    def _next_id(cls) -> int:
        with cls._seg_lock:
            cls._seg_counter += 1
            return cls._seg_counter

    def _chosen(self):
        """The device picked in the settings, if it is present right now."""
        if not self.device_name:
            return None
        devices = sc.all_microphones() if self.source == "mic" else sc.all_speakers()
        return _find(devices, self.device_name)

    def _default_id(self) -> str:
        chosen = self._chosen()
        if chosen is not None:
            return str(chosen.id)
        device = sc.default_microphone() if self.source == "mic" else sc.default_speaker()
        return str(device.id)

    def _open_device(self):
        chosen = self._chosen()
        if self.source == "mic":
            mic = chosen if chosen is not None else sc.default_microphone()
            return mic, str(mic.id)
        speaker = chosen if chosen is not None else sc.default_speaker()
        return sc.get_microphone(id=str(speaker.id), include_loopback=True), str(speaker.id)

    def run(self):
        with contextlib.suppress(Exception):
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            self.vad = StreamingVAD()
            while not self.stop_event.is_set():
                try:
                    device, device_id = self._open_device()
                    log.info("%s: recording from %s", self.source, getattr(device, "name", device_id))
                    self._capture(device, device_id)
                except Exception as e:
                    # Unplugged headphones or a Bluetooth profile switch break the device mid-recording,
                    # and Windows needs a moment to pick a new default one. A device that never worked fails at once.
                    if self.fed_at is None or time.monotonic() - self.fed_at > self.REOPEN_TIMEOUT_SEC:
                        raise
                    log.warning("%s: audio device failed (%s), reopening", self.source, e)
                    self.stop_event.wait(self.REOPEN_DELAY_SEC)
        except Exception as e:
            log.exception("listener failed")
            self.on_error(t("audio_error", source=t(f"source_{self.source}"), error=e))

    def _capture(self, device, device_id: str) -> None:
        """Record until asked to stop or until the default device changes."""
        self.vad.reset()
        preroll: collections.deque = collections.deque(maxlen=self.PREROLL)
        next_check = time.monotonic() + self.DEVICE_CHECK_SEC
        try:
            with device.recorder(samplerate=RATE) as rec:
                while not self.stop_event.is_set():
                    data = rec.record(numframes=FRAME)
                    if data.size == 0:
                        continue
                    mono = (data.mean(axis=1) if data.ndim > 1 else data).astype(np.float32, copy=False)
                    self._feed(mono, preroll)
                    self.fed_at = now = time.monotonic()
                    if now >= next_check:
                        next_check = now + self.DEVICE_CHECK_SEC
                        try:
                            if self._default_id() != device_id:
                                log.info("%s: default device changed, reopening", self.source)
                                break
                        except Exception:
                            log.exception("default device check failed")
        finally:
            # The phrase cut by a lost device is still transcribed.
            self._finish_segment()

    def _reset_segment(self):
        self.in_speech = False
        self.frames: list[np.ndarray] = []
        self.probs: list[float] = []
        self.voiced = self.silent = 0
        self.last_voiced = -1
        self.speculated = False
        self.last_partial_len = 0
        self.seg_id = 0
        self.t_start = 0.0

    def _start_segment(self, frames: list[np.ndarray], probs: list[float], now: float):
        self.in_speech = True
        self.seg_id = self._next_id()
        self.frames, self.probs = frames, probs
        self.t_start = now - len(frames) * FRAME_SEC
        self.voiced = sum(p >= self.threshold for p in probs)
        speech = [i for i, p in enumerate(probs) if p >= self.neg_threshold]
        self.last_voiced = speech[-1] if speech else 0
        self.silent = len(probs) - 1 - self.last_voiced
        self.speculated = False
        self.last_partial_len = 0
        self.on_event(SpeechEvent("start", self.source, self.seg_id, t_start=self.t_start))

    def _speech_audio(self) -> np.ndarray:
        end = min(len(self.frames), self.last_voiced + 1 + self.TAIL)
        return np.concatenate(self.frames[:end])

    def _t_voiced_end(self) -> float:
        return self.t_start + (self.last_voiced + 1) * FRAME_SEC

    def _feed(self, frame: np.ndarray, preroll: collections.deque) -> None:
        now = time.time()
        prob = self.vad(frame) if float(np.abs(frame).max()) > 1e-4 else 0.0
        self._level_count += 1
        if self._level_count >= LEVEL_EVERY:
            self._level_count = 0
            db = 20 * np.log10(max(float(np.sqrt(np.mean(frame * frame))), 1e-5))
            self.on_event(SpeechEvent("level", self.source, 0, level=min(1.0, max(0.0, (db + 50) / 50))))

        if not self.in_speech:
            preroll.append((frame, prob))
            if prob >= self.threshold:
                self._start_segment([f for f, _ in preroll], [p for _, p in preroll], now)
                preroll.clear()
            return

        self.frames.append(frame)
        self.probs.append(prob)
        if prob >= self.neg_threshold:
            if prob >= self.threshold:
                self.voiced += 1
            self.last_voiced = len(self.frames) - 1
            self.silent = 0
            self.speculated = False
        else:
            self.silent += 1

        n = len(self.frames)
        if self.silent >= self.end_frames:
            self._finish_segment()
        elif self.silent >= self.SPECULATE and not self.speculated and self.voiced >= self.MIN_VOICED:
            self.speculated = True
            self.on_event(
                SpeechEvent(
                    "partial",
                    self.source,
                    self.seg_id,
                    self._speech_audio(),
                    self.t_start,
                    self._t_voiced_end(),
                    speculative=True,
                )
            )
        elif self.silent == 0 and n >= self.MIN_PARTIAL and n - self.last_partial_len >= self.PARTIAL_EVERY:
            self.last_partial_len = n
            self.on_event(
                SpeechEvent("partial", self.source, self.seg_id, np.concatenate(self.frames), self.t_start, now)
            )
        if self.in_speech and n >= self.MAX_SEGMENT:
            self._split_long_segment(now)

    def _finish_segment(self) -> None:
        if self.in_speech and self.voiced >= self.MIN_VOICED:
            self.on_event(
                SpeechEvent("final", self.source, self.seg_id, self._speech_audio(), self.t_start, self._t_voiced_end())
            )
        elif self.in_speech:
            self.on_event(SpeechEvent("final", self.source, self.seg_id, None, self.t_start, self.t_start))
        self._reset_segment()

    def _split_long_segment(self, now: float) -> None:
        """Emit the head of a long monologue, cutting at the quietest point of the last seconds."""
        n = len(self.frames)
        lo, hi = max(1, n - _frames(4.0)), n - _frames(0.5)
        probs = np.convolve(np.asarray(self.probs, dtype=np.float32), np.ones(3) / 3, mode="same")
        cut = lo + int(np.argmin(probs[lo:hi]))
        head = np.concatenate(self.frames[:cut])
        self.on_event(
            SpeechEvent("final", self.source, self.seg_id, head, self.t_start, self.t_start + cut * FRAME_SEC)
        )
        self._start_segment(self.frames[cut:], self.probs[cut:], now)
