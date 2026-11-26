"""Bobo on screen: a label that plays the frames of the current state and reacts to the mouse."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from overlay.assets import bobo

CALM_STATES = ("sleep", "idle")


@lru_cache(maxsize=128)
def _pixmap(key: tuple[str, ...], scale: int, dpr: float) -> QPixmap:
    image = bobo.render(list(key), scale)
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(qimage)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def pixmap_for(rows: bobo.Rows, logical_px: int, dpr: float) -> QPixmap:
    """An integer scale, so pixels stay square; the ratio makes it exactly `logical_px` wide."""
    scale = max(1, round(logical_px * dpr / len(rows[0])))
    return _pixmap(tuple(rows), scale, scale * len(rows[0]) / logical_px)


class BoboWidget(QLabel):
    """`set_state` is the long-lived state from the app; `react` plays a short one on top of it,
    and hovering a calm Bobo makes him curious."""

    clicked = Signal()
    right_clicked = Signal()

    def __init__(self, logical_px: int = 32, state: str = "sleep", interactive: bool = False, animated: bool = True):
        super().__init__()
        self.logical_px = logical_px
        self.interactive = interactive
        self.animated = animated
        self.state = ""
        self._reaction: str | None = None
        self._hover = False
        self._shown = ""
        self._frames: list[bobo.Frame] = []
        self._index = 0
        self.setFixedSize(logical_px, logical_px)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self._reaction_timer = QTimer(self)
        self._reaction_timer.setSingleShot(True)
        self._reaction_timer.timeout.connect(self._end_reaction)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        if not self.animated and self.state:
            return  # a still logo keeps its first frame
        self.state = state
        self._refresh()

    def react(self, state: str, ms: int) -> None:
        """Play `state` for `ms`, then return to whatever the app says."""
        self._reaction = state
        self._reaction_timer.start(ms)
        self._refresh()

    def _end_reaction(self) -> None:
        self._reaction = None
        self._refresh()

    def _effective(self) -> str:
        if self._reaction:
            return self._reaction
        if self._hover and self.state in CALM_STATES:
            return "curious"
        return self.state

    def _refresh(self) -> None:
        state = self._effective()
        if state == self._shown:
            return
        self._shown = state
        self._frames = bobo.frames(state)
        self._index = 0
        self._timer.stop()
        self._show()

    def _show(self) -> None:
        rows, ms = self._frames[self._index]
        self.setPixmap(pixmap_for(rows, self.logical_px, self.devicePixelRatioF()))
        if len(self._frames) > 1 and self.animated:
            self._timer.start(ms)

    def _next(self) -> None:
        self._index = (self._index + 1) % len(self._frames)
        self._show()

    def enterEvent(self, event):
        if self.interactive:
            self._hover = True
            self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._refresh()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if not self.interactive:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
