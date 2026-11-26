"""Exercise the real SDK against an in-memory HTTP transport; no keys or paid calls."""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx
import openai

from overlay.ai.base import AnswerStats
from overlay.ai.client import AIChat
from overlay.ai.openai_client import API_BASE_URL, OpenAIChat, describe_error, response_input
from overlay.ai.prompts import build_user_content
from overlay.config import mode_preset
from overlay.core.pricing import estimate_cost, estimate_question


def sse(*, text="Hello", status="completed", refusal=False):
    part = (
        {"type": "refusal", "refusal": "Declined"}
        if refusal
        else {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
    )
    item = {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [part]}
    final = {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": "gpt-5.4-mini",
        "status": status,
        "output": [item],
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 10,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 110,
        },
    }
    events = [
        ("response.created", {"response": {**final, "status": "in_progress", "output": []}}),
        ("response.output_item.added", {"output_index": 0, "item": {**item, "status": "in_progress", "content": []}}),
        (
            "response.content_part.added",
            {
                "item_id": "msg_test",
                "output_index": 0,
                "content_index": 0,
                "part": {**part, "refusal" if refusal else "text": ""},
            },
        ),
    ]
    if not refusal:
        events.append(
            (
                "response.output_text.delta",
                {"item_id": "msg_test", "output_index": 0, "content_index": 0, "delta": text},
            )
        )
    events += [
        ("response.content_part.done", {"item_id": "msg_test", "output_index": 0, "content_index": 0, "part": part}),
        ("response.output_item.done", {"output_index": 0, "item": item}),
        ("response.completed" if status == "completed" else "response.incomplete", {"response": final}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps({'type': name, 'sequence_number': i, **data})}\n\n"
        for i, (name, data) in enumerate(events)
    )


class OpenAIClientTests(unittest.TestCase):
    def setUp(self):
        self.chat = OpenAIChat(None)
        self.requests = []
        self.events = []

    def tearDown(self):
        if self.chat.client:
            self.chat.client.close()

    def connect(self, response):
        def handle(request):
            self.requests.append(request)
            return response

        self.chat._client = openai.OpenAI(
            api_key="fake-unit-test",
            base_url=API_BASE_URL,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )

    def run_request(self, **kwargs):
        self.chat._run(
            self.chat.history,
            build_user_content(typed="Question", image_b64="aW1hZ2U="),
            "gpt_mini",
            "low",
            lambda kind, data: self.events.append((kind, data)),
            **kwargs,
        )
        return self.events[-1][1]

    def test_stream_images_history_instructions_and_disjoint_usage(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse()))
        self.chat.system_extra = "Profile facts"
        error, stats = self.run_request()
        self.assertIsNone(error)
        self.assertEqual([data for kind, data in self.events if kind == "delta"], ["Hello"])
        self.assertIsNotNone(stats.first_token)
        self.assertEqual((stats.input_tokens, stats.cache_read, stats.output_tokens), (60, 40, 10))
        self.assertEqual(stats.context_tokens, 110)
        self.assertEqual(self.chat.context_tokens, 110)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(str(self.requests[0].url), API_BASE_URL + "/responses")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertIn("Profile facts", payload["instructions"])
        self.assertEqual(
            payload["input"][0]["content"][0], {"type": "input_image", "image_url": "data:image/jpeg;base64,aW1hZ2U="}
        )
        self.assertEqual(self.chat.recent_exchanges(1), [("Question", "Hello")])
        self.assertAlmostEqual(estimate_cost(stats), (60 * 0.75 + 40 * 0.075 + 10 * 4.5) / 1e6)
        self.events.clear()
        self.run_request()
        payload = json.loads(self.requests[1].content)
        self.assertEqual(payload["input"][1], {"role": "assistant", "content": "Hello"})

    def test_auth_failure_removes_failed_turn(self):
        self.connect(httpx.Response(401, json={"error": {"message": "Invalid key", "type": "invalid_request_error"}}))
        error, _ = self.run_request()
        self.assertIn("API", error)
        self.assertEqual(self.chat.history, [])
        self.assertFalse(self.chat.busy)

    def test_incomplete_answer_is_kept_with_warning(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(status="incomplete")))
        error, _ = self.run_request()
        self.assertIsNone(error)
        self.assertEqual(len([x for x in self.events if x[0] == "delta"]), 2)
        self.assertEqual(len(self.chat.history), 2)

    def test_refusal_is_not_recorded_as_success(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(refusal=True)))
        error, stats = self.run_request()
        self.assertTrue(error)
        self.assertEqual(self.chat.history, [])
        self.assertEqual(stats.output_tokens, 10)

    def test_cancellation_rolls_back_the_current_question(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse()))

        def event(kind, data):
            self.events.append((kind, data))
            if kind == "delta":
                self.chat.cancel_event.set()

        self.chat._run(self.chat.history, [{"type": "text", "text": "Question"}], "gpt_mini", "low", event)
        error, stats = self.events[-1][1]
        self.assertTrue(error)
        self.assertTrue(stats.cancelled)
        self.assertEqual(self.chat.history, [])

    def test_no_key_does_not_add_history(self):
        error, _ = self.run_request()
        self.assertTrue(error)
        self.assertEqual(self.chat.history, [])

    def test_stream_without_terminal_event_rolls_back(self):
        body = sse().split("event: response.completed")[0]
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body))
        error, _ = self.run_request()
        self.assertTrue(error)
        self.assertEqual(self.chat.history, [])

    def test_empty_response_is_a_retryable_failure(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(text="")))
        error, stats = self.run_request()
        self.assertTrue(error)
        self.assertEqual(self.chat.history, [])
        self.assertEqual(stats.output_tokens, 10)
        self.assertGreater(estimate_cost(stats), 0)

    def test_reset_during_a_request_cannot_restore_the_old_history(self):
        self.connect(httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse()))

        def event(kind, data):
            self.events.append((kind, data))
            if kind == "delta":
                self.chat.reset()

        self.chat._run(self.chat.history, [{"type": "text", "text": "Question"}], "gpt_mini", "low", event)
        self.assertEqual(self.chat.history, [])
        self.assertEqual(self.chat.context_tokens, 0)

    def test_provider_specific_blocks_are_not_forwarded(self):
        history = [
            {
                "role": "assistant",
                "content": [
                    SimpleNamespace(type="thinking", thinking="private"),
                    SimpleNamespace(type="text", text="Answer"),
                ],
            }
        ]
        self.assertEqual(response_input(history), [{"role": "assistant", "content": "Answer"}])

    def test_quota_has_an_actionable_error(self):
        exc = openai.RateLimitError(
            "quota",
            response=httpx.Response(429, request=httpx.Request("POST", API_BASE_URL)),
            body={"code": "insufficient_quota"},
        )
        self.assertIn("billing", describe_error(exc, "gpt-5.4-mini"))

    def test_explicit_endpoint_ignores_environment_override(self):
        with mock.patch.dict("os.environ", {"OPENAI_BASE_URL": "https://example.invalid"}):
            self.chat._api_key = "fake-unit-test"
            self.chat._client = self.chat._make_client()
        self.assertEqual(str(self.chat.client.base_url), API_BASE_URL + "/")


class ProviderTests(unittest.TestCase):
    def make_chat(self):
        cfg = {"model": "gpt_mini"}
        with mock.patch("overlay.ai.client.load_api_key", return_value=None):
            chat = AIChat(cfg)
            chat._keys = {"openai": "test-openai", "anthropic": "test-anthropic"}
        return chat, cfg

    def test_switching_provider_preserves_text_and_images(self):
        chat, _ = self.make_chat()
        content = build_user_content(typed="Question", image_b64="aW1hZ2U=")
        chat._chat.history = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": [SimpleNamespace(type="text", text="Answer")]},
        ]
        chat.system_extra = "Profile"
        chat._chat.context_tokens = 123
        replacement = OpenAIChat(None)
        with (
            mock.patch.object(chat, "_make_chat", return_value=replacement),
            mock.patch.object(replacement, "ask", return_value=True),
        ):
            self.assertTrue(chat.ask(content, "sonnet", "low", lambda *_: None))
        self.assertEqual(chat._provider, "anthropic")
        self.assertEqual(chat.history[0]["content"], content)
        self.assertEqual(chat.history[1]["content"], [{"type": "text", "text": "Answer"}])
        self.assertEqual((chat.context_tokens, chat.system_extra), (123, "Profile"))

    def test_busy_provider_cannot_be_replaced(self):
        chat, _ = self.make_chat()
        chat._chat.busy = True
        self.assertFalse(chat.ask([], "sonnet", "low", lambda *_: None))
        self.assertEqual(chat._provider, "openai")

    def test_key_reload_waits_until_a_running_request_finishes(self):
        chat, _ = self.make_chat()
        original = chat._chat
        original.busy = True
        self.assertFalse(chat.reload_keys())
        self.assertIs(chat._chat, original)
        original.busy = False
        replacement = OpenAIChat(None)
        with (
            mock.patch("overlay.ai.client.load_api_key", return_value="new-key"),
            mock.patch.object(chat, "_make_chat", return_value=replacement),
        ):
            self.assertTrue(chat.configured)
        self.assertIs(chat._chat, replacement)
        self.assertFalse(chat._reload_pending)

    def test_configured_depends_on_selected_provider(self):
        chat, cfg = self.make_chat()
        chat._keys["anthropic"] = None
        self.assertTrue(chat.configured)
        cfg["model"] = "sonnet"
        self.assertFalse(chat.configured)

    def test_presets_keep_the_selected_provider(self):
        self.assertEqual(mode_preset("solve", "gpt_mini"), ("gpt_quality", "high"))
        self.assertEqual(mode_preset("solve", "sonnet"), ("opus", "high"))
        self.assertEqual(mode_preset("quick", "opus"), ("sonnet", "low"))

    def test_conservative_openai_estimate_and_long_context_price(self):
        self.assertAlmostEqual(
            estimate_question("gpt-5.4-mini", new_tokens=100, cached_tokens=1000, output_tokens=10),
            (1100 * 0.75 + 10 * 4.5) / 1e6,
        )
        stats = AnswerStats(model="GPT-5.5", model_id="gpt-5.5", input_tokens=300000, output_tokens=100)
        self.assertAlmostEqual(estimate_cost(stats), (300000 * 10 + 100 * 45) / 1e6)
