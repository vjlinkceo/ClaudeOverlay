import copy
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from test_controller import _controller

from overlay.config import DEFAULT_CONFIG, validate
from overlay.core.regions import region_on_screen, validate_region
from overlay.speech import audio, voice
from overlay.speech.audio import RATE, Listener, SpeechEvent
from overlay.speech.stt import Transcriber, VoiceTask
from overlay.win import hotkeys
from overlay.win.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, combination_down

REGION = {"left": -1700, "top": 100, "width": 600, "height": 400}
MONITORS = [{"left": -1920, "top": 0, "width": 1920, "height": 1080}]


class RegionTests(unittest.TestCase):
    def test_validation_and_old_config_defaults(self):
        self.assertEqual(validate_region(REGION), REGION)
        for bad in (None, [], {}, {**REGION, "width": -1}, {**REGION, "top": True}, {**REGION, "left": "10"}):
            self.assertIsNone(validate_region(bad))
        self.assertTrue(region_on_screen(REGION, MONITORS))
        self.assertFalse(region_on_screen(REGION, [{"left": 0, "top": 0, "width": 1920, "height": 1080}]))
        cfg = validate(copy.deepcopy(DEFAULT_CONFIG))
        self.assertIsNone(cfg["saved_area"])
        self.assertFalse(cfg["compact_mode"])
        self.assertIn("voice", cfg["hotkeys"])

    def test_selection_saves_and_next_capture_reuses_it(self):
        c, view, _, _ = _controller()
        with (
            mock.patch.object(view, "select_region", side_effect=lambda done: done(REGION)) as select,
            mock.patch.object(c, "_grab_and_send") as capture,
            mock.patch("overlay.core.controller.save_config") as save,
            mock.patch("overlay.core.controller.screen_monitors", return_value=MONITORS),
        ):
            c.area_and_ask()
            self.assertEqual(c.cfg["saved_area"], REGION)
            save.assert_called_once()
            c.area_and_ask()
        select.assert_called_once()
        self.assertEqual(capture.call_count, 2)

    def test_unplugged_display_never_falls_back_to_full_screen(self):
        c, view, _, _ = _controller()
        c.cfg["saved_area"] = REGION
        with (
            mock.patch("overlay.core.controller.screen_monitors", return_value=[]),
            mock.patch.object(c, "_grab_and_send") as capture,
            mock.patch.object(view, "select_region") as select,
        ):
            c.area_and_ask()
        capture.assert_not_called()
        select.assert_not_called()
        self.assertEqual(view.statuses[-1][1], "error")

    def test_cancelling_new_selection_keeps_old_region(self):
        c, _, _, _ = _controller()
        c.cfg["saved_area"] = REGION
        with mock.patch("overlay.core.controller.save_config") as save:
            c._on_area_selected(None)
        save.assert_not_called()
        self.assertEqual(c.cfg["saved_area"], REGION)


class VoiceControllerTests(unittest.TestCase):
    def setUp(self):
        self.c, self.view, self.chat, self.root = _controller("Unfinished draft")
        self.enterContext(mock.patch("overlay.core.controller.audio.ensure_loaded", return_value=True))
        self.recorder_class = self.enterContext(mock.patch("overlay.core.controller.VoiceRecorder"))
        self.transcriber_class = self.enterContext(mock.patch("overlay.core.controller.Transcriber"))

    def test_release_transcribes_then_sends_without_overwriting_draft(self):
        c = self.c
        c.start_voice()
        gen = c._voice_gen
        self.assertTrue(c._in_flight())
        c.finish_voice()
        self.recorder_class.return_value.finish.assert_called_once()
        c._on_voice_audio(gen, np.ones(RATE, dtype=np.float32), None)
        self.assertFalse(self.view.voice_recording)
        self.assertTrue(c._voice_pending)
        c._on_voice_text(gen, "Spoken question?", None)
        self.assertEqual(len(self.chat.asked), 1)
        prompt = self.chat.asked[0][0][-1]["text"]
        self.assertIn("Spoken question?", prompt)
        self.assertNotIn("Unfinished draft", prompt)
        self.assertEqual(self.view.text, "Unfinished draft")
        self.assertFalse(c._voice_pending)
        self.transcriber_class.return_value.submit_voice.assert_called_once()

    def test_stop_discards_late_recording_and_transcription(self):
        c = self.c
        c.start_voice()
        gen = c._voice_gen
        c.stop_answer()
        self.recorder_class.return_value.cancel.assert_called_once()
        c._on_voice_audio(gen, np.ones(RATE, dtype=np.float32), None)
        c._on_voice_text(gen, "Do not send", None)
        self.assertEqual(self.chat.asked, [])
        self.assertFalse(c._voice_pending)

    def test_new_chat_cancels_pending_dictation(self):
        self.c.start_voice()
        gen = self.c._voice_gen
        self.c.new_chat()
        self.c._on_voice_text(gen, "Old question", None)
        self.assertEqual(self.chat.asked, [])

    def test_busy_chat_or_missing_key_does_not_record(self):
        self.chat.busy = True
        self.c.start_voice()
        self.chat.busy = False
        self.chat.configured = False
        self.c.start_voice()
        self.recorder_class.assert_not_called()

    def test_silence_or_microphone_error_does_not_send(self):
        self.c.start_voice()
        self.c._on_voice_audio(self.c._voice_gen, None, None)
        self.assertFalse(self.c._voice_pending)
        self.assertEqual(self.chat.asked, [])
        self.c.start_voice()
        self.c._on_voice_audio(self.c._voice_gen, None, "No microphone")
        self.assertEqual(self.view.statuses[-1], ("No microphone", "error"))

    def test_transcriber_failure_cancels_capture(self):
        self.c.start_voice()
        self.c._on_stt_status("Model failed", "error")
        self.assertFalse(self.c._voice_pending)
        self.assertIsNone(self.c.voice_recorder)


class VoiceCaptureTests(unittest.TestCase):
    def recorder(self, amplitude=0.1, frames=10):
        results = []
        capture = voice.VoiceRecorder("", lambda: None, lambda samples, error: results.append((samples, error)))
        device = mock.MagicMock()
        device.recorder.return_value.__enter__.return_value = device
        remaining = [frames]

        def record(numframes):
            remaining[0] -= 1
            if remaining[0] == 0:
                capture.finish()
            return np.full((numframes, 2), amplitude, dtype=np.float32)

        device.record.side_effect = record
        sc = mock.Mock()
        sc.default_microphone.return_value = device
        self.enterContext(mock.patch.object(audio, "sc", sc))
        return capture, results, sc

    def test_release_returns_mono_samples_and_skips_device_enumeration(self):
        capture, results, sc = self.recorder()
        capture.run()
        samples, error = results[0]
        self.assertIsNone(error)
        self.assertEqual(samples.shape, (5120,))
        self.assertEqual(samples.dtype, np.float32)
        sc.all_microphones.assert_not_called()

    def test_cancel_before_open_never_opens_microphone(self):
        capture, results, sc = self.recorder()
        capture.cancel()
        capture.run()
        self.assertEqual(results, [(None, None)])
        sc.default_microphone.assert_not_called()

    def test_silence_is_not_submitted(self):
        capture, results, _ = self.recorder(amplitude=0)
        capture.run()
        self.assertEqual(results, [(None, None)])

    def test_duration_limit_stops_even_while_hotkey_is_held(self):
        capture, results, _ = self.recorder(frames=100)
        with mock.patch.object(voice, "MAX_SECONDS", 1):
            capture.run()
        self.assertIsNone(results[0][0])
        self.assertTrue(results[0][1])

    def test_regular_listener_skips_enumeration_for_default_device(self):
        listener = Listener("mic", 0.5, 0.6, lambda *_: None, lambda *_: None)
        with mock.patch.object(audio, "sc") as sc:
            listener._chosen()
            sc.all_microphones.assert_not_called()


class VoiceTranscriberTests(unittest.TestCase):
    def transcriber(self):
        return Transcriber(copy.deepcopy(DEFAULT_CONFIG), lambda *_: None, lambda *_: None, lambda *_: None)

    def test_voice_jobs_take_priority_over_passive_speech(self):
        transcriber = self.transcriber()
        order = []
        transcriber._load = mock.Mock(return_value=True)

        def handle(kind):
            order.append(kind)
            if len(order) == 2:
                transcriber.stop()

        transcriber._handle_voice = lambda _: handle("voice")
        transcriber._handle_final = lambda _: handle("passive")
        transcriber.submit(SpeechEvent("final", "mic", 1))
        transcriber.submit_voice(np.ones(RATE), lambda *_: None, threading.Event())
        transcriber.run()
        self.assertEqual(order, ["voice", "passive"])

    def test_cancelled_voice_job_does_not_transcribe(self):
        transcriber = self.transcriber()
        event = threading.Event()
        event.set()
        with mock.patch.object(transcriber, "_transcribe") as recognize:
            transcriber._handle_voice(VoiceTask(np.ones(RATE), mock.Mock(), event))
        recognize.assert_not_called()

    def test_voice_errors_are_delivered_to_controller(self):
        transcriber = self.transcriber()
        done = mock.Mock()
        with (
            mock.patch.object(transcriber, "_transcribe", side_effect=RuntimeError("Test failure")),
            self.assertLogs("overlay.speech.stt", level="ERROR"),
        ):
            transcriber._handle_voice(VoiceTask(np.ones(RATE), done, threading.Event()))
        self.assertEqual(done.call_args.args[0], "")
        self.assertIn("Test failure", done.call_args.args[1])


class HoldHotkeyTests(unittest.TestCase):
    def test_release_of_main_key_or_modifier_finishes_hold(self):
        held = {ord("V"), 0x11, 0x12, 0x10}

        def state(vk):
            return 0x8000 if vk in held else 0

        mods = MOD_CONTROL | MOD_ALT | MOD_SHIFT
        self.assertTrue(combination_down(mods, ord("V"), state))
        held.remove(0x11)
        self.assertFalse(combination_down(mods, ord("V"), state))
        held.add(0x11)
        held.remove(ord("V"))
        self.assertFalse(combination_down(mods, ord("V"), state))

    def test_native_message_loop_emits_press_and_release(self):
        user32 = mock.Mock()
        user32.RegisterHotKey.return_value = True
        user32.SetTimer.return_value = 123
        user32.GetAsyncKeyState.return_value = 0
        messages = [(hotkeys.WM_HOTKEY, 1), (hotkeys.WM_TIMER, 123)]

        def get_message(pointer, *_):
            if not messages:
                return 0
            pointer._obj.message, pointer._obj.wParam = messages.pop(0)
            return 1

        user32.GetMessageW.side_effect = get_message
        events, errors = [], []
        native = SimpleNamespace(user32=user32, kernel32=mock.Mock())
        native.kernel32.GetCurrentThreadId.return_value = 42
        worker = hotkeys.HotkeyThread({"voice": "ctrl+alt+v"}, events.append, errors.append)
        with mock.patch.object(hotkeys.ctypes, "windll", native):
            worker.run()
        self.assertEqual(events, ["voice_start", "voice_stop"])
        self.assertEqual(errors, [])
        user32.KillTimer.assert_called_once_with(None, 123)
        user32.UnregisterHotKey.assert_called_once()
