"""Shared AI conversation: streaming, cancellation, model profiles, latency stats."""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass

from overlay.config import MODEL_PROFILES
from overlay.i18n import t

log = logging.getLogger(__name__)


@dataclass
class AnswerStats:
    model: str
    model_id: str = ""
    fast: bool = False
    first_token: float | None = None
    total: float = 0.0
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cancelled: bool = False

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write + self.output_tokens


def _block_text(block) -> str:
    kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
    if kind != "text":
        return ""
    return block.get("text", "") if isinstance(block, dict) else block.text


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(text for text in (_block_text(b) for b in content) if text)


class _Cancelled(Exception):
    pass


class BaseChat:
    """The history is append-only so the prompt cache and thinking blocks stay valid."""

    default_profile = "gpt_mini"

    def __init__(self, api_key: str | None):
        self.configured = bool(api_key)
        self._api_key = api_key
        self._client = None
        self._connect_error = None
        self._ready = threading.Event()
        if api_key:
            threading.Thread(target=self._connect, name="ai-sdk", daemon=True).start()
        else:
            self._ready.set()
        self.history: list[dict] = []
        self.context_tokens = 0
        self.busy = False
        self.cancel_event = threading.Event()
        self.fast_unavailable = False
        self.system_extra = ""
        self._stream = None

    def _connect(self) -> None:
        try:
            self._client = self._make_client()
        except Exception as exc:
            self._connect_error = t("err_client_init", message=str(exc))
            log.exception("AI client initialization failed")
        finally:
            self._ready.set()

    def _make_client(self):
        raise NotImplementedError

    def _describe_error(self, exc: Exception, model: str) -> str:
        raise NotImplementedError

    @property
    def client(self):
        """Waits for the SDK to load; only the request thread should need to."""
        self._ready.wait()
        return self._client

    def reset(self) -> None:
        self.cancel()
        self.history = []
        self.context_tokens = 0

    def recent_exchanges(self, count: int) -> list[tuple[str, str]]:
        """The last `count` (question, answer) pairs as plain text."""
        pairs = []
        for user, assistant in zip(self.history[::2], self.history[1::2], strict=False):
            asked = _message_text(user["content"])
            answer = _message_text(assistant["content"]).strip()
            typed = re.search(r"<typed_question>\n(.*?)\n</typed_question>", asked, re.S)
            question = typed[1].strip() if typed else ""
            if not question and answer.startswith("❓"):
                question, _, answer = answer.partition("\n")
            pairs.append((question, answer.strip()))
        return pairs[-count:] if count else []

    def rollover(self, keep: int) -> list[tuple[str, str]]:
        """Start a new conversation; returns the last answers to carry over as plain text."""
        exchanges = self.recent_exchanges(keep)
        self.reset()
        return exchanges

    def cancel(self) -> None:
        """Closes the connection, so it also works while the model is thinking and not streaming text."""
        self.cancel_event.set()
        stream = self._stream
        if stream is not None:
            threading.Thread(target=self._close_quietly, args=(stream,), daemon=True).start()

    @staticmethod
    def _close_quietly(stream) -> None:
        with contextlib.suppress(Exception):
            stream.close()

    def ask(self, content: list[dict], profile_key: str, effort: str, on_event) -> bool:
        """on_event(kind, data): delta(text) / thinking / notice(text) / done((error, stats))."""
        if self.busy:
            return False
        self.busy = True
        self.cancel_event.clear()
        threading.Thread(
            target=self._run, args=(self.history, content, profile_key, effort, on_event), name="ai-answer", daemon=True
        ).start()
        return True

    def _run(self, history: list[dict], content: list[dict], profile_key: str, effort: str, on_event):
        profile = MODEL_PROFILES.get(profile_key, MODEL_PROFILES[self.default_profile])
        stats = AnswerStats(model=profile["label"], model_id=profile["model"])
        started = time.perf_counter()
        history.append({"role": "user", "content": content})
        error, ok = None, False
        try:
            if self.client is None:
                error = self._connect_error or t("no_key")
                return
            if self.cancel_event.is_set():
                raise _Cancelled
            final = self._request(history, profile, effort, on_event, stats, started)
            if final.stop_reason == "refusal":
                error = t("refusal")
            else:
                history.append({"role": "assistant", "content": final.content})
                ok = True
                if final.stop_reason == "max_tokens":
                    on_event("delta", t("truncated"))
            stats.input_tokens = final.usage.input_tokens or 0
            stats.cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
            stats.cache_write = getattr(final.usage, "cache_creation_input_tokens", 0) or 0
            stats.output_tokens = final.usage.output_tokens or 0
            if ok and history is self.history:
                self.context_tokens = stats.context_tokens
        except Exception as e:
            if isinstance(e, _Cancelled) or self.cancel_event.is_set():
                stats.cancelled, error = True, t("stopped")
            else:
                error = self._describe_error(e, profile["model"])
        finally:
            self._stream = None
            stats.total = time.perf_counter() - started
            if not ok and history and history[-1]["role"] == "user":
                history.pop()  # only the tail is removed, so the history prefix never changes
            self.busy = False
            log.info(
                "AI %s effort=%s: first=%.2fs total=%.2fs in=%d cached=%d written=%d out=%d ok=%s",
                profile["model"],
                effort,
                stats.first_token or -1,
                stats.total,
                stats.input_tokens,
                stats.cache_read,
                stats.cache_write,
                stats.output_tokens,
                ok,
            )
            on_event("done", (error, stats))

    def _request(self, history, profile, effort, on_event, stats, started):
        raise NotImplementedError
