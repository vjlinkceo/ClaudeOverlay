"""WinAPI helpers: the cursor, hiding windows and menus from screen capture, saved window geometry."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import re

log = logging.getLogger(__name__)

_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
_user32.SetWindowDisplayAffinity.restype = wt.BOOL

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11

_WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
_user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, _WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD]
_user32.SetWinEventHook.restype = wt.HANDLE
_user32.UnhookWinEvent.argtypes = [wt.HANDLE]
EVENT_SYSTEM_MENUPOPUPSTART = 0x0006
WINEVENT_OUTOFCONTEXT = 0x0000


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_capture_excluded(hwnd: int, excluded: bool) -> bool:
    """Hide a window from all screen capture (Zoom, Meet, Teams, OBS, screenshots); it stays visible
    on the monitor. Requires Windows 10 2004 or newer."""
    ok = bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE))
    if not ok:
        log.warning("SetWindowDisplayAffinity failed: error %d", ctypes.get_last_error())
    return ok


class MenuCaptureGuard:
    """Native pop-up menus (the window's system menu) are separate windows; hide them from capture too.
    The hook only sees this process's menus and must be installed on the UI thread."""

    def __init__(self):
        self.enabled = False
        # Keep a reference: otherwise the garbage collector frees the callback while Windows still calls it.
        self._proc = _WINEVENTPROC(self._on_menu)
        self._hook = _user32.SetWinEventHook(
            EVENT_SYSTEM_MENUPOPUPSTART,
            EVENT_SYSTEM_MENUPOPUPSTART,
            None,
            self._proc,
            os.getpid(),
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if not self._hook:
            log.warning("SetWinEventHook failed: menus will not be hidden from capture")

    def _on_menu(self, _hook, _event, hwnd, _obj, _child, _thread, _time):
        if self.enabled and hwnd:
            _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def close(self) -> None:
        if self._hook:
            _user32.UnhookWinEvent(self._hook)
            self._hook = None


def parse_geometry(geometry: str) -> tuple[int, int, int, int] | None:
    """'709x717+-1407+434' -> (w, h, x, y)."""
    m = _GEOMETRY_RE.match(geometry.strip()) if geometry else None
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]
