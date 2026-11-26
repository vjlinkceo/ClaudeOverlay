import unittest
from types import SimpleNamespace

from overlay.claude.client import ClaudeChat
from overlay.claude.prompts import MODE_STYLES, build_recap, build_user_content
from overlay.config import MODE_PRESETS, MODEL_PROFILES, validate


class ModeTests(unittest.TestCase):
    def test_every_mode_has_a_style_and_valid_preset(self):
        self.assertEqual(set(MODE_STYLES), set(MODE_PRESETS))
        for model, effort in MODE_PRESETS.values():
            self.assertIn(model, MODEL_PROFILES)
            self.assertIn(effort, ("low", "medium", "high"))

    def test_style_goes_into_the_message(self):
        text = build_user_content(typed="2+2?", style=MODE_STYLES["solve"])[0]["text"]
        self.assertIn("<answer_style>", text)
        self.assertIn("**Answer:**", text)

    def test_invalid_mode_falls_back(self):
        self.assertEqual(validate({"mode": "poetry"})["mode"], "quick")


class RolloverTests(unittest.TestCase):
    def _chat(self) -> ClaudeChat:
        chat = ClaudeChat(None)
        chat.history = [
            {"role": "user", "content": [{"type": "text", "text": "<transcript>…</transcript>"}]},
            {
                "role": "assistant",
                "content": [
                    SimpleNamespace(type="thinking", thinking=""),
                    SimpleNamespace(type="text", text="❓ What is GIL\nA global lock in CPython."),
                ],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": "<typed_question>\nAnd asyncio?\n</typed_question>"}],
            },
            {"role": "assistant", "content": [SimpleNamespace(type="text", text="An event loop in one thread.")]},
            {"role": "user", "content": [{"type": "text", "text": "<typed_question>\nThird\n</typed_question>"}]},
            {"role": "assistant", "content": [SimpleNamespace(type="text", text="Third answer.")]},
        ]
        chat.context_tokens = 50000
        return chat

    def test_recent_exchanges(self):
        exchanges = self._chat().recent_exchanges(3)
        self.assertEqual(exchanges[0], ("❓ What is GIL", "A global lock in CPython."))
        self.assertEqual(exchanges[1], ("And asyncio?", "An event loop in one thread."))
        self.assertEqual(len(self._chat().recent_exchanges(2)), 2)

    def test_rollover_resets_and_returns_last_answers(self):
        chat = self._chat()
        exchanges = chat.rollover(2)
        self.assertEqual(chat.history, [])
        self.assertEqual(chat.context_tokens, 0)
        self.assertEqual([q for q, _ in exchanges], ["And asyncio?", "Third"])

    def test_recap_block(self):
        recap = build_recap([("❓ What is GIL", "A global lock." + "x" * 2000)])
        self.assertTrue(recap.startswith("<earlier_conversation>"))
        self.assertIn("1. ❓ What is GIL", recap)
        self.assertLess(len(recap), 1000)
        self.assertEqual(build_recap([]), "")
        text = build_user_content(typed="Shorter", recap=recap)[0]["text"]
        self.assertTrue(text.startswith("<earlier_conversation>"))


if __name__ == "__main__":
    unittest.main()
