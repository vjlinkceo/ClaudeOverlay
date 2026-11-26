import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from overlay import config, i18n
from overlay.claude import keys
from overlay.win.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey
from overlay.win.winapi import parse_geometry


class ConfigTests(unittest.TestCase):
    def test_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "model": "gpt",
                        "auto_clear_minutes": -5,
                        "effort": "extreme",
                        "ui_language": "de",
                        "window_alpha": "abc",
                        "end_silence": 10,
                        "hotkeys": {"answer": "ctrl+shift+a"},
                    }
                ),
                encoding="utf-8",
            )
            cfg = config.load_config(path)
        self.assertEqual(cfg["model"], "gpt_mini")
        self.assertEqual(cfg["auto_clear_minutes"], 0)
        self.assertEqual(cfg["effort"], "low")
        self.assertEqual(cfg["ui_language"], "en")
        self.assertEqual(cfg["window_alpha"], config.DEFAULT_CONFIG["window_alpha"])
        self.assertEqual(cfg["end_silence"], 2.0)
        self.assertEqual(cfg["hotkeys"]["answer"], "ctrl+shift+a")
        self.assertEqual(cfg["hotkeys"]["area"], "ctrl+alt+r")
        self.assertIs(cfg["hide_from_capture"], True)
        self.assertEqual(cfg["glossary"], "")

    def test_geometry(self):
        self.assertEqual(parse_geometry("709x717+-1407+434"), (709, 717, -1407, 434))
        self.assertIsNone(parse_geometry("garbage"))

    def test_hotkeys(self):
        self.assertEqual(parse_hotkey("ctrl+alt+s"), (MOD_CONTROL | MOD_ALT, ord("S")))
        self.assertEqual(parse_hotkey("Ctrl + Shift + F8"), (MOD_CONTROL | MOD_SHIFT, 0x77))
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+alt")
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+ы")
        for combo in config.DEFAULT_CONFIG["hotkeys"].values():
            parse_hotkey(combo)


class I18nTests(unittest.TestCase):
    def test_languages_have_the_same_keys_and_placeholders(self):
        en, ru = i18n.STRINGS["en"], i18n.STRINGS["ru"]
        self.assertEqual(set(en), set(ru))
        for key in en:
            self.assertEqual(set(re.findall(r"\{(\w+)", en[key])), set(re.findall(r"\{(\w+)", ru[key])), key)

    def test_every_literal_key_used_in_code_exists(self):
        """A missing key raises only when that screen opens, so the scan catches it in tests instead."""
        pattern = re.compile(r"t\(\s*\"([a-z0-9_]+)\"")
        missing = set()
        for path in Path("overlay").rglob("*.py"):
            for key in pattern.findall(path.read_text(encoding="utf-8")):
                if key not in i18n.STRINGS["en"]:
                    missing.add(f"{path.name}: {key}")
        self.assertEqual(missing, set())

    def test_switch(self):
        try:
            i18n.set_language("ru")
            self.assertEqual(i18n.t("settings"), "Настройки")
            i18n.set_language("xx")
            self.assertEqual(i18n.t("settings"), "Settings")
        finally:
            i18n.set_language("en")


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            n: os.environ.pop(n, None) for n in (*keys.KEY_NAMES.values(), "HF_TOKEN", "HTTPS_PROXY", "SSL_CERT_FILE")
        }
        # Test key routing and migration independently of the executing Windows account's master key.
        import base64

        self.enterContext(
            mock.patch(
                "overlay.ai.keys.dpapi",
                side_effect=lambda data, encrypt: base64.b64encode(data) if encrypt else base64.b64decode(data),
            )
        )

    def tearDown(self):
        for name, value in self.saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value

    def test_env_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# comment\nexport A=\"x y\" \nB=plain # note\nC=\nD='q'\n", encoding="utf-8")
            self.assertEqual(keys.read_env(env), {"A": "x y", "B": "plain", "C": "", "D": "q"})

    def test_key_moves_from_env_to_dpapi(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, enc = Path(tmp) / ".env", Path(tmp) / "api_key.dpapi"
            env.write_text("# key\nANTHROPIC_API_KEY=fake-test-key-not-real\nOTHER=1\n", encoding="utf-8")
            self.assertEqual(keys.load_api_key(env, enc), "fake-test-key-not-real")
            content = env.read_text(encoding="utf-8")
            self.assertNotIn("fake-test-key-not-real", content)
            self.assertIn("OTHER=1", content)
            self.assertEqual(keys.read_env(env)[keys.KEY_NAME], "")
            self.assertEqual(keys.load_api_key(env, enc), "fake-test-key-not-real")

    def test_placeholder_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("ANTHROPIC_API_KEY=sk-ant-...\n", encoding="utf-8")
            self.assertIsNone(keys.load_api_key(env, Path(tmp) / "x.dpapi"))

    def test_only_allowed_env_vars_applied(self):
        keys.apply_env({"HF_TOKEN": "hf-fake", "HTTPS_PROXY": "http://evil:8080", "SSL_CERT_FILE": "x.pem"})
        self.assertEqual(os.environ.get("HF_TOKEN"), "hf-fake")
        self.assertNotIn("HTTPS_PROXY", os.environ)
        self.assertNotIn("SSL_CERT_FILE", os.environ)

    def test_providers_use_separate_env_names_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            openai_enc, anthropic_enc = Path(tmp) / "openai.dpapi", Path(tmp) / "anthropic.dpapi"
            env.write_text("OPENAI_API_KEY=test-openai\nANTHROPIC_API_KEY=test-anthropic\n", encoding="utf-8")
            self.assertEqual(keys.load_api_key(env, openai_enc, provider="openai"), "test-openai")
            self.assertEqual(keys.read_env(env)["ANTHROPIC_API_KEY"], "test-anthropic")
            self.assertEqual(keys.load_api_key(env, anthropic_enc, provider="anthropic"), "test-anthropic")
            self.assertEqual(keys.load_api_key(env, openai_enc, provider="openai"), "test-openai")
            self.assertEqual(keys.load_api_key(env, anthropic_enc, provider="anthropic"), "test-anthropic")
            keys.save_api_key("replacement", env, openai_enc, provider="openai")
            self.assertEqual(keys.load_api_key(env, openai_enc, provider="openai"), "replacement")
            self.assertEqual(keys.load_api_key(env, anthropic_enc, provider="anthropic"), "test-anthropic")

    def test_system_environment_has_priority_for_each_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("OPENAI_API_KEY=file-value\n", encoding="utf-8")
            os.environ["OPENAI_API_KEY"] = "environment-value"
            self.assertEqual(keys.load_api_key(env, Path(tmp) / "key.dpapi", provider="openai"), "environment-value")


class NativeEncryptionTests(unittest.TestCase):
    def test_windows_dpapi_roundtrip(self):
        try:
            encrypted = keys.dpapi(b"unit-test-value", encrypt=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) in (2, 5, 1312):
                self.skipTest("DPAPI master key unavailable to the restricted test process")
            raise
        self.assertNotIn(b"unit-test-value", encrypted)
        self.assertEqual(keys.dpapi(encrypted, encrypt=False), b"unit-test-value")


if __name__ == "__main__":
    unittest.main()
