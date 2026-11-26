import unittest

from overlay.core.uiqueue import UiQueue


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


class UiQueueTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.ticks = 0
        self.tick_fails = False
        self.errors: list[Exception] = []
        self.ui = UiQueue(self.root, on_tick=self._tick, on_error=self.errors.append, poll_ms=20)
        self.ui.start()

    def _tick(self):
        self.ticks += 1
        if self.tick_fails:
            raise RuntimeError("tick failed")

    def test_failing_callback_is_reported_and_polling_goes_on(self):
        ran: list[str] = []

        def boom():
            raise ValueError("bad callback")

        self.ui.post(boom)
        self.ui.post(ran.append, "same poll")
        with self.assertLogs("overlay.core.uiqueue", level="ERROR") as logs:
            self.root.advance(20)
        self.assertEqual(ran, ["same poll"])
        self.assertEqual([type(e) for e in self.errors], [ValueError])
        self.assertTrue(any("boom" in line for line in logs.output))

        self.ui.post(ran.append, "next poll")
        self.root.advance(20)
        self.assertEqual(ran, ["same poll", "next poll"])
        self.assertEqual(self.ticks, 2)

    def test_failing_tick_does_not_stop_polling(self):
        self.tick_fails = True
        with self.assertLogs("overlay.core.uiqueue", level="ERROR"):
            self.root.advance(20)
        self.assertEqual(len(self.errors), 1)
        self.tick_fails = False
        self.root.advance(20)
        self.assertEqual(self.ticks, 2)

    def test_stop_cancels_polling(self):
        self.ui.stop()
        self.ui.post(lambda: None)
        self.root.advance(100)
        self.assertEqual(self.ticks, 0)
        self.assertEqual(self.root.jobs, {})

    def test_native_wakeup_delivers_before_poll_and_coalesces(self):
        wakeups, calls = [], []
        self.root.wake = wakeups.append
        ui = UiQueue(self.root, on_tick=lambda: None, on_error=self.errors.append)
        ui.start()
        ui.post(calls.append, 1)
        ui.post(calls.append, 2)
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(calls, [])
        wakeups.pop()()
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.root.now_ms, 0)
        ui.stop()

    def test_stopped_queue_ignores_pending_native_wakeup(self):
        wakeups, calls = [], []
        self.root.wake = wakeups.append
        ui = UiQueue(self.root, on_tick=lambda: None, on_error=self.errors.append)
        ui.start()
        ui.post(calls.append, 1)
        ui.stop()
        wakeups.pop()()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
