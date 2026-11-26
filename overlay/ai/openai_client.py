"""OpenAI Responses API transport. Audio transcription stays local."""

from __future__ import annotations

import time
from types import SimpleNamespace

from overlay.ai.base import BaseChat, _Cancelled, _message_text
from overlay.ai.prompts import SYSTEM_PROMPT
from overlay.i18n import t

API_BASE_URL = "https://api.openai.com/v1"


def _sdk():
    import openai

    return openai


def describe_error(exc: Exception, model: str) -> str:
    sdk = _sdk()
    if isinstance(exc, sdk.AuthenticationError):
        return t("err_auth")
    if isinstance(exc, sdk.PermissionDeniedError):
        return t("err_permission", message=exc.message)
    if isinstance(exc, sdk.NotFoundError):
        return t("err_not_found", model=model)
    if isinstance(exc, sdk.RateLimitError):
        code = getattr(exc, "code", None)
        return t("err_quota") if code == "insufficient_quota" else t("err_rate_limit")
    if isinstance(exc, sdk.BadRequestError):
        return t("err_bad_request", message=exc.message)
    if isinstance(exc, sdk.APIStatusError):
        return t("err_api", code=exc.status_code, message=exc.message)
    if isinstance(exc, sdk.APIConnectionError):
        return t("err_connection")
    return f"{type(exc).__name__}: {exc}"


def response_input(history: list[dict]) -> list[dict]:
    """Translate our text/image blocks without forwarding Anthropic-only fields."""
    messages = []
    for message in history:
        content = message["content"]
        if message["role"] == "assistant" or isinstance(content, str):
            messages.append({"role": message["role"], "content": _message_text(content)})
            continue
        blocks = []
        for block in content:
            if block["type"] == "text":
                blocks.append({"type": "input_text", "text": block["text"]})
            elif block["type"] == "image":
                source = block["source"]
                blocks.append(
                    {"type": "input_image", "image_url": f"data:{source['media_type']};base64,{source['data']}"}
                )
        messages.append({"role": "user", "content": blocks})
    return messages


class OpenAIChat(BaseChat):
    def _make_client(self):
        sdk = _sdk()
        # Explicit endpoint prevents OPENAI_BASE_URL from redirecting the key.
        return sdk.OpenAI(api_key=self._api_key, base_url=API_BASE_URL, timeout=sdk.Timeout(120.0, connect=10.0))

    def _describe_error(self, exc: Exception, model: str) -> str:
        return describe_error(exc, model)

    def _request(self, history, profile, effort, on_event, stats, started):
        instructions = SYSTEM_PROMPT + ("\n\n" + self.system_extra if self.system_extra else "")
        final = None
        with self.client.responses.create(
            model=profile["model"],
            instructions=instructions,
            input=response_input(history),
            reasoning={"effort": effort},
            max_output_tokens=16000,
            store=False,
            stream=True,
        ) as stream:
            self._stream = stream
            if self.cancel_event.is_set():
                raise _Cancelled
            on_event("thinking", None)
            for event in stream:
                if self.cancel_event.is_set():
                    raise _Cancelled
                if event.type == "response.output_text.delta":
                    if stats.first_token is None:
                        stats.first_token = time.perf_counter() - started
                    on_event("delta", event.delta)
                elif event.type == "error":
                    raise RuntimeError(event.message)
                elif event.type == "response.failed":
                    error = event.response.error
                    raise RuntimeError(error.message if error else t("err_empty_answer"))
                elif event.type in ("response.completed", "response.incomplete"):
                    final = event.response
            if self.cancel_event.is_set():
                raise _Cancelled

        if final is None:
            raise RuntimeError(t("err_empty_answer"))

        usage = final.usage
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        # Even an empty terminal answer can contain billable reasoning tokens.
        stats.input_tokens = max(0, (getattr(usage, "input_tokens", 0) or 0) - cached)
        stats.cache_read = cached
        stats.output_tokens = getattr(usage, "output_tokens", 0) or 0

        refused = any(
            getattr(block, "type", "") == "refusal" for item in final.output for block in getattr(item, "content", [])
        )
        text = final.output_text
        if final.status not in ("completed", "incomplete"):
            raise RuntimeError(t("err_empty_answer"))
        reason = getattr(final.incomplete_details, "reason", "")
        if not text and not refused:
            raise RuntimeError(t("err_empty_answer"))
        # OpenAI includes cached tokens in input_tokens; the UI expects disjoint buckets.
        return SimpleNamespace(
            content=[{"type": "text", "text": text}],
            stop_reason="refusal" if refused else "max_tokens" if reason == "max_output_tokens" else "end_turn",
            usage=SimpleNamespace(
                input_tokens=stats.input_tokens,
                cache_read_input_tokens=cached,
                output_tokens=stats.output_tokens,
            ),
        )
