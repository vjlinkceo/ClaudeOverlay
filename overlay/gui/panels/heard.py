"""What was heard since the last question. Editable: a misheard term is fixed before it is sent."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit

from overlay.gui.theme import ACCENT, MUTED, SPEAKER_COLORS
from overlay.i18n import t
from overlay.speech.transcript import Line

MIN_LINES, MAX_LINES = 3, 8


class HeardPanel(QTextEdit):
    height_hint = Signal(int)

    def __init__(self):
        super().__init__()
        self.setAcceptRichText(False)
        self.setPlaceholderText(t("heard_placeholder"))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.modified = False
        self._updating = False
        self._shown_finals = 0
        self._last_text = ""
        self.textChanged.connect(self._on_text_changed)
        self.document().documentLayout().documentSizeChanged.connect(lambda _s: self.fit_height())
        self.setMinimumHeight(MIN_LINES * self.fontMetrics().lineSpacing() + 12)

    def _on_text_changed(self) -> None:
        if not self._updating:
            self.modified = True

    def override(self) -> str | None:
        """The edited text, or None while the panel only mirrors the transcript."""
        return self.toPlainText() if self.modified else None

    def refresh(self, lines: list[Line], labeled: bool, reset: bool = False) -> None:
        if reset:
            self.modified = False
        finals = [line for line in lines if not line.partial]
        self._updating = True
        try:
            if self.modified:
                self._append_new(finals, labeled)
            else:
                self._rebuild(lines, labeled)
        finally:
            self._updating = False
        self._shown_finals = len(finals)
        self._last_text = finals[-1].text if finals else ""

    def _rebuild(self, lines: list[Line], labeled: bool) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        labels = {"system": t("speaker_system"), "mic": t("speaker_mic")}
        parts = []
        for line in lines:
            who = labels.get(line.source, "") if labeled else ""
            if line.partial:
                who = f"{who} {t('still_speaking')}".strip()
            color = SPEAKER_COLORS.get(line.source, MUTED)
            text = html.escape(line.text)
            if line.partial:
                text = f'<span style="color:{MUTED};font-style:italic">{text}</span>'
            who_html = f' <span style="color:{color};font-weight:600">{html.escape(who)}:</span>' if who else ""
            parts.append(f'<span style="color:{ACCENT};font-size:8pt">{line.time}</span>{who_html} {text}')
        self.setHtml("<br/>".join(parts))
        if at_bottom:
            bar.setValue(bar.maximum())

    def _append_new(self, finals: list[Line], labeled: bool) -> None:
        """While the user edits, new phrases are added at the bottom as plain lines; the last phrase may
        also have grown because the transcript merges close utterances of one speaker."""
        labels = {"system": t("speaker_system"), "mic": t("speaker_mic")}
        added: list[str] = []
        if finals and len(finals) == self._shown_finals and finals[-1].text != self._last_text:
            grown = finals[-1].text
            if grown.startswith(self._last_text):
                added.append(grown[len(self._last_text) :].strip())
        for line in finals[self._shown_finals :]:
            who = labels.get(line.source, "") if labeled else ""
            added.append(f"{line.time} {who}: {line.text}" if who else f"{line.time} {line.text}")
        if not added:
            return
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        text = self.toPlainText()
        prefix = "" if not text or text.endswith("\n") else ("\n" if len(added) > 1 or ": " in added[0] else " ")
        cursor.insertText(prefix + "\n".join(added))
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def fit_height(self) -> None:
        """Asks for 3 to 8 lines, as the text needs; the window decides what it gets."""
        line = self.fontMetrics().lineSpacing()
        self.setMinimumHeight(MIN_LINES * line + 12)
        content = int(self.document().size().height()) + 12
        self.height_hint.emit(max(MIN_LINES * line + 12, min(content, MAX_LINES * line + 12)))
