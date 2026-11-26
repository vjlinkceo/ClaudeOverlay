"""Screenshots (monitor under the cursor or a region) and image encoding for the AI provider."""

from __future__ import annotations

import base64
import io

import mss
from PIL import Image

from overlay.win.winapi import cursor_pos


def _mss():
    return (getattr(mss, "MSS", None) or mss.mss)()


def monitor_under_cursor() -> dict:
    x, y = cursor_pos()
    with _mss() as screens:
        monitors = screens.monitors[1:] or screens.monitors
        return next(
            (m for m in monitors if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]),
            monitors[0],
        )


def screen_monitors() -> list[dict]:
    with _mss() as screens:
        return [dict(m) for m in screens.monitors[1:]]


def grab(region: dict) -> Image.Image:
    """region: {'left', 'top', 'width', 'height'} in physical pixels."""
    with _mss() as screens:
        shot = screens.grab(region)
        return Image.frombytes("RGB", shot.size, shot.rgb)


def scaled_pixels(size: tuple[int, int], max_side: int) -> int:
    """How many pixels the image has after `encode_image` scales it."""
    w, h = size
    scale = min(1.0, max_side / max(w, h, 1))
    return round(w * scale) * round(h * scale)


def encode_image(img: Image.Image, max_side: int) -> str:
    img = img.convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS, reducing_gap=2.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def load_image_file(path: str) -> Image.Image | None:
    try:
        img = Image.open(path)
        img.load()
        return img
    except Exception:
        return None
