import unittest
from unittest import mock

from overlay.core import autoanswer
from overlay.core.autoanswer import AutoAnswer


class FakeRoot:
    """Stands in for Tk: after() jobs run only when the test advances the clock."""

    def __init__(self):
        self.now_ms = 0
        self.jobs: dict[int, tuple[int, object]] = {}
        self.next_id = 0

    def after(self, ms, fn):
        self.next_id += 1
        self.jobs[self.next_id] = (self.now_ms + ms, fn)
        return self.next_id

    def after_cancel(self, job_id):
        self.jobs.pop(job_id, None)

    def advance(self, ms):
        self.now_ms += ms
        for job_id, (due, fn) in sorted(self.jobs.items(), key=lambda kv: kv[1][0]):
            if due <= self.now_ms and job_id in self.jobs:
                del self.jobs[job_id]
                fn()


class AutoAnswerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.busy = self.talking = False
        self.fired = 0
        self.auto = AutoAnswer(self.root, lambda: self.busy, lambda: self.talking, self._fire)
        self.auto.enabled = True

    def _fire(self):
        self.fired += 1

    def test_strong_question_fires_quickly(self):
        self.auto.heard("Чем процесс отличается от потока?")
        self.root.advance(autoanswer.DELAY_STRONG_MS)
        self.assertEqual(self.fired, 1)

    def test_statement_does_not_fire(self):
        self.auto.heard("Сегодня поговорим о базах данных.")
        self.root.advance(5000)
        self.assertEqual(self.fired, 0)

    def test_waits_while_busy_or_talking(self):
        self.auto.heard("What is a closure?")
        self.busy = True
        self.root.advance(autoanswer.DELAY_STRONG_MS)
        self.assertEqual(self.fired, 0)
        self.busy = False
        self.root.advance(autoanswer.RETRY_MS)
        self.assertEqual(self.fired, 1)

    def test_long_monologue_after_question_cancels(self):
        with mock.patch.object(autoanswer.time, "monotonic", return_value=0.0):
            self.auto.heard("Почему так? Сейчас объясню.")
        self.talking = True
        with mock.patch.object(autoanswer.time, "monotonic", return_value=autoanswer.MAX_WAIT_SEC + 1):
            self.root.advance(autoanswer.DELAY_STRONG_MS)
            self.root.advance(autoanswer.RETRY_MS)
        self.assertEqual(self.fired, 0)

    def test_disabled(self):
        self.auto.enabled = False
        self.auto.heard("What is a closure?")
        self.root.advance(5000)
        self.assertEqual(self.fired, 0)


if __name__ == "__main__":
    unittest.main()
