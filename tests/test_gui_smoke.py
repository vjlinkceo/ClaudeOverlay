"""Builds the Qt window off-screen and drives the View protocol once. Skipped without PySide6."""

import copy
import os
import unittest
import unittest.mock

from overlay.claude.client import AnswerStats
from overlay.config import DEFAULT_CONFIG
from overlay.core.controller import AnswerMeta
from overlay.speech.transcript import Line

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from overlay.gui.app import CaptureGuard
    from overlay.gui.window import MainWindow
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from PySide6.QtGui import QFontDatabase

        from overlay.gui.theme import stylesheet

        if not QFontDatabase.families():
            for font in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "consola.ttf"):
                QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
        cls.app.setStyleSheet(stylesheet(1.0))

    def setUp(self):
        self.enterContext(unittest.mock.patch("overlay.core.controller.HotkeyThread"))
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["hide_from_capture"] = False
        cfg["hotkeys"] = {name: "ctrl+alt+shift+f12" for name in cfg["hotkeys"]}
        self.window = MainWindow(cfg, CaptureGuard())

    def tearDown(self):
        self.window.c.hotkeys.stop()
        self.window.c.ui.stop()
        self.window.tray.hide()
        self.window.deleteLater()
        self.app.processEvents()

    def test_answer_flow_renders(self):
        w = self.window
        w.show_user_message("What is GIL?")
        w.begin_answer()
        w.update_answer("❓ What is GIL?\n\nA **lock** with `code` and $x^2$.\n\n```python\nx = 1\n```")
        w.chat._flush()
        self.assertIn("▍", w.chat.toPlainText())
        w.finish_answer(AnswerMeta(AnswerStats(model="Sonnet", first_token=0.4, total=1.3), ""), None)
        text = w.chat.toPlainText()
        self.assertIn("What is GIL?", text)
        self.assertIn("lock", text)
        self.assertIn("x = 1", text)
        self.assertNotIn("▍", text)
        self.assertEqual(w.stack.currentWidget(), w.chat)
        self.assertEqual(w.chat.raw_answer(0)[:1], "❓")
        w.clear_chat()
        self.assertEqual(w.stack.currentWidget(), w.empty)

    def test_heard_panel_edit_becomes_override(self):
        w = self.window
        lines = [Line("12:00:00", "system", "Misheard words", False), Line("…", "mic", "typing", True)]
        w.refresh_heard(lines, True)
        self.assertIsNone(w.heard_override())
        self.assertIn("Misheard words", w.heard.toPlainText())
        w.heard.setPlainText("Corrected words")
        self.assertEqual(w.heard_override(), "Corrected words")
        w.refresh_heard(
            [Line("12:00:00", "system", "Misheard words", False), Line("12:00:05", "system", "More", False)], True
        )
        self.assertIn("More", w.heard.toPlainText())
        self.assertIn("Corrected words", w.heard.toPlainText())
        w.refresh_heard([], True, reset=True)
        self.assertIsNone(w.heard_override())

    def test_compact_mode_preserves_full_history_and_window_geometry(self):
        w = self.window
        w.show()
        w.resize(560, 740)
        self.app.processEvents()
        w.show_user_message("First question")
        w.begin_answer()
        w.update_answer("First answer")
        w.finish_answer(None, None)
        w.composer.setPlainText("Draft")
        w.toggle_compact()
        self.app.processEvents()
        self.assertTrue(w._compact)
        self.assertTrue(w.heard_box.isHidden())
        self.assertTrue(w.normal_toolbar.isHidden())
        self.assertEqual(w.stack.currentWidget(), w.latest_chat)
        self.assertLess(w.height(), 500)
        self.assertLess(w.width(), 500)
        self.assertEqual(w.composer.toPlainText(), "Draft")
        w.show_user_message("Second question")
        w.begin_answer()
        w.update_answer("Second answer")
        w.finish_answer(None, None)
        self.assertNotIn("First answer", w.latest_chat.toPlainText())
        self.assertIn("Second answer", w.latest_chat.toPlainText())
        w.toggle_compact()
        self.app.processEvents()
        self.assertIn("First answer", w.chat.toPlainText())
        self.assertIn("Second answer", w.chat.toPlainText())
        self.assertEqual(w.size().width(), 560)
        self.assertEqual(w.size().height(), 740)

    def test_first_chunk_is_visible_without_render_timer(self):
        w = self.window
        w.show_user_message("Question")
        w.begin_answer()
        w.update_answer("Visible immediately")
        self.assertIn("Visible immediately", w.chat.toPlainText())
        self.assertFalse(w.chat._timer.isActive())

    def test_voice_button_uses_press_and_release(self):
        with (
            unittest.mock.patch.object(self.window.c, "start_voice") as start,
            unittest.mock.patch.object(self.window.c, "finish_voice") as finish,
        ):
            button = self.window._voice_button()
            button.pressed.emit()
            button.released.emit()
        start.assert_called_once()
        finish.assert_called_once()

    def test_status_and_options(self):
        w = self.window
        w.set_status("Boom", "error")
        self.assertTrue(w.status.dismiss.isVisible() or not w.isVisible())
        w.set_listening(True)
        self.assertTrue(w.listen_btn.property("accent"))
        w.c.set_option("mode", "solve")
        self.assertTrue(w.mode_chips["solve"].isChecked())
        w.open_settings("audio")
        self.assertEqual(w.settings_dialog.nav.currentRow(), 1)
        w.settings_dialog.close()

    def test_provider_setup_and_stale_key_check(self):
        from overlay.gui.dialogs.first_run import FirstRunDialog

        dialog = FirstRunDialog(self.window.cfg)
        self.assertEqual(dialog.provider, "openai")
        self.assertIn("OpenAI", dialog.intro.text())
        dialog.key_edit.setText("old-test-key")
        stale = dialog._generation
        dialog.key_edit.setText("new-test-key")
        dialog._on_checked("old result", True, stale)
        self.assertEqual(dialog.result.text(), "")
        dialog.provider_combo.setCurrentIndex(1)
        self.assertEqual(dialog.provider, "anthropic")
        self.assertEqual(dialog.key_edit.text(), "")
        self.assertFalse(dialog.save_btn.isEnabled())
        dialog.provider_combo.setCurrentIndex(0)
        dialog.key_edit.setText("sk-ant-test-not-real")
        dialog._save()
        self.assertIsNone(dialog.key)
        self.assertTrue(dialog.result.text())
        dialog.deleteLater()

    def test_api_settings_save_selected_provider_and_reload(self):
        from types import SimpleNamespace

        def dialog(cfg):
            cfg.update(model="sonnet", audio_source="mic")
            return SimpleNamespace(exec=lambda: 1, key="fake-ui-test", provider="anthropic")

        with (
            unittest.mock.patch("overlay.gui.dialogs.first_run.FirstRunDialog", side_effect=dialog),
            unittest.mock.patch("overlay.ai.keys.save_api_key") as save_key,
            unittest.mock.patch("overlay.config.save_config"),
            unittest.mock.patch.object(self.window.c.chat, "reload_keys") as reload_keys,
            unittest.mock.patch.object(self.window.c, "on_source_change"),
        ):
            self.window.configure_api()
        save_key.assert_called_once_with("fake-ui-test", provider="anthropic")
        reload_keys.assert_called_once()
        self.assertEqual(self.window.cfg["model"], "sonnet")
        self.assertIn("Anthropic", self.window.model_chip.text())

    def test_failed_encryption_keeps_previous_settings(self):
        from types import SimpleNamespace

        def dialog(cfg):
            cfg.update(model="sonnet", audio_source="mic")
            return SimpleNamespace(exec=lambda: 1, key="fake-ui-test", provider="anthropic")

        with (
            unittest.mock.patch("overlay.gui.dialogs.first_run.FirstRunDialog", side_effect=dialog),
            unittest.mock.patch("overlay.ai.keys.save_api_key", side_effect=OSError("DPAPI unavailable")),
            unittest.mock.patch("overlay.config.save_config") as save_config,
            unittest.mock.patch.object(self.window.c.chat, "reload_keys") as reload_keys,
        ):
            self.window.configure_api()
        save_config.assert_not_called()
        reload_keys.assert_not_called()
        self.assertEqual(self.window.cfg["model"], "gpt_mini")
        self.assertEqual(self.window.cfg["audio_source"], DEFAULT_CONFIG["audio_source"])

    def test_title_bar_and_resizable_parts(self):
        from PySide6.QtCore import QPoint

        w = self.window
        w.resize(520, 760)
        w.show()
        self.app.processEvents()
        self.assertTrue(w.title_hit(w.title_label.mapTo(w, w.title_label.rect().center())))
        self.assertFalse(w.title_hit(w.pin_btn.mapTo(w, w.pin_btn.rect().center())))
        self.assertFalse(w.title_hit(QPoint(10, w.height() - 10)))
        w._initial_split()
        before = w.splitter.sizes()[2]
        w.composer.setPlainText("one\ntwo\nthree\nfour\nfive\nsix")
        self.assertGreater(w.splitter.sizes()[2], before)
        w._set_pane_height(2, 260)
        self.assertGreaterEqual(w.splitter.sizes()[2], 200)
        self.assertEqual(w.answer_btn.height(), w.composer.minimumHeight())
        w.composer.setPlainText("estimate me")
        self.assertTrue(w.estimate_lbl.text().startswith("≈"))
        w._set_heard_height(300)
        self.assertGreaterEqual(w.splitter.sizes()[0], 200)
        w.cfg["always_on_top"] = True
        w._sync_pin()
        self.assertTrue(w.pin_btn.property("active"))

    def test_profile_manager_opens_and_creates(self):
        import tempfile
        from pathlib import Path

        from overlay.core import profiles

        w = self.window
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(profiles, "PROFILES_DIR", Path(tmp)):
            w.open_profiles()
            dialog = w.profiles_dialog
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.form_widget.isEnabled())
            dialog._new()
            self.assertEqual(dialog.list.count(), 1)
            self.assertTrue(dialog.form_widget.isEnabled())
            dialog.name.setText("Acme")
            self.assertEqual([p.name for p in profiles.list_profiles()], ["Acme"])
            dialog._use()
            self.assertEqual(w.profile_chip.text(), "Acme")
            dialog.close()

    def test_visible_window_is_announced(self):
        w = self.window
        w.capture_hidden = False
        w.set_mascot("listening")
        self.assertTrue(w.visible_banner.isVisibleTo(w))
        self.assertEqual(w.hero._shown, "visible")
        w.capture_hidden = True
        w._sync_visibility()
        self.assertFalse(w.visible_banner.isVisibleTo(w))
        self.assertEqual(w.hero._shown, "listening")
        w.capture_hidden = False
        w.set_mascot("thinking")
        self.assertEqual(w.hero._shown, "thinking")

    def test_bobo_reacts(self):
        w = self.window
        w.capture_hidden = True
        w._sync_visibility()
        w.set_mascot("sleep")
        self.assertEqual(w.bobo._shown, "idle")
        w.hero._hover = True
        w.hero._refresh()
        self.assertEqual(w.hero._shown, "curious")
        w.hero.react("wave", 50)
        self.assertEqual(w.hero._shown, "wave")
        w.hero._end_reaction()
        w.hero._hover = False
        w.hero._refresh()
        self.assertEqual(w.hero._shown, "sleep")


if __name__ == "__main__":
    unittest.main()
