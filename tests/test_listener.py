import unittest
from unittest import mock

import numpy as np

from overlay.speech import audio
from overlay.speech.audio import Listener

DEVICE_LOST = RuntimeError("Error 0x88890004")


class FakeVAD:
    def reset(self):
        pass

    def __call__(self, frame):
        return 0.9


class FakeDevice:
    """Records `frames` frames of speech, then breaks (dies=True) or stops the listener."""

    def __init__(self, name: str, frames: int = 0, dies: bool = False, opens: bool = True):
        self.name, self.frames, self.dies, self.opens = name, frames, dies, opens
        self.listener: Listener | None = None

    def recorder(self, samplerate):
        if not self.opens:
            raise DEVICE_LOST
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        if self.frames == 0:
            if self.dies:
                raise DEVICE_LOST
            self.listener.stop_event.set()
        self.frames -= 1
        return np.full((numframes, 2), 0.1, dtype=np.float32)


class ScriptedListener(Listener):
    REOPEN_DELAY_SEC = 0.0

    def __init__(self, devices: list[FakeDevice], events: list, errors: list):
        super().__init__("system", 0.5, 0.6, on_event=events.append, on_error=errors.append)
        self.devices = devices
        self.opened: list[str] = []

    def _open_device(self):
        device = self.devices.pop(0) if self.devices else FakeDevice("gone", opens=False)
        device.listener = self
        self.opened.append(device.name)
        return device, device.name

    def _default_id(self):
        return self.opened[-1]


class DeviceLossTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(audio, "StreamingVAD", FakeVAD)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events, self.errors = [], []

    def _run(self, listener: Listener) -> list[str]:
        with self.assertLogs("overlay.speech.audio", level="INFO") as logs:
            listener.run()
        return logs.output

    def test_reopens_after_device_is_lost(self):
        listener = ScriptedListener(
            [FakeDevice("headset", frames=40, dies=True), FakeDevice("speakers", frames=20)], self.events, self.errors
        )
        logs = self._run(listener)
        self.assertEqual(listener.opened, ["headset", "speakers"])
        self.assertEqual(self.errors, [])
        self.assertTrue(any("reopening" in line for line in logs))
        finals = [ev for ev in self.events if ev.kind == "final"]
        self.assertEqual(len(finals), 2)
        self.assertIsNotNone(finals[0].audio)

    def test_device_that_never_worked_fails_at_once(self):
        listener = ScriptedListener([FakeDevice("broken", opens=False)], self.events, self.errors)
        self._run(listener)
        self.assertEqual(listener.opened, ["broken"])
        self.assertEqual(len(self.errors), 1)

    def test_gives_up_when_no_device_comes_back(self):
        listener = ScriptedListener([FakeDevice("headset", frames=5, dies=True)], self.events, self.errors)
        listener.REOPEN_TIMEOUT_SEC = 0.05
        listener.REOPEN_DELAY_SEC = 0.01
        self._run(listener)
        self.assertGreater(len(listener.opened), 2)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("0x88890004", self.errors[0])


if __name__ == "__main__":
    unittest.main()
