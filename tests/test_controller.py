import copy
import time
import unittest
from unittest import mock

from overlay.claude.client import AnswerStats
from overlay.config import DEFAULT_CONFIG
from overlay.core import controller as controller_module
from overlay.core.controller import AppController, Unsent
from overlay.i18n import t
from overlay.speech.transcript import Utterance


class FakeRoot:
    """Stands in for the UI toolkit: after() jobs run only when the test advances the clock."""

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


class FakeView:
    """Records every call the controller makes."""

    def __init__(self, typed: str = ""):
        self.text = typed
        self.statuses: list[tuple[str, str]] = []
        self.mascot: list[str] = []
        self.listening: bool | None = None
        self.options: dict[str, str] = {}
        self.heard: list = []
        self.override: str | None = None
        self.attachment: str | None = None
        self.chat: list[tuple[str, str | None]] = []
        self.answer = ""
        self.finished: list = []
        self.notes: list[str] = []
        self.cleared = 0
        self.toggled = 0
        self.level = 0.0
        self.progress = None
        self.session_cost = 0.0
        self.estimate = None
        self.context = None
        self.budget = None

    def set_status(self, text, kind="info"):
        self.statuses.append((text, kind))

    def set_mascot(self, state):
        self.mascot.append(state)

    def set_listening(self, on):
        self.listening = on

    def set_option(self, key, value):
        self.options[key] = value

    def refresh_heard(self, lines, labeled, reset=False):
        self.heard = lines
        if reset:
            self.override = None

    def heard_override(self):
        return self.override

    def typed(self):
        return self.text

    def clear_typed(self):
        self.text = ""

    def restore_typed(self, text):
        self.text = text + self.text

    def set_attachment(self, label):
        self.attachment = label

    def show_user_message(self, shown):
        self.chat.append(("user", shown))

    def begin_answer(self):
        self.chat.append(("answer", None))
        self.answer = ""

    def update_answer(self, markdown):
        self.answer = markdown

    def finish_answer(self, meta, error):
        self.finished.append((meta, error))

    def note(self, text):
        self.notes.append(text)

    def clear_chat(self):
        self.chat = []
        self.cleared += 1

    def hide_for_grab(self, fn):
        fn()

    def select_region(self, on_done):
        on_done(None)

    def toggle_window(self):
        self.toggled += 1

    def toggle_compact(self):
        self.toggled += 1

    def set_voice_recording(self, on):
        self.voice_recording = on

    def set_level(self, level):
        self.level = level

    def set_progress(self, fraction):
        self.progress = fraction

    def set_session_cost(self, usd):
        self.session_cost = usd

    def set_estimate(self, costs, tokens_in, tokens_out):
        self.estimate = (costs, tokens_in, tokens_out)

    def set_context_usage(self, fraction, tokens, limit):
        self.context = (fraction, tokens, limit)

    def set_budget(self, spent, budget):
        self.budget = (spent, budget)


class FakeChat:
    """ClaudeChat without the network: `busy` is owned by the request thread, as in the real one."""

    def __init__(self):
        self.busy = False
        self.history: list = []
        self.context_tokens = 0
        self.configured = True
        self.system_extra = ""
        self.asked: list[tuple[list, str, str]] = []
        self.rollovers = 0

    def ask(self, content, profile, effort, on_event) -> bool:
        if self.busy:
            return False
        self.busy = True
        self.asked.append((content, profile, effort))
        return True

    def rollover(self, keep):
        self.rollovers += 1
        self.history, self.context_tokens = [], 0
        return []

    def reset(self):
        self.history, self.context_tokens = [], 0

    def cancel(self):
        pass


def _controller(typed: str = "", pending_image: str | None = None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    root, view, chat = FakeRoot(), FakeView(typed), FakeChat()
    c = AppController(cfg, root, view, chat=chat)
    c.pending_image = pending_image
    return c, view, chat, root


class RestoreTests(unittest.TestCase):
    def test_failed_send_is_put_back(self):
        c, view, _, _ = _controller()
        c.transcript.add(Utterance("system", "Новая фраза.", 200.0, 201.0))
        sent = [Utterance("system", "Чем процесс отличается от потока?", 100.0, 103.0)]
        c._restore_unsent(Unsent("и про GIL", sent, "IMG", "Screenshot attached"))
        self.assertEqual(view.text, "и про GIL")
        self.assertEqual([u.text for u in c.transcript.items], ["Чем процесс отличается от потока?", "Новая фраза."])
        self.assertEqual((c.pending_image, view.attachment), ("IMG", "Screenshot attached"))

    def test_newer_input_and_image_are_kept(self):
        c, view, _, _ = _controller(typed="новый вопрос", pending_image="NEW")
        c._restore_unsent(Unsent("старый вопрос", [], "OLD", "Image attached"))
        self.assertEqual(view.text, "новый вопрос")
        self.assertEqual(c.pending_image, "NEW")
        self.assertIsNone(view.attachment)

    def test_nothing_to_restore(self):
        c, view, _, _ = _controller()
        c._restore_unsent(None)
        self.assertEqual((view.text, c.transcript.items, view.attachment), ("", [], None))


class InFlightTests(unittest.TestCase):
    def test_busy_until_the_done_event_is_handled(self):
        c, view, chat, _ = _controller()
        unsent = Unsent("типизированный вопрос", [], None, "")
        self.assertTrue(c._start_answer([], "shown", "sonnet", "low", unsent))
        self.assertTrue(c.answering)
        self.assertTrue(c._busy())

        # The request thread finished and dropped chat.busy, but its done event is still queued:
        # a send here would bump the generation and discard that event with the unsent data.
        chat.busy = False
        self.assertTrue(c._busy())
        self.assertEqual(view.statuses[-1], (t("still_answering"), "busy"))

        c._on_done("connection lost", AnswerStats(model="Sonnet"))
        self.assertFalse(c.answering)
        self.assertFalse(c._in_flight())
        self.assertEqual(view.text, "типизированный вопрос")
        self.assertEqual(view.finished, [(None, "connection lost")])
        self.assertEqual(view.mascot[-1], "error")

    def test_busy_while_a_cancelled_request_is_still_running(self):
        c, _, chat, _ = _controller()
        chat.busy = True
        self.assertTrue(c._in_flight())

    def test_nothing_is_shown_or_taken_when_the_request_cannot_start(self):
        c, view, chat, _ = _controller()
        chat.busy = True
        self.assertFalse(c._start_answer([], "shown", "sonnet", "low", Unsent("q", [], None, "")))
        self.assertFalse(c.answering)
        self.assertIsNone(c.unsent)
        self.assertEqual(view.chat, [])
        self.assertEqual(c.answer_gen, 0)
        self.assertEqual(view.statuses[-1], (t("still_answering"), "busy"))


class SendTests(unittest.TestCase):
    def test_send_takes_the_typed_text_and_the_transcript(self):
        c, view, chat, _ = _controller(typed="и про GIL")
        c.transcript.add(Utterance("system", "Чем процесс отличается от потока?", 100.0, 103.0))
        c.send()
        self.assertEqual(len(chat.asked), 1)
        text = chat.asked[0][0][-1]["text"]
        self.assertIn("<transcript", text)
        self.assertIn("<typed_question>\nи про GIL", text)
        self.assertEqual(view.chat[0][0], "user")
        self.assertIn("«", view.chat[0][1])
        self.assertEqual(view.text, "")
        self.assertEqual(c.transcript.items, [])
        self.assertEqual(c.unsent.typed, "и про GIL")
        self.assertEqual(view.statuses[-1][1], "busy")
        self.assertEqual(view.mascot[-1], "thinking")

    def test_edited_heard_text_replaces_the_transcript(self):
        c, view, chat, _ = _controller()
        c.transcript.add(Utterance("system", "Misheard words", 100.0, 103.0))
        view.override = "Corrected words"
        c.send()
        text = chat.asked[0][0][-1]["text"]
        self.assertIn("Corrected words", text)
        self.assertNotIn("Misheard", text)

    def test_nothing_to_send(self):
        c, view, chat, _ = _controller()
        c.send()
        self.assertEqual(chat.asked, [])
        self.assertEqual(view.statuses[-1], (t("nothing_to_send"), "info"))

    def test_streamed_answer_is_rendered_on_the_tick_and_finished_with_stats(self):
        c, view, _, root = _controller(typed="2+2?")
        c.send()
        gen = c.answer_gen
        c._on_claude(gen, "thinking", None)
        c._on_claude(gen, "delta", "❓ 2+2?\n")
        c._on_claude(gen, "delta", "4")
        self.assertEqual(view.answer, "")
        c._tick()
        self.assertEqual(view.answer, "❓ 2+2?\n4")
        self.assertEqual(view.mascot[-1], "talking")
        stats = AnswerStats(model="Sonnet", first_token=0.5, total=1.2)
        c._on_claude(gen, "done", (None, stats))
        self.assertIs(view.finished[-1][0].stats, stats)
        self.assertEqual(view.mascot[-1], "happy")
        root.advance(controller_module.HAPPY_MS)
        self.assertEqual(view.mascot[-1], "sleep")
        self.assertIsNone(c.unsent)

    def test_stale_events_are_ignored(self):
        c, view, _, _ = _controller(typed="q")
        c.send()
        c._on_claude(c.answer_gen - 1, "delta", "old")
        self.assertEqual(c.answer_raw, "")

    def test_long_chat_is_restarted_with_a_note(self):
        c, view, chat, _ = _controller(typed="next")
        chat.history = [{"role": "user", "content": "x"}]
        chat.context_tokens = c.cfg["max_history_tokens"] + 1
        c.send()
        self.assertEqual(chat.rollovers, 1)
        self.assertEqual(view.notes, [t("chat_restarted")])

    def test_followup_needs_a_conversation(self):
        c, view, chat, _ = _controller()
        c.followup("shorter")
        self.assertEqual(chat.asked, [])
        self.assertEqual(view.statuses[-1][0], t("nothing_to_follow"))
        chat.history = [{"role": "user", "content": "x"}]
        c.followup("deeper")
        self.assertEqual(chat.asked[0][1:], ("gpt_quality", "high"))


class HousekeepingTests(unittest.TestCase):
    def test_idle_clear_wipes_the_chat(self):
        c, view, chat, root = _controller()
        c.cfg["auto_clear_minutes"] = 1
        c.messages = 1
        chat.history = [{"role": "user", "content": "x"}]
        with mock.patch.object(controller_module.time, "monotonic", return_value=c.last_activity + 61):
            c._check_idle()
        self.assertEqual(view.cleared, 1)
        self.assertEqual(chat.history, [])
        self.assertEqual(view.statuses[-1][0], t("auto_cleared", minutes=1))
        self.assertEqual(len(root.jobs), 1)

    def test_mode_preset_sets_model_and_effort(self):
        c, view, _, _ = _controller()
        c.cfg["mode"] = "solve"
        c.on_mode_change()
        self.assertEqual((c.cfg["model"], c.cfg["effort"]), ("gpt_quality", "high"))
        self.assertEqual(view.options, {"model": "gpt_quality", "effort": "high"})

    def test_new_chat_resets_everything(self):
        c, view, chat, _ = _controller(typed="q")
        c.send()
        c.pending_image = "IMG"
        c.new_chat()
        self.assertFalse(c.answering)
        self.assertEqual((c.messages, c.pending_image, view.cleared), (0, None, 1))
        self.assertEqual(view.mascot[-1], "sleep")

    def test_hotkeys_dispatch(self):
        c, view, _, _ = _controller()
        c.on_hotkey("toggle_window")
        self.assertEqual(view.toggled, 1)


class SignalTests(unittest.TestCase):
    def test_level_events_reach_the_view(self):
        from overlay.speech.audio import SpeechEvent

        c, view, _, root = _controller()
        c.ui.start()
        c.listeners["system"] = mock.Mock()
        c._on_speech_event(SpeechEvent("level", "system", 0, level=0.7))
        root.advance(20)
        self.assertEqual(view.level, 0.7)
        c.stop_listen()
        self.assertEqual(view.level, 0.0)

    def test_download_progress_is_polled_until_ready(self):
        c, view, _, root = _controller()
        c.transcriber = mock.Mock(downloading_model="small", device=None)
        with mock.patch.object(controller_module, "download_progress", return_value=0.25):
            c._on_stt_status("downloading…", "downloading")
            root.advance(controller_module.PROGRESS_POLL_MS)
        self.assertEqual(view.progress, 0.25)
        self.assertEqual(view.statuses[-1], ("downloading…", "busy"))
        c._on_stt_status(None, "ready")
        self.assertIsNone(view.progress)
        self.assertTrue(c.stt_ready)

    def test_cost_accumulates_over_the_session(self):
        c, view, _, _ = _controller(typed="q")
        c.send()
        stats = AnswerStats(model="Sonnet 5.5", model_id="claude-sonnet-5-5", output_tokens=100_000)
        c._on_claude(c.answer_gen, "done", (None, stats))
        self.assertAlmostEqual(view.finished[-1][0].cost, 1.0)
        self.assertAlmostEqual(view.session_cost, 1.0)
        view.text = "again"
        c.send()
        c._on_claude(c.answer_gen, "done", (None, stats))
        self.assertAlmostEqual(view.session_cost, 2.0)


class RecapTests(unittest.TestCase):
    def test_recap_of_the_last_minutes(self):
        c, view, chat, _ = _controller()
        c.summarize(5)
        self.assertEqual(chat.asked, [])
        self.assertEqual(view.statuses[-1][0], t("nothing_heard_yet"))
        c._on_final("system", Utterance("system", "We agreed to ship on Friday.", time.time() - 10, time.time() - 8))
        c.summarize(5)
        content, profile, effort = chat.asked[-1]
        self.assertIn('<transcript_window minutes="5"', content[0]["text"])
        self.assertIn("ship on Friday", content[0]["text"])
        self.assertEqual(effort, "low")
        self.assertEqual(view.chat[0], ("user", t("recap_shown", minutes=5)))
        c._on_claude(c.answer_gen, "done", (None, AnswerStats(model="Sonnet 5.5")))
        self.assertEqual(view.finished[-1][0].kind, "summary")

    def test_call_summary_uses_openai_and_survives_sending_questions(self):
        c, view, chat, _ = _controller()
        c._on_final("system", Utterance("system", "First topic.", time.time() - 100, time.time() - 98))
        view.text = "question"
        c.send()
        c._on_claude(c.answer_gen, "done", (None, AnswerStats(model="Sonnet 5.5")))
        chat.busy = False
        self.assertEqual(c.transcript.items, [])
        c.summarize(None)
        content, profile, effort = chat.asked[-1]
        self.assertIn("First topic.", content[0]["text"])
        self.assertEqual((profile, effort), ("gpt_mini", "medium"))
        c._on_claude(c.answer_gen, "done", (None, AnswerStats(model="Sonnet 5.5")))
        self.assertEqual(view.finished[-1][0].kind, "summary")
        c.new_chat()
        self.assertEqual(c.meeting.items, [])


class ProfileTests(unittest.TestCase):
    def test_switching_profiles_restarts_the_chat_and_applies_settings(self):
        import tempfile
        from pathlib import Path

        from overlay.core import profiles

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(profiles, "PROFILES_DIR", Path(tmp)):
            profile = profiles.create("Acme", {"mode": "interview", "context": "Python"}, Path(tmp))
            profile.notes = "Senior dev"
            profiles.save(profile, Path(tmp))
            c, view, chat, _ = _controller(typed="q")
            c.send()
            c._on_claude(c.answer_gen, "done", (None, AnswerStats(model="Sonnet 5.5")))
            chat.busy = False
            chat.history = [{"role": "user", "content": "x"}]
            c.set_profile(profile.slug)
            self.assertEqual(chat.rollovers, 1)
            self.assertIn("Senior dev", chat.system_extra)
            self.assertEqual((c.cfg["mode"], c.cfg["context"], c.cfg["profile"]), ("interview", "Python", profile.slug))
            self.assertEqual(view.options["mode"], "interview")
            self.assertIn("Acme", view.notes[-1])
            c.set_profile(None)
            self.assertEqual((chat.system_extra, c.cfg["profile"]), ("", ""))


class MeterTests(unittest.TestCase):
    def test_estimate_follows_the_question(self):
        c, view, chat, _ = _controller()
        c.update_meters()
        costs, tokens_in, tokens_out = view.estimate
        base = costs["sonnet"]
        self.assertGreater(base, 0)
        self.assertGreater(costs["opus"], costs["sonnet"])
        self.assertLess(costs["haiku"], costs["sonnet"])
        view.text = "x" * 4000
        c.typed_changed()
        self.assertGreater(view.estimate[0]["sonnet"], base)
        c._attach("IMG", "label", 1920 * 1080)
        self.assertGreater(view.estimate[1], tokens_in + 2000)
        c.cfg["effort"] = "high"
        c.set_option("mode", "solve")
        self.assertGreater(view.estimate[2], tokens_out)

    def test_context_meter_and_budget(self):
        c, view, chat, _ = _controller(typed="q")
        c.cfg["budget_usd"] = 1.5
        c.cfg["max_history_tokens"] = 10000
        with mock.patch.object(controller_module, "save_config", lambda cfg: None):
            c.send()
            stats = AnswerStats(model="Sonnet 5.5", model_id="claude-sonnet-5-5", output_tokens=100_000)
            chat.context_tokens = 2500
            c._on_claude(c.answer_gen, "done", (None, stats))
            self.assertEqual(view.context, (0.25, 2500, 10000))
            self.assertEqual(view.budget, (1.0, 1.5))
            self.assertNotEqual(view.statuses[-1][1], "error")
            chat.busy = False
            view.text = "again"
            c.send()
            c._on_claude(c.answer_gen, "done", (None, stats))
            self.assertAlmostEqual(c.cfg["spent_usd"], 2.0)
            self.assertEqual(view.statuses[-1][1], "error")
            self.assertIn("$2.00", view.statuses[-1][0])
            c.cfg["spent_month"] = "1999-01"
            chat.busy = False
            view.text = "new month"
            c.send()
            c._on_claude(c.answer_gen, "done", (None, stats))
            self.assertAlmostEqual(c.cfg["spent_usd"], 1.0)
            self.assertEqual(c.cfg["spent_month"], time.strftime("%Y-%m"))


if __name__ == "__main__":
    unittest.main()
