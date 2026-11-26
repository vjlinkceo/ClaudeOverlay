"""The settings window: pages on the left, one form per page. Changes apply at once."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QWidget,
)

from overlay.config import MODEL_PROFILES
from overlay.core.labels import efforts, languages, sources
from overlay.gui.dialogs.hotkey_edit import HotkeyEdit
from overlay.i18n import t
from overlay.speech.audio import list_devices

PAGES = ("model", "audio", "answers", "context", "profiles", "window", "hotkeys")


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, window):
        super().__init__(window, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.cfg = cfg
        self.window_ = window
        self._syncing = False
        self._sync_fns: list[Callable[[], None]] = []
        self.setWindowTitle(t("settings"))
        self.setModal(False)
        self.setMinimumSize(520, 380)
        self.resize(640, 480)
        self.setSizeGripEnabled(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.pages = QStackedWidget()
        for page in PAGES:
            self.nav.addItem(t(f"page_{page}"))
            self.pages.addWidget(getattr(self, f"_page_{page}")())
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)

    def show_page(self, name: str) -> None:
        if name in PAGES:
            self.nav.setCurrentRow(PAGES.index(name))

    def sync(self) -> None:
        self._syncing = True
        try:
            for fn in self._sync_fns:
                fn()
        finally:
            self._syncing = False

    def _changed(self, key: str) -> None:
        if not self._syncing:
            self.window_.setting_changed(key)

    # --- widget helpers ---

    def _form(self) -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setVerticalSpacing(10)
        form.setContentsMargins(6, 4, 6, 4)
        return page, form

    def _label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setProperty("muted", True)
        return label

    def _note(self, key: str) -> QLabel:
        note = QLabel(t(key))
        note.setProperty("muted", True)
        note.setWordWrap(True)
        return note

    def _combo(self, key: str, labels: dict[str, str]) -> QComboBox:
        combo = QComboBox()
        keys = list(labels)
        for value in labels.values():
            combo.addItem(value)

        def changed(index: int):
            if 0 <= index < len(keys):
                self.cfg[key] = keys[index]
                self._changed(key)

        combo.currentIndexChanged.connect(changed)
        self._sync_fns.append(lambda: combo.setCurrentIndex(keys.index(self.cfg[key]) if self.cfg[key] in keys else 0))
        return combo

    def _device_combo(self, key: str, names: list[str]) -> QComboBox:
        """The configured device stays selectable even while it is unplugged."""
        current = self.cfg.get(key, "")
        if current and current not in names:
            names = [*names, current]
        return self._combo(key, {"": t("device_default"), **{name: name for name in names}})

    def _check(self, key: str, text_key: str) -> QCheckBox:
        box = QCheckBox(t(text_key))

        def toggled(on: bool):
            self.cfg[key] = bool(on)
            self._changed(key)

        box.toggled.connect(toggled)
        self._sync_fns.append(lambda: box.setChecked(bool(self.cfg[key])))
        return box

    def _slider(self, key: str, lo: int, hi: int, scale: float, fmt: Callable[[float], str]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        value = QLabel()
        value.setProperty("muted", True)
        value.setFixedWidth(60)

        def changed(pos: int):
            self.cfg[key] = round(pos * scale, 2)
            value.setText(fmt(self.cfg[key]))
            self._changed(key)

        slider.valueChanged.connect(changed)

        def sync():
            slider.setValue(round(float(self.cfg[key]) / scale))
            value.setText(fmt(float(self.cfg[key])))

        self._sync_fns.append(sync)
        layout.addWidget(slider, 1)
        layout.addWidget(value)
        return row

    def _spin(self, key: str, lo: int, hi: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)

        def changed(v: int):
            self.cfg[key] = int(v)
            self._changed(key)

        spin.valueChanged.connect(changed)
        self._sync_fns.append(lambda: spin.setValue(int(self.cfg[key])))
        return spin

    def _dspin(self, key: str, lo: float, hi: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(2)

        def changed(v: float):
            self.cfg[key] = float(v)
            self._changed(key)

        spin.valueChanged.connect(changed)
        self._sync_fns.append(lambda: spin.setValue(float(self.cfg[key])))
        return spin

    def _line(self, key: str) -> QLineEdit:
        edit = QLineEdit()

        def changed(text: str):
            self.cfg[key] = text
            self._changed(key)

        edit.textChanged.connect(changed)

        def sync():
            if edit.text() != self.cfg.get(key, ""):
                edit.setText(self.cfg.get(key, ""))

        self._sync_fns.append(sync)
        return edit

    def _text(self, key: str) -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setMinimumHeight(edit.fontMetrics().lineSpacing() * 3 + 16)

        def changed():
            self.cfg[key] = edit.toPlainText()
            self._changed(key)

        edit.textChanged.connect(changed)

        def sync():
            if edit.toPlainText() != self.cfg.get(key, ""):
                edit.setPlainText(self.cfg.get(key, ""))

        self._sync_fns.append(sync)
        return edit

    def _hotkey(self, name: str) -> HotkeyEdit:
        edit = HotkeyEdit(self.cfg["hotkeys"][name])

        def changed(combo: str):
            self.cfg["hotkeys"][name] = combo
            self._changed("hotkeys")

        edit.changed.connect(changed)
        self._sync_fns.append(lambda: edit.set_combo(self.cfg["hotkeys"][name]))
        return edit

    # --- pages ---

    def _page_model(self) -> QWidget:
        page, form = self._form()
        form.addRow(self._label("model"), self._combo("model", {k: p["label"] for k, p in MODEL_PROFILES.items()}))
        form.addRow(self._label("effort"), self._combo("effort", efforts()))
        form.addRow(self._note("model_note"))
        form.addRow(self._note("api_note"))
        key_button = QPushButton(t("manage_api_key"))
        key_button.clicked.connect(self.window_.configure_api)
        form.addRow(key_button)
        return page

    def _page_audio(self) -> QWidget:
        page, form = self._form()
        mics, speakers = list_devices()
        form.addRow(self._label("source"), self._combo("audio_source", sources()))
        form.addRow(self._label("speaker_device"), self._device_combo("speaker_device", speakers))
        form.addRow(self._label("mic_device"), self._device_combo("mic_device", mics))
        form.addRow(self._label("speech"), self._combo("speech_language", languages()))
        form.addRow(self._label("vad_threshold"), self._slider("vad_threshold", 10, 95, 0.01, lambda v: f"{v:.2f}"))
        form.addRow(self._label("end_silence"), self._slider("end_silence", 3, 20, 0.1, lambda v: f"{v:.1f} s"))
        return page

    def _page_answers(self) -> QWidget:
        page, form = self._form()
        form.addRow(self._label("reply"), self._combo("answer_language", languages()))
        form.addRow(self._check("auto_answer", "auto_answer"))
        form.addRow(self._label("auto_clear"), self._spin("auto_clear_minutes", 0, 1440))
        form.addRow(self._label("budget"), self._dspin("budget_usd", 0.0, 10000.0, 0.5))
        return page

    def _page_context(self) -> QWidget:
        page, form = self._form()
        form.addRow(self._label("topic"), self._line("context"))
        form.addRow(self._label("terms"), self._text("glossary"))
        form.addRow(self._note("context_note"))
        return page

    def _page_profiles(self) -> QWidget:
        page, form = self._form()
        form.addRow(self._note("profiles_note"))
        button = QPushButton(t("profile_manage"))
        button.clicked.connect(lambda: self.window_.open_profiles())
        form.addRow(button)
        return page

    def _page_window(self) -> QWidget:
        page, form = self._form()
        form.addRow(self._check("compact_mode", "compact_mode"))
        form.addRow(self._label("opacity"), self._slider("window_alpha", 30, 100, 0.01, lambda v: f"{v * 100:.0f}%"))
        form.addRow(self._label("font_scale"), self._slider("font_scale", 80, 160, 0.01, lambda v: f"{v * 100:.0f}%"))
        form.addRow(
            self._label("visibility"),
            self._combo("hide_from_capture", {True: t("visibility_hidden"), False: t("visibility_visible")}),
        )
        form.addRow(self._note("visibility_note"))
        form.addRow(self._check("close_to_tray", "close_to_tray"))
        form.addRow(self._label("ui_language"), self._combo("ui_language", {"en": "English", "ru": "Русский"}))
        return page

    def _page_hotkeys(self) -> QWidget:
        page, form = self._form()
        for name in self.cfg["hotkeys"]:
            form.addRow(self._label(f"hotkey_{name}"), self._hotkey(name))
        form.addRow(self._note("hotkeys_note"))
        return page
