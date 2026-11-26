"""LaTeX formulas as PNG images, typeset by matplotlib's mathtext. No Qt here, so it is unit-tested."""

from __future__ import annotations

import io
import logging
import re
import threading
from functools import lru_cache

log = logging.getLogger(__name__)

_UNSUPPORTED = re.compile(r"\\(displaystyle|textstyle|,|;|!|quad|qquad)\b|\\(?=[,;!])")
_STRIP = {r"\displaystyle": "", r"\textstyle": "", r"\quad": r"\ ", r"\qquad": r"\ \ ", r"\,": r"\ ", r"\;": r"\ "}


def warm_up() -> None:
    """Importing matplotlib takes a while (and builds a font cache on the first run): do it off the UI thread."""

    def load():
        try:
            render_png("x", "#ffffff", 10.0, 96)
        except Exception:
            log.exception("matplotlib warm-up failed")

    threading.Thread(target=load, name="mathtext-warmup", daemon=True).start()


def _clean(latex: str) -> str:
    for old, new in _STRIP.items():
        latex = latex.replace(old, new)
    return " ".join(latex.split())


@lru_cache(maxsize=512)
def render_png(latex: str, color: str, size_pt: float, dpi: int) -> bytes | None:
    """None when mathtext cannot typeset the formula (matrices, \\text with odd content, unknown commands)."""
    latex = _clean(latex)
    if not latex or "$" in latex:
        return None
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        fig = Figure(figsize=(0.1, 0.1))
        FigureCanvasAgg(fig)
        fig.text(0, 0, f"${latex}$", fontsize=size_pt, color=color, math_fontfamily="dejavusans")
        buf = io.BytesIO()
        fig.savefig(buf, dpi=dpi, format="png", transparent=True, bbox_inches="tight", pad_inches=0.03)
        return buf.getvalue()
    except Exception as e:
        log.info("mathtext fallback for %r: %s", latex[:80], str(e).splitlines()[0][:120])
        return None
