import unittest

from overlay.claude.prompts import build_summary_content
from overlay.core.meeting import MeetingLog
from overlay.speech.transcript import Utterance


class MeetingLogTests(unittest.TestCase):
    def _log(self) -> MeetingLog:
        log = MeetingLog()
        log.append(Utterance("system", "Intro of the meeting.", 1000.0, 1003.0))
        log.append(Utterance("system", "Second sentence.", 1003.5, 1005.0))
        log.append(Utterance("mic", "My reply.", 1100.0, 1102.0))
        log.append(Utterance("system", "Late question?", 1500.0, 1502.0))
        return log

    def test_window_by_minutes(self):
        log = self._log()
        recent = log.text(5, labeled=True, max_chars=10000, now=1600.0)
        self.assertIn("Late question?", recent)
        self.assertNotIn("Intro", recent)
        everything = log.text(None, labeled=True, max_chars=10000, now=1600.0)
        self.assertIn("Intro of the meeting. Second sentence.", everything)
        self.assertIn("Them:", everything)
        self.assertIn("Me:", everything)

    def test_copies_are_stored_and_merging_does_not_touch_them(self):
        log = self._log()
        log.text(None, labeled=False, max_chars=10000, now=1600.0)
        self.assertEqual([u.text for u in log.items][:2], ["Intro of the meeting.", "Second sentence."])

    def test_too_long_keeps_the_newest(self):
        log = self._log()
        text = log.text(None, labeled=False, max_chars=60, now=1600.0)
        self.assertIn("Late question?", text)
        self.assertIn("omitted", text)

    def test_wipe(self):
        log = self._log()
        log.wipe()
        self.assertEqual(log.text(None, labeled=False, max_chars=100), "")


class SummaryPromptTests(unittest.TestCase):
    def test_window_prompt(self):
        content = build_summary_content("[10:00:00] Them: hello", labeled=True, minutes=5, answer_language="ru")
        text = content[0]["text"]
        self.assertIn('<transcript_window minutes="5"', text)
        self.assertIn("speakers labeled", text)
        self.assertIn("last 5 minutes", text)
        self.assertIn("Reply in Russian", text)

    def test_call_summary_prompt(self):
        text = build_summary_content("[10:00:00] hello", labeled=False, minutes=None, answer_language="auto")[0]["text"]
        self.assertIn('minutes="all"', text)
        self.assertIn("action items", text.lower())
        self.assertNotIn("Reply in", text)


if __name__ == "__main__":
    unittest.main()
