"""Process setup that must run before heavy imports and window creation."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import sysconfig
from logging.handlers import RotatingFileHandler
from pathlib import Path

from overlay.config import LOG_PATH

_done = False


def _add_nvidia_dll_dirs() -> None:
    """cuBLAS and cuDNN from the nvidia-*-cu12 wheels, needed to run Whisper on the GPU."""
    site_packages = Path(sysconfig.get_paths()["purelib"])
    for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
        path = site_packages / sub
        if path.is_dir():
            with contextlib.suppress(OSError):
                os.add_dll_directory(str(path))
            # cuDNN loads its sub-libraries with plain LoadLibrary, which ignores add_dll_directory.
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")


def _setup_logging() -> None:
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("faster_whisper", "httpx", "httpx2", "httpcore", "httpcore2", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.WARNING)


def init() -> None:
    global _done
    if _done:
        return
    _done = True
    # pythonw.exe has no console: stdout/stderr are None and library progress bars crash on .write().
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    # The xet downloader hangs at 0 bytes with some providers.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # The Hugging Face CDN can take longer than the default 10 s to send the first byte of a large model.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    _setup_logging()
    _add_nvidia_dll_dirs()
