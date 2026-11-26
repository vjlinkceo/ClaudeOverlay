"""One line that says what the app is doing: a colored dot, the text, the signal level, the session cost,
the context and budget meters, a progress bar while a model downloads, and a way to dismiss an error."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from overlay.gui.theme import ACCENT, BORDER, DIM, ERR_C, MUTED, OK_C, WARN_C

KIND_COLORS = {"info": MUTED, "ok": OK_C, "busy": ACCENT, "error": ERR_C}
PULSE_MS = 650
BARS = 4


class LevelMeter(QWidget):
    """Four bars that follow the microphone or PC audio level."""

    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.setFixedSize(22, 14)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        if abs(level - self.level) >= 0.05 or level == 0.0:
            self.level = level
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        lit = round(self.level * BARS)
        width, gap = 4, 2
        for i in range(BARS):
            height = 4 + i * 3
            painter.setBrush(QColor(OK_C if i < lit else BORDER))
            painter.drawRoundedRect(QRectF(i * (width + gap), self.height() - height, width, height), 1.5, 1.5)
        painter.end()


class MeterBar(QWidget):
    """A thin bar with a tiny caption: how full something is. Turns amber past 75 %, red past 90 %."""

    def __init__(self, caption: str):
        super().__init__()
        self.caption = caption
        self.fraction = 0.0
        self.setFixedSize(58, 16)
        self.setVisible(False)

    def set_fraction(self, fraction: float, tooltip: str) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self.setToolTip(tooltip)
        self.setVisible(True)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(DIM))
        font = painter.font()
        font.setPointSizeF(7.0)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, self.width(), 8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.caption
        )
        painter.setPen(Qt.PenStyle.NoPen)
        track = QRectF(0, 10, self.width(), 5)
        painter.setBrush(QColor(BORDER))
        painter.drawRoundedRect(track, 2.5, 2.5)
        color = ERR_C if self.fraction >= 0.9 else WARN_C if self.fraction >= 0.75 else ACCENT
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(0, 10, max(4.0, self.width() * self.fraction), 5), 2.5, 2.5)
        painter.end()


class StatusStrip(QFrame):
    def __init__(self, context_caption: str = "", budget_caption: str = ""):
        super().__init__()
        self.setProperty("panel", True)
        self.kind = "info"
        self.listening = False
        self._pulse_on = False
        self.on_dismiss = None

        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setProperty("muted", True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meter = LevelMeter()
        self.meter.setVisible(False)
        self.context_meter = MeterBar(context_caption)
        self.budget_meter = MeterBar(budget_caption)
        self.extra = QLabel()
        self.extra.setProperty("tiny", True)
        self.extra.setVisible(False)
        self.dismiss = QPushButton("✕")
        self.dismiss.setProperty("link", True)
        self.dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss.setVisible(False)
        self.dismiss.clicked.connect(self._dismissed)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 5, 6, 5)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.dot)
        row.addWidget(self.text, 1)
        row.addWidget(self.meter)
        row.addWidget(self.context_meter)
        row.addWidget(self.budget_meter)
        row.addWidget(self.extra)
        row.addWidget(self.dismiss)
        outer.addLayout(row)
        outer.addWidget(self.progress)

        self._timer = QTimer(self)
        self._timer.setInterval(PULSE_MS)
        self._timer.timeout.connect(self._pulse)
        self._paint_dot(MUTED)

    def set_status(self, text: str, kind: str = "info") -> None:
        self.kind = kind
        self.text.setText(text)
        self.text.setToolTip(text)
        self.text.setMaximumHeight(self.text.fontMetrics().lineSpacing() * 3)
        self.text.setStyleSheet(f"color: {ERR_C};" if kind == "error" else "")
        self.dismiss.setVisible(kind == "error")
        self._update_pulse()

    def set_extra(self, text: str) -> None:
        self.extra.setText(text)
        self.extra.setVisible(bool(text))

    def set_level(self, level: float) -> None:
        self.meter.set_level(level)

    def set_context(self, fraction: float, tooltip: str) -> None:
        self.context_meter.set_fraction(fraction, tooltip)

    def set_budget(self, fraction: float | None, tooltip: str = "") -> None:
        if fraction is None:
            self.budget_meter.setVisible(False)
        else:
            self.budget_meter.set_fraction(fraction, tooltip)

    def set_progress(self, fraction: float | None) -> None:
        self.progress.setVisible(fraction is not None)
        if fraction is not None:
            self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def set_listening(self, on: bool) -> None:
        self.listening = on
        self.meter.setVisible(on)
        if not on:
            self.meter.set_level(0.0)
        self._update_pulse()

    def _update_pulse(self) -> None:
        if self.listening and self.kind in ("ok", "info"):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self._paint_dot(KIND_COLORS.get(self.kind, MUTED))

    def _pulse(self) -> None:
        self._pulse_on = not self._pulse_on
        self._paint_dot(OK_C if self._pulse_on else BORDER)

    def _paint_dot(self, color: str) -> None:
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def _dismissed(self) -> None:
        if self.on_dismiss:
            self.on_dismiss()
