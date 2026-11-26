"""Explicit push-to-talk microphone capture: release sends, cancellation discards."""

from __future__ import annotations

import contextlib
import ctypes
import threading

import numpy as np

from overlay.i18n import t
from overlay.speech import audio
from overlay.speech.audio import RATE
from overlay.speech.vad import FRAME

MAX_SECONDS = 45
MIN_SAMPLES = RATE // 5


class VoiceRecorder(threading.Thread):
    def __init__(self, device_name: str, on_ready, on_done):
        super().__init__(name="voice-mic", daemon=True)
        self.device_name = device_name
        self.on_ready, self.on_done = on_ready, on_done
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()

    def finish(self) -> None:
        self.stop_event.set()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.stop_event.set()

    def _device(self):
        if not self.device_name:
            return audio.sc.default_microphone()
        chosen = next((d for d in audio.sc.all_microphones() if self.device_name and d.name == self.device_name), None)
        return chosen if chosen is not None else audio.sc.default_microphone()

    def run(self) -> None:
        initialized = False
        with contextlib.suppress(Exception):
            initialized = ctypes.windll.ole32.CoInitializeEx(None, 0) >= 0
        chunks, samples = [], 0
        result, error = None, None
        try:
            if self.stop_event.is_set():
                return
            with self._device().recorder(samplerate=RATE) as recorder:
                self.on_ready()
                while not self.stop_event.is_set() and samples < MAX_SECONDS * RATE:
                    data = recorder.record(numframes=FRAME)
                    if data.size == 0:
                        continue
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    mono = np.asarray(mono, dtype=np.float32).copy()
                    chunks.append(mono)
                    samples += len(mono)
            if samples >= MAX_SECONDS * RATE and not self.stop_event.is_set():
                error = t("voice_limit", seconds=MAX_SECONDS)
            elif samples >= MIN_SAMPLES and not self.cancel_event.is_set():
                result = np.concatenate(chunks)[: MAX_SECONDS * RATE]
                if float(np.max(np.abs(result))) <= 1e-4:
                    result = None
        except Exception as exc:
            error = t("audio_error", source=t("source_mic"), error=exc)
        finally:
            if self.cancel_event.is_set():
                result, error = None, None
            self.on_done(result, error)
            if initialized:
                with contextlib.suppress(Exception):
                    ctypes.windll.ole32.CoUninitialize()
