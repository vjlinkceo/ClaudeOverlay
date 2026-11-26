"""The main window: the controller's View, built from Qt widgets."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from overlay.config import MODEL_PROFILES
from overlay.core.controller import AnswerMeta, AppController
from overlay.core.labels import efforts, modes, modes_short
from overlay.core.pricing import format_cost
from overlay.gui import frameless
from overlay.gui.app import CaptureGuard, QtScheduler, hide_from_capture
from overlay.gui.chat import mathimg
from overlay.gui.chat.view import ChatView
from overlay.gui.dialogs.profiles import ProfilesDialog
from overlay.gui.dialogs.settings import SettingsDialog
from overlay.gui.frameless import FramelessMixin
from overlay.gui.icons import icon_for
from overlay.gui.mascot import BoboWidget
from overlay.gui.panels.composer import Composer
from overlay.gui.panels.heard import HeardPanel
from overlay.gui.panels.status import StatusStrip
from overlay.gui.region import RegionSelector, screen_under_cursor
from overlay.gui.theme import OK_C, stylesheet
from overlay.gui.tray import Tray
from overlay.i18n import t
from overlay.speech.transcript import Line
from overlay.win.hotkeys import pretty_hotkey
from overlay.win.screen import monitor_under_cursor
from overlay.win.winapi import MenuCaptureGuard, parse_geometry, set_capture_excluded

log = logging.getLogger(__name__)

HIDE_DELAY_MS = 90
TOPIC_CHIP_CHARS = 16
FONT_STEP = 0.1
FONT_RANGE = (0.8, 1.6)
WAVE_MS = 1300


class MainWindow(FramelessMixin, QWidget):
    def __init__(self, cfg: dict, guard: CaptureGuard):
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if cfg["always_on_top"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.cfg = cfg
        self.guard = guard
        self.scheduler = QtScheduler()
        self.c = AppController(cfg, self.scheduler, self)
        self.selector: RegionSelector | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.profiles_dialog: ProfilesDialog | None = None
        self.capture_hidden = False
        self.menu_guard = MenuCaptureGuard()
        self._quitting = False
        self._tray_hint_shown = False
        self._tips: list[tuple[QPushButton, str, str]] = []
        self._mascot_state = "sleep"
        self._compact = bool(cfg.get("compact_mode", False))
        self.voice_buttons: list[QPushButton] = []
        self.setWindowTitle("ChatGPT Overlay")
        self.setWindowIcon(icon_for("sleep"))
        self.setWindowOpacity(float(cfg["window_alpha"]))
        self.setMinimumSize(460, 420)
        self._build()
        self._apply_compact()
        self._restore_geometry()
        # A native handle now, so the frame styles and capture hiding apply before the first frame.
        self._apply_frame()
        self.apply_capture_hiding()
        self.tray = Tray(
            on_toggle=self.toggle_window,
            on_listen=self.c.toggle_listen,
            on_auto=self._on_auto_toggled,
            on_settings=self.open_settings,
            on_quit=self.quit_app,
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self._sync_visibility()
        QShortcut(QKeySequence("Ctrl+Q"), self, self.quit_app)
        QShortcut(QKeySequence("Escape"), self, self.c.stop_answer)
        for keys, delta in (("Ctrl+=", 1), ("Ctrl++", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            QShortcut(QKeySequence(keys), self, lambda d=delta: self._zoom(d))
        self.c.start()
        if self.c.chat.configured and cfg["hide_from_capture"] and not self.capture_hidden:
            self.c.set_status(t("capture_failed"), "error")
        mathimg.warm_up()

    # --- building ---

    def _button(
        self, text: str, slot: Callable[[], None], tip: str = "", accent: bool = False, hotkey: str = ""
    ) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            self._tips.append((button, tip, hotkey))
        if accent:
            button.setProperty("accent", True)
        return button

    def _icon_button(self, glyph: str, slot: Callable[[], None], tip: str) -> QPushButton:
        button = self._button(glyph, slot, tip)
        button.setProperty("icon", True)
        return button

    def _link(self, text: str, slot: Callable[[], None], tip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setProperty("link", True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        if tip:
            button.setToolTip(tip)
        return button

    def _apply_tips(self) -> None:
        hk = {name: pretty_hotkey(combo) for name, combo in self.cfg["hotkeys"].items()}
        for button, key, hotkey in self._tips:
            button.setToolTip(t(key, hotkey=hk[hotkey]) if hotkey else t(key))

    def _build(self) -> None:
        c = self.c
        hk = {name: pretty_hotkey(combo) for name, combo in self.cfg["hotkeys"].items()}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(6)

        self.title_row = QFrame()
        self.title_row.setProperty("titlebar", True)
        title = QHBoxLayout(self.title_row)
        title.setContentsMargins(2, 2, 0, 2)
        title.setSpacing(6)
        self.bobo = BoboWidget(28, "idle", animated=False)
        title.addWidget(self.bobo)
        self.title_label = QLabel("ChatGPT Overlay")
        self.title_label.setProperty("muted", True)
        title.addWidget(self.title_label)
        title.addStretch(1)
        self.pin_btn = self._icon_button("◉", self._toggle_pin, "tip_pin_on")
        self.compact_btn = self._button("▣", self.toggle_compact, "tip_compact", hotkey="compact")
        self.compact_btn.setProperty("icon", True)
        title.addWidget(self.compact_btn)
        title.addWidget(self.pin_btn)
        title.addWidget(self._icon_button("─", self.showMinimized, "tip_minimize"))
        close_btn = self._icon_button("✕", self.close, "tip_close")
        close_btn.setProperty("close", True)
        title.addWidget(close_btn)
        root.addWidget(self.title_row)

        self.visible_banner = QFrame()
        self.visible_banner.setProperty("banner", True)
        banner = QHBoxLayout(self.visible_banner)
        banner.setContentsMargins(10, 4, 6, 4)
        banner_text = QLabel(t("visible_banner"))
        banner_text.setWordWrap(True)
        banner.addWidget(banner_text, 1)
        banner.addWidget(self._link(t("visible_hide"), self._hide_again, t("tip_visible_hide")))
        self.visible_banner.setVisible(False)
        root.addWidget(self.visible_banner)

        self.normal_toolbar = QWidget()
        toolbar = QHBoxLayout(self.normal_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(3)
        self.listen_btn = self._button(t("listen"), c.toggle_listen, "tip_listen", hotkey="listen")
        toolbar.addWidget(self.listen_btn)
        toolbar.addWidget(self._voice_button())
        toolbar.addWidget(self._button(t("screenshot"), c.screenshot_and_ask, "tip_screenshot", hotkey="screenshot"))
        toolbar.addWidget(self._button(t("area"), c.area_and_ask, "tip_area", hotkey="area"))
        self.area_menu = QMenu(self)
        self.area_menu.aboutToShow.connect(self._fill_area_menu)
        self.area_more_btn = self._icon_button("", lambda: None, "tip_area_menu")
        self.area_more_btn.setMenu(self.area_menu)
        toolbar.addWidget(self.area_more_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self._recap_button())
        toolbar.addWidget(self._icon_button("■", c.stop_answer, "tip_stop"))
        toolbar.addWidget(self._icon_button("＋", c.new_chat, "tip_new_chat"))
        toolbar.addWidget(self._icon_button("⚙", lambda: self.open_settings(), "settings"))
        root.addWidget(self.normal_toolbar)

        self.compact_toolbar = QWidget()
        small = QHBoxLayout(self.compact_toolbar)
        small.setContentsMargins(0, 0, 0, 0)
        small.setSpacing(3)
        small.addWidget(self._voice_button())
        small.addWidget(self._button(t("area"), c.area_and_ask, "tip_area", hotkey="area"))
        area_more = self._icon_button("", lambda: None, "tip_area_menu")
        area_more.setMenu(self.area_menu)
        small.addWidget(area_more)
        small.addStretch(1)
        small.addWidget(self._icon_button("■", c.stop_answer, "tip_stop"))
        small.addWidget(self._icon_button("⚙", lambda: self.open_settings(), "settings"))
        root.addWidget(self.compact_toolbar)

        self.mode_row_widget = QWidget()
        mode_row = QHBoxLayout(self.mode_row_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_chips: dict[str, QPushButton] = {}
        full = modes()
        for key, label in modes_short().items():
            chip = QPushButton(label)
            chip.setProperty("chip", True)
            chip.setCheckable(True)
            chip.setToolTip(full[key])
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked, k=key: self._on_mode_chip(k))
            self.mode_group.addButton(chip)
            self.mode_chips[key] = chip
            mode_row.addWidget(chip)
        mode_row.addStretch(1)
        self.model_chip = self._link("", lambda: self.open_settings("model"), t("page_model"))
        mode_row.addWidget(self.model_chip)
        root.addWidget(self.mode_row_widget)

        self.status = StatusStrip(t("meter_context"), t("meter_budget"))
        self.status.on_dismiss = c.set_idle_status
        root.addWidget(self.status)
        self.compact_status = QLabel()
        self.compact_status.setProperty("muted", True)
        self.compact_status.setWordWrap(True)
        root.addWidget(self.compact_status)

        heard_box = QWidget()
        heard_layout = QVBoxLayout(heard_box)
        heard_layout.setContentsMargins(0, 0, 0, 0)
        heard_layout.setSpacing(2)
        heard_head = QHBoxLayout()
        heard_head.setSpacing(4)
        heard_title = QLabel(t("heard_title"))
        heard_title.setProperty("muted", True)
        heard_title.setToolTip(t("heard_tip"))
        heard_head.addWidget(heard_title)
        self.profile_chip = self._link("", lambda: None, t("profile_tip"))
        self.profile_menu = QMenu(self.profile_chip)
        self.profile_menu.aboutToShow.connect(self._fill_profile_menu)
        self.profile_chip.setMenu(self.profile_menu)
        heard_head.addWidget(self.profile_chip)
        self.topic_chip = self._link("", lambda: self.open_settings("context"), t("topic"))
        heard_head.addWidget(self.topic_chip)
        heard_head.addStretch(1)
        self.auto_box = QCheckBox(t("auto_answer"))
        self.auto_box.setChecked(bool(self.cfg["auto_answer"]))
        self.auto_box.toggled.connect(self._on_auto_toggled)
        heard_head.addWidget(self.auto_box)
        heard_head.addWidget(self._link(t("heard_send"), lambda: c.send(include_typed=False), t("tip_heard_send")))
        heard_head.addWidget(self._link("✕", c.clear_heard, t("tip_heard_clear")))
        heard_layout.addLayout(heard_head)
        self.heard = HeardPanel()
        self.heard.height_hint.connect(self._fit_heard)
        heard_layout.addWidget(self.heard)
        self.heard_box = heard_box

        self.stack = QStackedWidget()
        self.empty = self._empty_state(hk)
        self.chat = ChatView(float(self.cfg["font_scale"]))
        self.chat.action.connect(self._on_chat_action)
        self.chat.zoom.connect(self._zoom)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.chat)
        self.latest_chat = ChatView(float(self.cfg["font_scale"]))
        self.latest_chat.action.connect(self._on_latest_action)
        self.latest_chat.zoom.connect(self._zoom)
        self.latest_chat.note(t("compact_hint"))
        self.stack.addWidget(self.latest_chat)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        self.splitter.addWidget(heard_box)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self.splitter, 1)

        self.compose_box = QWidget()
        compose_layout = QVBoxLayout(self.compose_box)
        compose_layout.setContentsMargins(0, 0, 0, 0)
        compose_layout.setSpacing(4)
        self.attach_row = QFrame()
        attach = QHBoxLayout(self.attach_row)
        attach.setContentsMargins(2, 0, 2, 0)
        self.attach_lbl = QLabel()
        self.attach_lbl.setStyleSheet(f"color: {OK_C}; font-size: 9pt;")
        attach.addWidget(self.attach_lbl)
        attach.addWidget(self._link(t("remove"), c.clear_image))
        attach.addStretch(1)
        self.attach_row.setVisible(False)
        compose_layout.addWidget(self.attach_row)

        compose = QHBoxLayout()
        compose.setSpacing(6)
        self.composer = Composer()
        self.composer.submit.connect(c.send)
        self.composer.stop.connect(c.stop_answer)
        self.composer.typed.connect(c._touch)
        self.composer.textChanged.connect(c.typed_changed)
        self.composer.image_dropped.connect(c.attach_image)
        compose.addWidget(self.composer, 1)
        self.answer_btn = self._button(t("answer"), c.send, "tip_answer", accent=True, hotkey="answer")
        self.answer_btn.setFixedHeight(self.composer.minimumHeight())
        self.composer.height_hint.connect(self._fit_composer)
        send_box = QVBoxLayout()
        send_box.setSpacing(2)
        send_box.addStretch(1)
        send_box.addWidget(self.answer_btn)
        self.estimate_lbl = QLabel()
        self.estimate_lbl.setProperty("tiny", True)
        self.estimate_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        send_box.addWidget(self.estimate_lbl)
        compose.addLayout(send_box)
        compose_layout.addLayout(compose)
        self.splitter.addWidget(self.compose_box)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setCollapsible(2, False)

        self._apply_tips()
        self._sync_pin()
        self._sync_chips()

    def _voice_button(self) -> QPushButton:
        button = self._button(t("voice_short"), lambda: None, "tip_voice", hotkey="voice")
        button.pressed.connect(self.c.start_voice)
        button.released.connect(self.c.finish_voice)
        self.voice_buttons.append(button)
        return button

    def _fill_area_menu(self) -> None:
        self.area_menu.clear()
        region = self.cfg.get("saved_area")
        reuse = self.area_menu.addAction(t("saved_area_use") + "\t" + pretty_hotkey(self.cfg["hotkeys"]["area"]))
        reuse.setEnabled(bool(region))
        reuse.triggered.connect(self.c.area_and_ask)
        choose = self.area_menu.addAction(
            t("saved_area_choose") + "\t" + pretty_hotkey(self.cfg["hotkeys"]["choose_area"])
        )
        choose.triggered.connect(self.c.choose_area)
        clear = self.area_menu.addAction(t("saved_area_clear"))
        clear.setEnabled(bool(region))
        clear.triggered.connect(self.c.clear_saved_area)
        if region:
            self.area_menu.addSeparator()
            dimensions = self.area_menu.addAction(
                f"{region['width']} × {region['height']} · {region['left']}, {region['top']}"
            )
            dimensions.setEnabled(False)

    def toggle_compact(self) -> None:
        self.cfg["compact_mode"] = not self._compact
        self.setting_changed("compact_mode")
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def _apply_compact(self) -> None:
        for widget in (self.normal_toolbar, self.mode_row_widget, self.heard_box, self.status):
            widget.setVisible(not self._compact)
        self.compact_toolbar.setVisible(self._compact)
        self.compact_status.setVisible(self._compact)
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored if self._compact else QSizePolicy.Policy.Expanding,
        )
        if self._compact:
            self.setMinimumSize(360, 240)
        else:
            self.setMinimumSize(460, 420)
        self.composer.compact = self._compact
        self.composer.fit_height()
        self.answer_btn.setFixedHeight(self.composer.minimumHeight())
        self.chat.set_render_paused(self._compact)
        self.latest_chat.set_render_paused(not self._compact)
        self.compact_btn.setToolTip(
            t("tip_expand" if self._compact else "tip_compact", hotkey=pretty_hotkey(self.cfg["hotkeys"]["compact"]))
        )
        self.stack.setCurrentWidget(self.latest_chat if self._compact else self.chat if self.chat._raw else self.empty)

    def _on_latest_action(self, kind: str, arg: str) -> None:
        if kind == "save":
            arg = str(len(self.chat._raw) - 1)
        self._on_chat_action(kind, arg)

    def set_voice_recording(self, on: bool) -> None:
        for button in self.voice_buttons:
            button.setProperty("accent", on)
            button.style().unpolish(button)
            button.style().polish(button)

    def _recap_button(self) -> QPushButton:
        button = self._button(t("recap"), lambda: None, "tip_recap", hotkey="summary")
        menu = QMenu(button)
        for minutes in (2, 5, 15):
            action = QAction(t("recap_minutes", minutes=minutes), menu)
            action.triggered.connect(lambda _c=False, m=minutes: self.c.summarize(m))
            menu.addAction(action)
        everything = QAction(t("recap_all"), menu)
        everything.triggered.connect(lambda: self.c.summarize(None))
        menu.addAction(everything)
        menu.addSeparator()
        summary = QAction(t("call_summary"), menu)
        summary.triggered.connect(lambda: self.c.summarize(None))
        menu.addAction(summary)
        button.setMenu(menu)
        return button

    def _empty_state(self, hk: dict[str, str]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self.hero = BoboWidget(160, "sleep", interactive=True)
        self.hero.setToolTip(t("hero_tip"))
        self.hero.clicked.connect(self._hero_clicked)
        self.hero.right_clicked.connect(self._bobo_menu)
        layout.addWidget(self.hero, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = QLabel()
        self.empty_title.setProperty("heading", True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setWordWrap(True)
        layout.addWidget(self.empty_title)
        self.empty_hint = QLabel()
        self.empty_hint.setProperty("muted", True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)
        layout.addStretch(2)
        self._fill_empty_state(hk)
        return page

    def _fill_empty_state(self, hk: dict[str, str]) -> None:
        self.empty_title.setText(t("empty_title", listen=hk["listen"]))
        self.empty_hint.setText(
            t("empty_hint", screenshot=hk["screenshot"], area=hk["area"], toggle=hk["toggle_window"])
        )

    # --- the frame ---

    def _apply_frame(self) -> None:
        frameless.apply(int(self.winId()))

    def title_hit(self, local: QPoint) -> bool:
        if not self.title_row.geometry().contains(local):
            return False
        child = self.childAt(local)
        return child is None or child in (self.title_row, self.title_label)

    def _toggle_pin(self) -> None:
        on = not bool(self.cfg["always_on_top"])
        self.cfg["always_on_top"] = on
        # Changing a window flag recreates the native window: the frame styles and the capture
        # exclusion have to be applied again.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()
        self._apply_frame()
        self.apply_capture_hiding()
        self._sync_pin()

    def _sync_pin(self) -> None:
        on = bool(self.cfg["always_on_top"])
        self.pin_btn.setText("◉" if on else "○")
        self.pin_btn.setProperty("active", on)
        self.pin_btn.setToolTip(t("tip_pin_on" if on else "tip_pin_off"))
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)

    # --- visibility in screen sharing ---

    def _hide_again(self) -> None:
        self.cfg["hide_from_capture"] = True
        self.setting_changed("hide_from_capture")
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def _sync_visibility(self) -> None:
        """When others can see the window, say so, and let Bobo show it too."""
        if not hasattr(self, "tray"):
            return
        self.visible_banner.setVisible(not self.capture_hidden)
        self._apply_mascot()

    def _apply_mascot(self) -> None:
        state = self._mascot_state
        if not self.capture_hidden and state in ("sleep", "idle", "listening", "hearing"):
            state = "visible"
        self.hero.set_state(state)
        self.tray.set_state(state, f"ChatGPT Overlay · {t(f'bobo_state_{state}')}")
        self.setWindowIcon(icon_for(state))

    # --- the mascot ---

    def _hero_clicked(self) -> None:
        self.hero.react("wave", WAVE_MS)
        self.c.toggle_listen()

    def _bobo_menu(self) -> None:
        menu = QMenu(self)
        listen = QAction(t("listen_stop") if self.c.listeners else t("listen"), menu)
        listen.triggered.connect(self.c.toggle_listen)
        menu.addAction(listen)
        recap = QAction(t("recap"), menu)
        recap.triggered.connect(lambda: self.c.summarize(int(self.cfg["summary_minutes"])))
        menu.addAction(recap)
        settings = QAction(t("settings"), menu)
        settings.triggered.connect(lambda: self.open_settings())
        menu.addAction(settings)
        menu.addSeparator()
        quit_action = QAction(t("quit"), menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        menu.exec(QCursor.pos())

    # --- the split between Heard and the chat ---

    def _fit_heard(self, panel_height: int) -> None:
        """Heard grows with its text until the user drags the handle; then that height stays."""
        if self._compact:
            return
        if int(self.cfg["heard_height"]) > 0:
            return
        self._set_heard_height(
            panel_height + self.heard_box.layout().spacing() + self.heard_box.layout().itemAt(0).sizeHint().height()
        )

    def _set_heard_height(self, height: int) -> None:
        self._set_pane_height(0, height)

    def _fit_composer(self, editor_height: int) -> None:
        """The question box grows with its text until the user drags the handle above it."""
        if not self._compact and int(self.cfg["composer_height"]) > 0:
            return
        extra = self.attach_row.sizeHint().height() + 4 if self.attach_row.isVisible() else 0
        self._set_pane_height(2, editor_height + extra)

    def _set_pane_height(self, index: int, height: int) -> None:
        """Resize Heard (0) or the input area (2); the chat in the middle takes the rest."""
        sizes = self.splitter.sizes()
        if len(sizes) < 3 or sum(sizes) <= 0:
            return
        total = sum(sizes)
        other = sizes[2] if index == 0 else sizes[0]
        minimum = self.splitter.widget(index).minimumSizeHint().height()
        height = max(minimum, min(height, total - other - 120))
        sizes[index] = height
        sizes[1] = max(120, total - height - other)
        self.splitter.setSizes(sizes)

    def _on_splitter_moved(self, _pos: int, index: int) -> None:
        if self._compact:
            return
        sizes = self.splitter.sizes()
        if index == 1:
            self.cfg["heard_height"] = int(sizes[0])
        elif index == 2:
            self.cfg["composer_height"] = int(sizes[2])

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._initial_split)

    def _initial_split(self) -> None:
        if self._compact:
            self.composer.fit_height()
            return
        saved = int(self.cfg["heard_height"])
        if saved > 0:
            self._set_heard_height(saved)
        else:
            self.heard.fit_height()
        saved = int(self.cfg["composer_height"])
        if saved > 0:
            self._set_pane_height(2, saved)
        else:
            self.composer.fit_height()

    # --- option sync ---

    def _on_mode_chip(self, key: str) -> None:
        self.cfg["mode"] = key
        self.c.on_mode_change()
        self._sync_chips()

    def _sync_chips(self) -> None:
        self.compact_btn.setToolTip(
            t("tip_expand" if self._compact else "tip_compact", hotkey=pretty_hotkey(self.cfg["hotkeys"]["compact"]))
        )
        chip = self.mode_chips.get(self.cfg["mode"])
        if chip and not chip.isChecked():
            chip.setChecked(True)
        model = MODEL_PROFILES.get(self.cfg["model"], MODEL_PROFILES["gpt_mini"])["label"]
        self.model_chip.setText(f"{model} · {efforts().get(self.cfg['effort'], '')} ▸")
        profile = self.c.profile
        self.profile_chip.setText(t("profile_chip", name=profile.name) if profile else t("profile_pick"))
        topic = (self.cfg.get("context") or "").strip()
        self.topic_chip.setVisible(bool(topic) and profile is None)
        if topic:
            short = topic if len(topic) <= TOPIC_CHIP_CHARS else topic[: TOPIC_CHIP_CHARS - 1] + "…"
            self.topic_chip.setText(t("topic_chip", topic=short))
            self.topic_chip.setToolTip(topic)

    def _on_auto_toggled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.c.on_auto_change(enabled)
        if self.auto_box.isChecked() != enabled:
            self.auto_box.blockSignals(True)
            self.auto_box.setChecked(enabled)
            self.auto_box.blockSignals(False)
        self.tray.sync(bool(self.c.listeners), enabled)

    def _zoom(self, delta: int) -> None:
        scale = 1.0 if delta == 0 else float(self.cfg["font_scale"]) + delta * FONT_STEP
        self.cfg["font_scale"] = round(max(FONT_RANGE[0], min(FONT_RANGE[1], scale)), 2)
        self.setting_changed("font_scale")
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def setting_changed(self, key: str) -> None:
        """The settings window changed cfg[key]; apply what needs applying."""
        c = self.c
        if key == "mode":
            c.on_mode_change()
        elif key == "compact_mode":
            self._save_geometry()
            self._compact = bool(self.cfg["compact_mode"])
            self._apply_compact()
            self._restore_geometry()
            self._initial_split()
        elif key in ("audio_source", "mic_device", "speaker_device"):
            c.on_source_change()
        elif key == "speech_language":
            c.on_speech_lang_change()
        elif key == "auto_answer":
            self._on_auto_toggled(bool(self.cfg["auto_answer"]))
        elif key == "hide_from_capture":
            self.apply_capture_hiding()
            if self.cfg["hide_from_capture"] and not self.capture_hidden:
                c.set_status(t("capture_failed"), "error")
            else:
                c.set_status(t("capture_on") if self.capture_hidden else t("capture_off"), "ok")
        elif key == "window_alpha":
            self.setWindowOpacity(float(self.cfg["window_alpha"]))
        elif key == "font_scale":
            scale = float(self.cfg["font_scale"])
            QApplication.instance().setStyleSheet(stylesheet(scale))
            self.chat.set_scale(scale)
            self.latest_chat.set_scale(scale)
            self.composer.fit_height()
            self.answer_btn.setFixedHeight(self.composer.minimumHeight())
            self.heard.fit_height()
        elif key == "budget_usd":
            c.update_meters()
        elif key in ("model", "effort"):
            c.update_meters()
            if not c.chat.configured:
                c.set_status(t("no_key"), "error")
            elif not c._in_flight():
                c.set_idle_status()
        elif key == "hotkeys":
            c.restart_hotkeys()
            self._apply_tips()
            self._fill_empty_state({name: pretty_hotkey(combo) for name, combo in self.cfg["hotkeys"].items()})
            c.set_idle_status()
        self._sync_chips()

    def _on_chat_action(self, kind: str, arg: str) -> None:
        if kind == "copied":
            self.c.set_status(t("copied"), "ok")
        elif kind == "followup":
            self.c.followup(arg)
        elif kind == "save":
            self._save_answer(int(arg))

    def _save_answer(self, index: int) -> None:
        """The only way a transcript-derived text reaches the disk: the user asks for it."""
        path, _ = QFileDialog.getSaveFileName(self, t("save_summary"), "call-summary.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.chat.raw_answer(index).strip() + "\n")
            self.c.set_status(t("saved_to", path=path), "ok")
        except OSError as e:
            self.c.set_status(t("ui_error", error=e), "error")

    # --- capture hiding ---

    def apply_capture_hiding(self) -> None:
        want = bool(self.cfg["hide_from_capture"])
        ok = set_capture_excluded(int(self.winId()), want)
        self.capture_hidden = want and ok
        self.guard.enabled = self.capture_hidden
        self.menu_guard.enabled = self.capture_hidden
        for dialog in (self.settings_dialog, self.profiles_dialog):
            if dialog is not None:
                hide_from_capture(dialog, want)
        log.info("hide from screen capture: wanted=%s ok=%s", want, ok)
        self._sync_visibility()

    # --- profiles ---

    def _fill_profile_menu(self) -> None:
        from overlay.core import profiles

        menu = self.profile_menu
        menu.clear()
        current = self.cfg.get("profile") or ""
        for profile in profiles.list_profiles():
            action = QAction(profile.name, menu)
            action.setCheckable(True)
            action.setChecked(profile.slug == current)
            action.triggered.connect(lambda _c=False, s=profile.slug: self.use_profile(s))
            menu.addAction(action)
        none = QAction(t("profile_none"), menu)
        none.setCheckable(True)
        none.setChecked(not current)
        none.triggered.connect(lambda: self.use_profile(None))
        menu.addAction(none)
        menu.addSeparator()
        manage = QAction(t("profile_manage"), menu)
        manage.triggered.connect(lambda: self.open_profiles())
        menu.addAction(manage)

    def use_profile(self, slug: str | None) -> None:
        self.c.set_profile(slug)
        self._sync_chips()
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def profile_changed(self) -> None:
        self.c.reload_profile()
        self._sync_chips()

    def open_profiles(self) -> None:
        if self.profiles_dialog is None:
            self.profiles_dialog = ProfilesDialog(self.cfg, self)
        else:
            self.profiles_dialog.reload()
        self.profiles_dialog.show()
        self.profiles_dialog.raise_()
        self.profiles_dialog.activateWindow()

    # --- settings ---

    def configure_api(self) -> None:
        from overlay.ai.keys import save_api_key
        from overlay.config import save_config
        from overlay.gui.dialogs.first_run import FirstRunDialog

        if self.c._busy():
            return
        dialog_cfg = copy.deepcopy(self.cfg)
        dialog = FirstRunDialog(dialog_cfg)
        if not dialog.exec() or not dialog.key:
            return
        try:
            save_api_key(dialog.key, provider=dialog.provider)
        except OSError as exc:
            self.c.set_status(t("key_save_failed", message=str(exc)), "error")
            return
        self.cfg.update({key: dialog_cfg[key] for key in ("model", "audio_source")})
        self.c.chat.reload_keys()
        self.c.on_source_change()
        self._sync_chips()
        if self.settings_dialog is not None:
            self.settings_dialog.sync()
        save_config(self.cfg)
        self.c.set_status(t("key_saved"), "ok")

    def open_settings(self, page: str | None = None) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.cfg, self)
        self.settings_dialog.sync()
        if page:
            self.settings_dialog.show_page(page)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    # --- View protocol ---

    def set_status(self, text: str, kind: str = "info") -> None:
        self.status.set_status(text, kind)
        self.compact_status.setText(text)
        self.compact_status.setToolTip(text)
        self.compact_status.setMaximumHeight(self.compact_status.fontMetrics().lineSpacing() * 3)

    def set_mascot(self, state: str) -> None:
        self._mascot_state = state
        self._apply_mascot()

    def set_listening(self, on: bool) -> None:
        self.listen_btn.setText(t("listen_stop") if on else t("listen"))
        self.listen_btn.setProperty("accent", on)
        self.listen_btn.style().unpolish(self.listen_btn)
        self.listen_btn.style().polish(self.listen_btn)
        self.status.set_listening(on)
        self.tray.sync(on, bool(self.cfg["auto_answer"]))

    def set_level(self, level: float) -> None:
        self.status.set_level(level)

    def set_progress(self, fraction: float | None) -> None:
        self.status.set_progress(fraction)

    def set_session_cost(self, usd: float) -> None:
        self.status.set_extra(t("session_cost", cost=format_cost(usd)))

    def set_estimate(self, costs: dict[str, float | None], tokens_in: int, tokens_out: int) -> None:
        current = costs.get(self.cfg["model"])
        self.estimate_lbl.setText(t("estimate", cost=format_cost(current)) if current is not None else "")
        lines = [
            t("estimate_line", model=MODEL_PROFILES[key]["label"], cost=format_cost(usd))
            for key, usd in costs.items()
            if usd is not None
        ]
        lines.append(t("estimate_tokens", tokens_in=f"{tokens_in:,}".replace(",", " "), tokens_out=tokens_out))
        self.estimate_lbl.setToolTip("\n".join(lines))
        self.answer_btn.setToolTip(self.answer_btn.toolTip().split("\n")[0] + "\n" + "\n".join(lines))

    def set_context_usage(self, fraction: float, tokens: int, limit: int) -> None:
        self.status.set_context(
            fraction, t("tip_context_meter", percent=int(fraction * 100), limit=f"{limit:,}".replace(",", " "))
        )

    def set_budget(self, spent: float, budget: float) -> None:
        if budget <= 0:
            self.status.set_budget(None)
        else:
            tip = t("tip_budget_meter", spent=format_cost(spent), budget=format_cost(budget))
            self.status.set_budget(spent / budget, tip)

    def set_option(self, key: str, value: str) -> None:
        self._sync_chips()
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def refresh_heard(self, lines: list[Line], labeled: bool, reset: bool = False) -> None:
        self.heard.refresh(lines, labeled, reset)

    def heard_override(self) -> str | None:
        return self.heard.override()

    def typed(self) -> str:
        return self.composer.toPlainText()

    def clear_typed(self) -> None:
        self.composer.clear()

    def restore_typed(self, text: str) -> None:
        self.composer.setPlainText(text + self.composer.toPlainText())

    def set_attachment(self, label: str | None) -> None:
        self.attach_lbl.setText(label or "")
        self.attach_row.setVisible(bool(label))
        self.composer.fit_height()

    def show_user_message(self, shown: str) -> None:
        self.stack.setCurrentWidget(self.latest_chat if self._compact else self.chat)
        self.chat.show_user(shown)
        self.latest_chat.clear()

    def begin_answer(self) -> None:
        self.chat.begin_answer()
        self.latest_chat.begin_answer()

    def update_answer(self, markdown: str) -> None:
        self.chat.update_answer(markdown)
        self.latest_chat.update_answer(markdown)

    def finish_answer(self, meta: AnswerMeta | None, error: str | None) -> None:
        text = None
        if meta is not None:
            stats = meta.stats
            first = t("first_token_short", sec=stats.first_token) if stats.first_token is not None else ""
            notice = f" · {meta.notice}" if meta.notice else ""
            text = t("answer_meta", model=stats.model, first=first, total=stats.total, notice=notice)
            if meta.cost is not None:
                text += f" · {format_cost(meta.cost)}"
        self.chat.finish_answer(text, error, save=bool(meta and meta.kind == "summary"))
        self.latest_chat.finish_answer(text, error, save=bool(meta and meta.kind == "summary"))

    def note(self, text: str) -> None:
        self.stack.setCurrentWidget(self.latest_chat if self._compact else self.chat)
        self.chat.note(text)
        self.latest_chat.note(text)

    def clear_chat(self) -> None:
        self.chat.clear()
        self.latest_chat.clear()
        self.latest_chat.note(t("compact_hint"))
        self.stack.setCurrentWidget(self.latest_chat if self._compact else self.empty)

    def hide_for_grab(self, fn: Callable[[], None]) -> None:
        if self.capture_hidden or not self.isVisible():
            fn()
            return
        self.setWindowOpacity(0.0)

        def grab_then_restore():
            try:
                fn()
            finally:
                self.setWindowOpacity(float(self.cfg["window_alpha"]))

        QTimer.singleShot(HIDE_DELAY_MS, grab_then_restore)

    def select_region(self, on_done: Callable[[dict | None], None]) -> None:
        if self.selector is not None:
            return
        hidden = self.capture_hidden or not self.isVisible()
        if not hidden:
            self.setWindowOpacity(0.0)

        def done(region: dict | None):
            self.selector = None
            try:
                on_done(region)
            finally:
                if not hidden:
                    self.setWindowOpacity(float(self.cfg["window_alpha"]))

        self.selector = RegionSelector(
            screen_under_cursor(), monitor_under_cursor(), bool(self.cfg["hide_from_capture"]), t("select_area"), done
        )

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    # --- geometry and lifecycle ---

    def _restore_geometry(self) -> None:
        key = "compact_geometry" if self._compact else "geometry"
        min_w, min_h = (360, 240) if self._compact else (460, 420)
        parsed = parse_geometry(self.cfg.get(key) or "")
        if parsed:
            w, h, x, y = parsed
            if QApplication.screenAt(QPoint(x + 40, y + 10)) is not None:
                self.resize(max(w, min_w), max(h, min_h))
                self.move(x, y)
                return
            log.info("saved geometry %s is off-screen, resetting", self.cfg.get(key))
        area = QApplication.primaryScreen().availableGeometry()
        w, h = (390, min(360, area.height() - 80)) if self._compact else (520, min(700, area.height() - 80))
        self.resize(w, h)
        self.move(area.right() - w - 24, area.top() + 60)

    def _save_geometry(self) -> None:
        if self.isVisible() and not self.isMinimized() and not self.isMaximized():
            g, p = self.geometry(), self.pos()
            key = "compact_geometry" if self._compact else "geometry"
            self.cfg[key] = f"{g.width()}x{g.height()}+{p.x()}+{p.y()}"

    def closeEvent(self, event):
        if not self._quitting and self.cfg["close_to_tray"] and self.tray.isVisible():
            event.ignore()
            self._save_geometry()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "ChatGPT Overlay",
                    t("tray_hidden", toggle=pretty_hotkey(self.cfg["hotkeys"]["toggle_window"])),
                    icon_for("sleep"),
                    4000,
                )
            return
        self.quit_app()
        event.accept()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._save_geometry()
        self.c.shutdown()
        self.menu_guard.close()
        self.tray.hide()
        QApplication.quit()
