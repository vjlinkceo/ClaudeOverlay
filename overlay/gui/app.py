"""QApplication setup: the theme, the scheduler, and hiding every window of the process from capture."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from overlay.ai.keys import load_api_key, save_api_key
from overlay.config import load_config, provider_for_model, save_config
from overlay.gui.theme import stylesheet
from overlay.i18n import set_language, t
from overlay.win.winapi import set_capture_excluded

log = logging.getLogger(__name__)


class _UiWake(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.requested.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, callback):
        callback()


class QtScheduler:
    """`Scheduler` on single-shot QTimers that can be cancelled. The UI queue polls every 20 ms,
    so timers are reused from a small pool instead of being created and destroyed each time."""

    POOL = 8

    def __init__(self):
        self._pending: dict[int, tuple[QTimer, Callable[[], None]]] = {}
        self._pool: list[QTimer] = []
        self._next = 0
        self._waker = _UiWake()

    def wake(self, callback: Callable[[], None]) -> None:
        """Thread-safe: deliver data without waiting for the next timer poll."""
        self._waker.requested.emit(callback)

    def _make(self) -> QTimer:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(lambda t=timer: self._fire(t))
        return timer

    def after(self, ms: int, fn: Callable[[], None]) -> int:
        self._next += 1
        handle = self._next
        timer = self._pool.pop() if self._pool else self._make()
        timer.setProperty("handle", handle)
        self._pending[handle] = (timer, fn)
        timer.start(ms)
        return handle

    def _recycle(self, timer: QTimer) -> None:
        if len(self._pool) < self.POOL:
            self._pool.append(timer)

    def _fire(self, timer: QTimer) -> None:
        entry = self._pending.pop(timer.property("handle"), None)
        self._recycle(timer)
        if entry is not None:
            entry[1]()

    def after_cancel(self, handle: int) -> None:
        entry = self._pending.pop(handle, None)
        if entry is not None:
            entry[0].stop()
            self._recycle(entry[0])


def hide_from_capture(widget: QWidget, hidden: bool = True) -> bool:
    return set_capture_excluded(int(widget.winId()), hidden)


class CaptureGuard(QObject):
    """Menus, combo lists, tooltips and dialogs are separate windows: each one is excluded from screen
    capture when it is about to be shown, before its first frame."""

    def __init__(self):
        super().__init__()
        self.enabled = False

    def eventFilter(self, obj, event):
        if self.enabled and event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            hide_from_capture(obj)
        return False


def run() -> int:
    from overlay.gui.dialogs.first_run import FirstRunDialog
    from overlay.gui.window import MainWindow

    cfg = load_config()
    set_language(cfg["ui_language"])
    app = QApplication(sys.argv)
    app.setApplicationName("ChatGPT Overlay")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(stylesheet(float(cfg["font_scale"])))
    guard = CaptureGuard()
    guard.enabled = bool(cfg["hide_from_capture"])
    app.installEventFilter(guard)

    if load_api_key(provider=provider_for_model(cfg["model"])) is None:
        dialog = FirstRunDialog(cfg)
        dialog.exec()
        if dialog.key:
            try:
                save_api_key(dialog.key, provider=dialog.provider)
                save_config(cfg)
            except OSError as exc:
                QMessageBox.critical(None, t("fr_key"), t("key_save_failed", message=str(exc)))

    window = MainWindow(cfg, guard)
    window.show()
    return app.exec()
