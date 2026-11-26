"""The tray icon: the app keeps running there when the window is closed."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from overlay.gui.icons import icon_for
from overlay.i18n import t


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_listen: Callable[[], None],
        on_auto: Callable[[bool], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ):
        super().__init__(icon_for("sleep"))
        self.setToolTip("ChatGPT Overlay")
        menu = QMenu()
        self.show_action = QAction(t("show_hide"), menu)
        self.show_action.triggered.connect(on_toggle)
        self.listen_action = QAction(t("tray_listen"), menu)
        self.listen_action.setCheckable(True)
        self.listen_action.triggered.connect(lambda _checked: on_listen())
        self.auto_action = QAction(t("auto_answer"), menu)
        self.auto_action.setCheckable(True)
        self.auto_action.triggered.connect(on_auto)
        settings_action = QAction(t("settings"), menu)
        settings_action.triggered.connect(on_settings)
        quit_action = QAction(t("quit"), menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(self.show_action)
        menu.addSeparator()
        menu.addAction(self.listen_action)
        menu.addAction(self.auto_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        self._on_toggle = on_toggle

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._on_toggle()

    def set_state(self, state: str, tooltip: str) -> None:
        self.setIcon(icon_for(state))
        self.setToolTip(tooltip)

    def sync(self, listening: bool, auto: bool) -> None:
        self.listen_action.setChecked(listening)
        self.auto_action.setChecked(auto)
