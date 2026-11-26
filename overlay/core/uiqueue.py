"""Hands callbacks from worker threads to the UI thread, which may be the only one touching widgets."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from overlay.core.scheduler import Scheduler

log = logging.getLogger(__name__)

POLL_MS = 20


class UiQueue:
    """Posted callbacks run on the UI thread at the next poll. A callback that fails is reported and
    skipped: if it stopped the polling, no recording, Whisper, AI or hotkey thread could reach the
    window again while the window itself kept looking alive."""

    def __init__(
        self,
        scheduler: Scheduler,
        on_tick: Callable[[], None],
        on_error: Callable[[Exception], None],
        poll_ms: int = POLL_MS,
    ):
        self.scheduler = scheduler
        self.on_tick = on_tick
        self.on_error = on_error
        self.poll_ms = poll_ms
        self._queue: queue.Queue = queue.Queue()
        self._job = None
        self._running = False
        self._wake = getattr(scheduler, "wake", None)
        self._wake_lock = threading.Lock()
        self._wake_pending = False

    def post(self, fn: Callable, *args) -> None:
        """Thread-safe."""
        self._queue.put((fn, args))
        if self._running and self._wake is not None:
            with self._wake_lock:
                if self._wake_pending:
                    return
                self._wake_pending = True
            self._wake(self._woken)

    def _woken(self) -> None:
        with self._wake_lock:
            self._wake_pending = False
        if self._running:
            self._drain()

    def start(self) -> None:
        self._running = True
        self._job = self.scheduler.after(self.poll_ms, self._poll)

    def stop(self) -> None:
        self._running = False
        if self._job is not None:
            self.scheduler.after_cancel(self._job)
            self._job = None

    def _poll(self) -> None:
        try:
            self._drain()
        finally:
            if self._running:
                self._job = self.scheduler.after(self.poll_ms, self._poll)

    def _drain(self) -> None:
        try:
            while True:
                fn, args = self._queue.get_nowait()
                self._run(fn, *args)
        except queue.Empty:
            pass
        self._run(self.on_tick)

    def _run(self, fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            log.exception("UI callback failed: %s", getattr(fn, "__name__", fn))
            self.on_error(e)
