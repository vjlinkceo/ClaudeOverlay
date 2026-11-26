"""A window without the system frame that keeps Windows' resizing, snapping, shadow and rounded corners.

Qt's FramelessWindowHint drops the styles Windows needs for those, so they are put back on the native
window; WM_NCCALCSIZE then claims the whole window as client area (no title bar is drawn), and
WM_NCHITTEST tells Windows where the resize edges and the draggable title are.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import QPoint

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.SetWindowPos.restype = wt.BOOL
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_user32.GetWindowRect.restype = wt.BOOL
_user32.IsZoomed.argtypes = [wt.HWND]
_user32.IsZoomed.restype = wt.BOOL
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int

GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00C00000, 0x00040000, 0x00020000, 0x00010000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0004, 0x0010, 0x0020
WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92
BORDER_PX = 8  # physical pixels of resize edge


def apply(hwnd: int) -> None:
    """Call after the native window exists, and again whenever Qt recreates it (window flag changes)."""
    style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
    _user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    _user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    )


def _signed16(value: int) -> int:
    return ctypes.c_short(value & 0xFFFF).value


class FramelessMixin:
    """Mix into a top-level QWidget. It must define `title_hit(local: QPoint) -> bool`, which gets the
    point in the window's own logical coordinates."""

    def title_hit(self, local: QPoint) -> bool:  # pragma: no cover - overridden
        return False

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_NCCALCSIZE and msg.wParam:
                if _user32.IsZoomed(msg.hWnd):
                    # Maximized windows hang over the screen edge by the frame width; pull them back in.
                    rect = wt.RECT.from_address(msg.lParam)
                    pad = _user32.GetSystemMetrics(SM_CXPADDEDBORDER)
                    fx, fy = _user32.GetSystemMetrics(SM_CXFRAME) + pad, _user32.GetSystemMetrics(SM_CYFRAME) + pad
                    rect.left += fx
                    rect.top += fy
                    rect.right -= fx
                    rect.bottom -= fy
                return True, 0
            if msg.message == WM_NCHITTEST:
                return True, self._hit_test(msg.hWnd, msg.lParam)
        return super().nativeEvent(event_type, message)

    def _hit_test(self, hwnd: int, lparam: int) -> int:
        x, y = _signed16(lparam), _signed16(lparam >> 16)
        rect = wt.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if not _user32.IsZoomed(hwnd):
            left, right = x < rect.left + BORDER_PX, x >= rect.right - BORDER_PX
            top, bottom = y < rect.top + BORDER_PX, y >= rect.bottom - BORDER_PX
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        # The message point is physical and screen-relative; within one window the ratio is uniform.
        dpr = self.devicePixelRatioF()
        local = QPoint(round((x - rect.left) / dpr), round((y - rect.top) / dpr))
        return HTCAPTION if self.title_hit(local) else HTCLIENT
