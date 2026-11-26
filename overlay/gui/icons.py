"""Window and tray icons: Bobo's face per state, at exact integer scales."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QIcon, QImage, QPixmap

from overlay.assets import bobo


def _pixmap(rows: bobo.Rows, scale: int) -> QPixmap:
    image = bobo.render(rows, scale)
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


@lru_cache(maxsize=16)
def icon_for(state: str) -> QIcon:
    icon = QIcon()
    face = bobo.icon_rows(state)
    for scale in (1, 2, 3):
        icon.addPixmap(_pixmap(face, scale))
    icon.addPixmap(_pixmap(bobo.frames(state)[0][0], 4))
    return icon
