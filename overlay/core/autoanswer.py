"""Decides when to send automatically after a question was heard."""

from __future__ import annotations

import time
from collections.abc import Callable

from overlay.core.scheduler import Scheduler
from overlay.speech.transcript import detect_question

DELAY_STRONG_MS = 400
DELAY_WEAK_MS = 1000
RETRY_MS = 300
MAX_WAIT_SEC = 8.0


class AutoAnswer:
    def __init__(
        self,
        scheduler: Scheduler,
        is_busy: Callable[[], bool],
        is_talking: Callable[[], bool],
        fire: Callable[[], None],
    ):
        self.scheduler = scheduler
        self.is_busy = is_busy
        self.is_talking = is_talking
        self.fire = fire
        self.enabled = False
        self._job = None
        self._since = 0.0

    def heard(self, text: str) -> None:
        """A questioner's utterance (merged with their previous one) was transcribed."""
        if not self.enabled:
            return
        strength = detect_question(text, last=2)
        if strength:
            if not self._job:
                self._since = time.monotonic()
            self._schedule(DELAY_STRONG_MS if strength == "strong" else DELAY_WEAK_MS)
        elif self._job:
            if time.monotonic() - self._since > MAX_WAIT_SEC:
                self.cancel()
            else:
                self._schedule(DELAY_WEAK_MS)

    def answer_finished(self, last_text: str | None) -> None:
        """A new question may have been asked while the model was answering."""
        if self.enabled and not self._job and last_text and detect_question(last_text, last=2):
            self._since = time.monotonic()
            self._schedule(DELAY_STRONG_MS)

    def cancel(self) -> None:
        if self._job:
            self.scheduler.after_cancel(self._job)
            self._job = None

    def _schedule(self, delay_ms: int) -> None:
        self.cancel()
        self._job = self.scheduler.after(delay_ms, self._fire)

    def _fire(self) -> None:
        self._job = None
        if not self.enabled:
            return
        talking = self.is_talking()
        if talking and time.monotonic() - self._since > MAX_WAIT_SEC:
            return
        if talking or self.is_busy():
            self._job = self.scheduler.after(RETRY_MS, self._fire)
            return
        self.fire()
