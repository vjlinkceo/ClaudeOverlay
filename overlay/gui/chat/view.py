"""The conversation: user cards, streamed answers and the actions under each answer."""

from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QTextBrowser

from overlay.ai.prompts import FOLLOWUPS
from overlay.gui.chat import mathimg
from overlay.gui.chat.render import MathSpan, Renderer, fallback_html, img_tag
from overlay.gui.theme import ACCENT, BASE_PT, CARD, COLORS, DIM, ERR_C, FG, FONT_FAMILY, MUTED, USER_C
from overlay.i18n import t

RENDER_EVERY_MS = 40
RENDER_LONG_MS = 90
LONG_ANSWER_CHARS = 4000
ACTION_SCHEME = "act:"
CARET_HTML = f'<span style="color:{ACCENT}">▍</span>'


class ChatView(QTextBrowser):
    action = Signal(str, str)  # kind ("copied" | "followup"), argument
    zoom = Signal(int)

    def __init__(self, scale: float = 1.0):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.document().setDocumentMargin(10)
        self.document().setDefaultStyleSheet(f"a {{ color: {ACCENT}; text-decoration: none; }}")
        self.anchorClicked.connect(self._on_anchor)
        self.verticalScrollBar().sliderPressed.connect(self._stop_following)
        self.verticalScrollBar().actionTriggered.connect(lambda _a: self._stop_following())
        self.renderer = Renderer(COLORS)
        self.scale = scale
        self._raw: list[str] = []
        self._answer_pos: int | None = None
        self._follow = False
        self._pending: str | None = None
        self._render_paused = False
        self._math_cache: dict[str, bool] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RENDER_EVERY_MS)
        self._timer.timeout.connect(self._flush)
        self.set_scale(scale)

    def set_scale(self, scale: float) -> None:
        self.scale = scale
        font = QFont(FONT_FAMILY)
        font.setPointSizeF(BASE_PT * scale)
        self.document().setDefaultFont(font)
        self._math_cache.clear()

    # --- content ---

    def _cursor_at_end(self, new_block: bool = True) -> QTextCursor:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if new_block and not self.document().isEmpty():
            cursor.insertBlock()
        return cursor

    def show_user(self, shown: str) -> None:
        cursor = self._cursor_at_end()
        stamp = time.strftime("%H:%M")
        cursor.insertHtml(
            f'<table width="100%" cellpadding="6" cellspacing="0" bgcolor="{CARD}"><tr><td>'
            f'<span style="color:{USER_C};font-weight:600">{t("you_label")}</span> '
            f'<span style="color:{DIM};font-size:8pt">{stamp}</span><br/>'
            f"{html.escape(shown)}</td></tr></table>"
        )

    def begin_answer(self) -> None:
        self._raw.append("")
        cursor = self._cursor_at_end()
        cursor.insertHtml(f'<span style="color:{ACCENT};font-weight:600">{t("assistant_label")}</span>')
        cursor.insertBlock()
        self._answer_pos = cursor.position()
        self._follow = True
        self._pending = None

    def update_answer(self, markdown: str) -> None:
        if not self._raw:
            return
        first = not self._raw[-1]
        self._raw[-1] = markdown
        self._pending = markdown
        if self._render_paused:
            return
        if first:
            self._flush()
            return
        self._timer.setInterval(RENDER_EVERY_MS if len(markdown) < LONG_ANSWER_CHARS else RENDER_LONG_MS)
        if not self._timer.isActive():
            self._timer.start()

    def set_render_paused(self, paused: bool) -> None:
        self._render_paused = paused
        if paused:
            self._timer.stop()
        elif self._pending is not None:
            self._flush()

    def _flush(self, final: bool = False) -> None:
        if self._pending is None or self._answer_pos is None:
            return
        markdown, self._pending = self._pending, None
        rendered = self.renderer.render(markdown)
        body = self._resolve_math(rendered.html, rendered.math)
        if not final:
            body += CARET_HTML
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._answer_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(body)
        if self._follow:
            self._scroll_to_answer()

    def finish_answer(self, meta_text: str | None, error: str | None, save: bool = False) -> None:
        self._timer.stop()
        if self._raw and self._answer_pos is not None:
            self._pending = self._raw[-1]
            self._flush(final=True)
        cursor = self._cursor_at_end()
        if error:
            cursor.insertHtml(f'<span style="color:{ERR_C}">[{html.escape(error)}]</span>')
        else:
            index = len(self._raw) - 1
            links = [f'<a href="{ACTION_SCHEME}copy:{index}">{t("copy")}</a>']
            if save:
                links.append(f'<a href="{ACTION_SCHEME}save:{index}">{t("save_md")}</a>')
            links += [f'<a href="{ACTION_SCHEME}followup:{kind}">{t(f"followup_{kind}")}</a>' for kind in FOLLOWUPS]
            cursor.insertHtml(
                f'<span style="color:{DIM};font-size:8pt">{html.escape(meta_text or "")}</span>'
                f'&nbsp;&nbsp;<span style="font-size:9pt">{" · ".join(links)}</span>'
            )
        self._answer_pos = None
        if self._follow:
            self._scroll_to_answer(final=True)

    def note(self, text: str) -> None:
        cursor = self._cursor_at_end()
        cursor.insertHtml(f'<p align="center"><span style="color:{MUTED};font-size:9pt">{html.escape(text)}</span></p>')

    def clear(self) -> None:
        self.document().clear()
        self._raw = []
        self._answer_pos = None
        self._pending = None
        self._math_cache.clear()

    def raw_answer(self, index: int) -> str:
        return self._raw[index] if 0 <= index < len(self._raw) else ""

    # --- formulas ---

    def _resolve_math(self, body: str, math: dict[str, MathSpan]) -> str:
        for url, span in math.items():
            if not self._math_image(url, span):
                body = body.replace(img_tag(url), fallback_html(span, COLORS))
        return body

    def _math_image(self, url: str, span: MathSpan) -> bool:
        cached = self._math_cache.get(url)
        if cached is not None:
            return cached
        dpr = self.devicePixelRatioF()
        size_pt = BASE_PT * self.scale * (1.15 if span.display else 1.0)
        png = mathimg.render_png(span.latex, FG, size_pt, round(96 * dpr))
        ok = png is not None
        if ok:
            image = QImage.fromData(png, "PNG")
            image.setDevicePixelRatio(dpr)
            self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(url), image)
        self._math_cache[url] = ok
        return ok

    # --- scrolling ---

    def _scroll_to_answer(self, final: bool = False) -> None:
        """Follow the answer while its start fits in the view, then keep the start at the top:
        the "❓" line and the first sentences matter more than the tail."""
        doc = self.document()
        bar = self.verticalScrollBar()
        if self._answer_pos is None and not final:
            bar.setValue(bar.maximum())
            return
        block = doc.findBlock(self._answer_pos if self._answer_pos is not None else doc.characterCount() - 1)
        header = block.previous() if block.previous().isValid() else block
        top = doc.documentLayout().blockBoundingRect(header).top()
        total = doc.documentLayout().documentSize().height()
        if total - top <= self.viewport().height() - 16:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(int(top))
            self._follow = False

    def _stop_following(self) -> None:
        self._follow = False

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom.emit(1 if event.angleDelta().y() > 0 else -1)
            return
        self._follow = False
        super().wheelEvent(event)

    # --- links ---

    def _on_anchor(self, url: QUrl) -> None:
        target = url.toString()
        if target.startswith(ACTION_SCHEME):
            parts = target[len(ACTION_SCHEME) :].split(":", 1)
            if parts[0] == "copy" and len(parts) == 2:
                QApplication.clipboard().setText(self.raw_answer(int(parts[1])))
                self.action.emit("copied", "")
            elif parts[0] == "followup" and len(parts) == 2:
                self.action.emit("followup", parts[1])
            elif parts[0] == "save" and len(parts) == 2:
                self.action.emit("save", parts[1])
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
