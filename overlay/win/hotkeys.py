"""Global hotkeys via RegisterHotKey: they work while the window is not focused."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import threading

from overlay.i18n import t

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_TIMER = 0x0312, 0x0012, 0x0113
SPECIAL_VK = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


def parse_hotkey(combo: str) -> tuple[int, int]:
    """'ctrl+alt+s' -> (modifiers, virtual key). Letter VKs do not depend on the keyboard layout."""
    mods, vk = 0, None
    for part in combo.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "super"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = 0x6F + int(part[1:])
        elif part in SPECIAL_VK:
            vk = SPECIAL_VK[part]
        else:
            raise ValueError(f"unknown key '{part}' in '{combo}'")
    if vk is None:
        raise ValueError(f"no main key in '{combo}'")
    return mods, vk


def pretty_hotkey(combo: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in combo.split("+"))


def combination_down(mods: int, vk: int, key_state) -> bool:
    if not key_state(vk) & 0x8000:
        return False
    for flag, key in ((MOD_CONTROL, 0x11), (MOD_ALT, 0x12), (MOD_SHIFT, 0x10)):
        if mods & flag and not key_state(key) & 0x8000:
            return False
    return not mods & MOD_WIN or bool((key_state(0x5B) | key_state(0x5C)) & 0x8000)


class HotkeyThread(threading.Thread):
    def __init__(self, bindings: dict[str, str], on_hotkey, on_error):
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = bindings
        self.on_hotkey = on_hotkey
        self.on_error = on_error
        self.thread_id = None
        self._stopping = threading.Event()

    def run(self):
        user32 = ctypes.windll.user32
        # Create the message queue before publishing the thread ID, so an immediate stop cannot be lost.
        user32.PeekMessageW(ctypes.byref(wt.MSG()), None, 0, 0, 0)
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if self._stopping.is_set():
            return
        user32.SetTimer.argtypes = [wt.HWND, wt.WPARAM, wt.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = wt.WPARAM
        user32.KillTimer.argtypes = [wt.HWND, wt.WPARAM]
        registered: dict[int, str] = {}
        failed = []
        for hk_id, (name, combo) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse_hotkey(combo)
            except ValueError as e:
                failed.append(str(e))
                continue
            if user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk):
                registered[hk_id] = name
            else:
                failed.append(t("hotkey_taken", combo=pretty_hotkey(combo)))
        if failed:
            self.on_error(t("hotkeys_failed", details="; ".join(failed)))

        msg, hold, timer_id = wt.MSG(), None, 0
        try:
            while not self._stopping.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in registered:
                    name = registered[msg.wParam]
                    if name == "voice":
                        if hold is None:
                            hold = parse_hotkey(self.bindings[name])
                            timer_id = user32.SetTimer(None, 0, 15, None)
                            if timer_id:
                                self.on_hotkey("voice_start")
                            else:
                                hold = None
                                self.on_error(t("voice_hotkey_failed"))
                    else:
                        self.on_hotkey(name)
                elif msg.message == WM_TIMER and msg.wParam == timer_id and hold is not None:
                    if not combination_down(*hold, user32.GetAsyncKeyState):
                        self.on_hotkey("voice_stop")
                        user32.KillTimer(None, timer_id)
                        hold, timer_id = None, 0
        finally:
            if timer_id:
                user32.KillTimer(None, timer_id)
                self.on_hotkey("voice_stop")
            for hk_id in registered:
                user32.UnregisterHotKey(None, hk_id)

    def stop(self):
        self._stopping.set()
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
