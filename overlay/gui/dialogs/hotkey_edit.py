"""A button that records the next key combination pressed on it."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton

from overlay.i18n import t
from overlay.win.hotkeys import parse_hotkey, pretty_hotkey

# Virtual key codes match RegisterHotKey, so a letter is the same on a Cyrillic layout.
_VK_SPECIAL = {
    0x20: "space",
    0x0D: "enter",
    0x09: "tab",
    0x1B: "esc",
    0x2D: "insert",
    0x2E: "delete",
    0x24: "home",
    0x23: "end",
    0x21: "pageup",
    0x22: "pagedown",
    0x26: "up",
    0x28: "down",
    0x25: "left",
    0x27: "right",
}
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


def combo_from_event(event) -> str | None:
    """'ctrl+alt+s' for the event, or None while only modifiers are held or the key is unusable."""
    if event.key() in _MODIFIER_KEYS:
        return None
    mods = event.modifiers()
    parts = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("win")
    vk = event.nativeVirtualKey()
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        key = chr(vk).lower()
    elif 0x70 <= vk <= 0x87:
        key = f"f{vk - 0x6F}"
    elif vk in _VK_SPECIAL:
        key = _VK_SPECIAL[vk]
    else:
        return None
    if not parts and not key.startswith("f"):
        return None  # a bare letter would steal typing from every program
    combo = "+".join(parts + [key])
    try:
        parse_hotkey(combo)
    except ValueError:
        return None
    return combo


class HotkeyEdit(QPushButton):
    changed = Signal(str)

    def __init__(self, combo: str):
        super().__init__(pretty_hotkey(combo))
        self.combo = combo
        self.recording = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.clicked.connect(self._start)
        self.setToolTip(t("hotkey_edit_tip"))

    def set_combo(self, combo: str) -> None:
        self.combo = combo
        self.setText(pretty_hotkey(combo))

    def _start(self) -> None:
        self.recording = True
        self.setText(t("hotkey_recording"))
        self.setFocus()

    def _stop(self) -> None:
        self.recording = False
        self.setText(pretty_hotkey(self.combo))

    def keyPressEvent(self, event):
        if not self.recording:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._stop()
            return
        combo = combo_from_event(event)
        if combo is None:
            return
        self.combo = combo
        self._stop()
        self.changed.emit(combo)

    def focusOutEvent(self, event):
        if self.recording:
            self._stop()
        super().focusOutEvent(event)
