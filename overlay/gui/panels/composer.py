"""The question box: Enter sends, Shift+Enter breaks the line, images arrive by paste or drop."""

from __future__ import annotations

import io

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QPlainTextEdit

from overlay.i18n import t
from overlay.win.screen import load_image_file

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
LINES = 3
MAX_LINES = 8


class Composer(QPlainTextEdit):
    submit = Signal()
    stop = Signal()
    image_dropped = Signal(object)  # a PIL image
    typed = Signal()
    height_hint = Signal(int)

    def __init__(self):
        super().__init__()
        self.compact = False
        self.setPlaceholderText(t("placeholder_input"))
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.textChanged.connect(self.fit_height)
        self.fit_height()

    def fit_height(self) -> None:
        """Asks for 3 to 8 lines, as the text needs; the window decides what it gets."""
        line = self.fontMetrics().lineSpacing()
        minimum, maximum = (1, 3) if self.compact else (LINES, MAX_LINES)
        self.setMinimumHeight(minimum * line + 16)
        lines = max(minimum, min(maximum, self.document().lineCount()))
        self.height_hint.emit(lines * line + 16)

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.stop.emit()
            return
        self.typed.emit()
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        image = _image_from_mime(source)
        if image is not None:
            self.image_dropped.emit(image)
            return
        super().insertFromMimeData(source)


def _image_from_mime(source) -> Image.Image | None:
    if source.hasImage():
        qimage = QImage(source.imageData())
        if not qimage.isNull():
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            qimage.save(buffer, "PNG")
            return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")
    if source.hasUrls():
        for url in source.urls():
            path = url.toLocalFile()
            if path.lower().endswith(IMAGE_EXT):
                return load_image_file(path)
    return None
