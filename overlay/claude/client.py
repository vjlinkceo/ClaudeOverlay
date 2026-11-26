"""Anthropic transport, including its optional fast-mode fallback."""

from __future__ import annotations

import logging
import time

from overlay.ai.base import AnswerStats, BaseChat, _Cancelled
from overlay.ai.prompts import SYSTEM_PROMPT
from overlay.i18n import t

log = logging.getLogger(__name__)
API_BASE_URL = "https://api.anthropic.com"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FAST_BETA = "fast-mode-2026-02-01"


def describe_error(e: Exception, model: str) -> str:
    anthropic = _sdk()
    if isinstance(e, anthropic.AuthenticationError):
        return t("err_auth")
    if isinstance(e, anthropic.PermissionDeniedError):
        return t("err_permission", message=e.message)
    if isinstance(e, anthropic.NotFoundError):
        return t("err_not_found", model=model)
    if isinstance(e, anthropic.RateLimitError):
        return t("err_rate_limit")
    if isinstance(e, anthropic.BadRequestError):
        return t("err_bad_request", message=e.message)
    if isinstance(e, anthropic.APIStatusError):
        return t("err_api", code=e.status_code, message=e.message)
    if isinstance(e, anthropic.APIConnectionError):
        return t("err_connection")
    return f"{type(e).__name__}: {e}"


def _sdk():
    # SDK imports run on a background thread so the window stays responsive.
    import anthropic

    return anthropic


class ClaudeChat(BaseChat):
    default_profile = "sonnet"

    def _make_client(self):
        sdk = _sdk()
        return sdk.Anthropic(api_key=self._api_key, base_url=API_BASE_URL, timeout=sdk.Timeout(120.0, connect=10.0))

    def _describe_error(self, exc: Exception, model: str) -> str:
        return describe_error(exc, model)

    def _request(self, history, profile, effort, on_event, stats: AnswerStats, started: float):
        fast = profile["fast"] and not self.fast_unavailable
        while True:
            try:
                return self._stream_once(history, profile, effort, fast, on_event, stats, started)
            except _sdk().RateLimitError as e:
                if not fast or stats.first_token is not None:
                    raise
                # Fast mode has its own rate limit. The error text contains the org id, so it is not logged.
                zero_limit = "rate limit of 0 " in e.message
                log.warning("fast mode rate-limited (zero limit: %s), retrying at standard speed", zero_limit)
                fast = False
                if zero_limit:
                    self.fast_unavailable = True
                on_event("notice", t("fast_unavailable" if zero_limit else "fast_overloaded"))

    def _stream_once(self, history, profile, effort, fast, on_event, stats: AnswerStats, started: float):
        stats.fast = fast
        system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
        if self.system_extra:
            system.append({"type": "text", "text": self.system_extra, "cache_control": {"type": "ephemeral"}})
        params: dict = dict(
            model=profile["model"],
            max_tokens=16000,
            system=system,
            messages=history,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
        )
        betas = []
        if profile["fallbacks"]:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if fast:
            betas.append(FAST_BETA)
            params["speed"] = "fast"
        if betas:
            params["betas"] = betas
        client = self.client.with_options(max_retries=0) if fast else self.client

        with client.beta.messages.stream(**params) as stream:
            self._stream = stream
            if self.cancel_event.is_set():
                raise _Cancelled
            for event in stream:
                if self.cancel_event.is_set():
                    raise _Cancelled
                if event.type == "text":
                    if stats.first_token is None:
                        stats.first_token = time.perf_counter() - started
                    on_event("delta", event.text)
                elif event.type == "content_block_start" and event.content_block.type == "thinking":
                    on_event("thinking", None)
            if self.cancel_event.is_set():
                raise _Cancelled
            return stream.get_final_message()
