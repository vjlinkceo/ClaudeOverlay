"""Entry point: process setup, then the window."""

from __future__ import annotations

import logging
import sys
import threading

from overlay import bootstrap

bootstrap.init()

log = logging.getLogger(__name__)


def main() -> None:
    sys.excepthook = lambda *exc: log.error("uncaught", exc_info=exc)
    threading.excepthook = lambda args: log.error(
        "uncaught in thread %s", args.thread, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )
    from overlay.gui.app import run

    sys.exit(run())
