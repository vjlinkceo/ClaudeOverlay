"""Drag-to-select a screen region on the monitor under the cursor."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from overlay.gui.theme import ACCENT
from overlay.win.winapi import set_capture_excluded


def screen_under_cursor() -> QScreen:
    return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()


class RegionSelector(QWidget):
    MIN_SIZE = 8

    def __init__(
        self, screen: QScreen, monitor: dict, hide_from_capture: bool, hint: str, on_done: Callable[[dict | None], None]
    ):
        super().__init__(
            None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.monitor = monitor
        self.hint = hint
        self.on_done = on_done
        self.dpr = screen.devicePixelRatio()
        self.start: QPoint | None = None
        self.current: QPoint | None = None
        self.done = False
        self.setWindowOpacity(0.35)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        if hide_from_capture:
            set_capture_excluded(int(self.winId()), True)
        self.show()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 14)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRect(0, 30, self.width(), 40), Qt.AlignmentFlag.AlignHCenter, self.hint)
        if self.start and self.current:
            painter.setPen(QPen(QColor(ACCENT), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRect(self.start, self.current).normalized())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._finish(None)
            return
        self.start = self.current = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event):
        if self.start:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.start:
            return
        rect = QRect(self.start, event.position().toPoint()).normalized()
        if rect.width() < self.MIN_SIZE or rect.height() < self.MIN_SIZE:
            self._finish(None)
            return
        self._finish(
            {
                "left": self.monitor["left"] + round(rect.left() * self.dpr),
                "top": self.monitor["top"] + round(rect.top() * self.dpr),
                "width": max(1, round(rect.width() * self.dpr)),
                "height": max(1, round(rect.height() * self.dpr)),
            }
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)

    def _finish(self, region: dict | None) -> None:
        if self.done:
            return
        self.done = True
        self.hide()
        self.deleteLater()
        self.on_done(region)
