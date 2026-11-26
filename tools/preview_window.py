"""Render the window, the settings and the first-run dialog with sample content to PNG files.

    .venv\\Scripts\\python tools\\preview_window.py out\\ ru

Needs no screen capture (the window is drawn into images) and never touches config.json.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from overlay import bootstrap  # noqa: E402

bootstrap.init()

import overlay.core.controller  # noqa: E402

overlay.core.controller.save_config = lambda cfg: None  # a preview must not write the real settings
overlay.core.controller.AppController._start_hotkeys = lambda self: None

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from overlay.claude.client import AnswerStats  # noqa: E402
from overlay.config import DEFAULT_CONFIG  # noqa: E402
from overlay.core.controller import AnswerMeta  # noqa: E402
from overlay.gui.app import CaptureGuard  # noqa: E402
from overlay.gui.dialogs.first_run import FirstRunDialog  # noqa: E402
from overlay.gui.theme import stylesheet  # noqa: E402
from overlay.gui.window import MainWindow  # noqa: E402
from overlay.i18n import set_language  # noqa: E402
from overlay.speech.transcript import Line  # noqa: E402

ANSWER = """❓ How does the GIL work in Python?

The **GIL** (Global Interpreter Lock) is a mutex in CPython that lets only one thread run bytecode at a time.

- Threads still help with I/O: while one waits for the network, the GIL is released.
- For CPU-bound work use `multiprocessing` or a C extension.

A waiting-time estimate: $t = \\frac{n}{k} + \\sqrt{x^2 + 1}$, and as a matrix:

$$A = \\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$$

```python
import threading

def worker(n: int) -> int:
    return sum(range(n))  # the GIL is held here
```

| Task | Pick |
|---|---|
| Network, disks | threading, asyncio |
| Computation | multiprocessing |
"""


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    out.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["hide_from_capture"] = "--visible" not in sys.argv  # grab() renders the widget, so hiding does not matter
    cfg["ui_language"] = lang
    cfg["context"] = "Python backend interview"
    set_language(lang)
    app = QApplication(sys.argv)
    # The Windows offscreen plugin has no system font database in restricted environments.
    if not QFontDatabase.families():
        for font in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / font))
    app.setStyleSheet(stylesheet(1.0))
    w = MainWindow(cfg, CaptureGuard())
    w.resize(500, 760)
    w.show()

    def stage1():
        w.set_listening(True)
        w.set_level(0.6)
        w.set_session_cost(0.0042)
        w.set_status("Listening: PC + mic · GPU · text 0.7 s after speech", "ok")
        w.refresh_heard(
            [
                Line("12:41:03", "system", "Tell me how the GIL works in Python and when it gets in the way?", False),
                Line("…", "mic", "well, the GIL is", True),
            ],
            True,
        )
        w.show_user_message("«[12:41:03] Them: Tell me how the GIL works in Python and when it gets in the way?»")
        w.begin_answer()
        w.update_answer(ANSWER)
        w.chat._flush()
        w.finish_answer(AnswerMeta(AnswerStats(model="GPT-5.4 mini", first_token=0.6, total=2.1), "", 0.0031), None)
        QTimer.singleShot(700, stage2)

    def stage2():
        w.grab().save(str(out / f"window_{lang}.png"))
        w.toggle_compact()
        QTimer.singleShot(200, compact)

    def compact():
        w.grab().save(str(out / f"compact_{lang}.png"))
        w.toggle_compact()
        w.open_settings("model")
        QTimer.singleShot(400, stage3)

    def stage3():
        w.settings_dialog.grab().save(str(out / f"settings_{lang}.png"))
        w.settings_dialog.close()
        dialog = FirstRunDialog(cfg)
        dialog.show()
        QTimer.singleShot(
            400, lambda: (dialog.grab().save(str(out / f"first_run_{lang}.png")), dialog.close(), stage4())
        )

    def stage4():
        w.clear_chat()
        w.set_listening(False)
        w.set_status("Ready. Ctrl+Alt+S — screenshot and answer, Ctrl+Alt+L — listen.", "info")
        QTimer.singleShot(300, lambda: (w.grab().save(str(out / f"empty_{lang}.png")), w.quit_app()))

    QTimer.singleShot(600, stage1)
    app.exec()
    print(out)


if __name__ == "__main__":
    main()
