"""Select the provider from the model, keeping the visible conversation intact."""

from __future__ import annotations

import contextlib
import threading

from overlay.ai.base import _message_text
from overlay.ai.keys import load_api_key
from overlay.ai.openai_client import OpenAIChat
from overlay.claude.client import ClaudeChat
from overlay.config import provider_for_model


class AIChat:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._keys: dict[str, str | None] = {}
        self._reload_pending = False
        self._provider = provider_for_model(cfg["model"])
        self._chat = self._make_chat(self._provider)

    def _key(self, provider: str) -> str | None:
        if provider not in self._keys:
            self._keys[provider] = load_api_key(provider=provider)
        return self._keys[provider]

    def _make_chat(self, provider: str):
        cls = OpenAIChat if provider == "openai" else ClaudeChat
        return cls(self._key(provider))

    @property
    def configured(self) -> bool:
        if self._reload_pending and not self.busy:
            self.reload_keys()
        return bool(self._key(provider_for_model(self.cfg["model"])))

    @property
    def busy(self) -> bool:
        return self._chat.busy

    @property
    def history(self) -> list[dict]:
        return self._chat.history

    @property
    def context_tokens(self) -> int:
        return self._chat.context_tokens

    @property
    def system_extra(self) -> str:
        return self._chat.system_extra

    @system_extra.setter
    def system_extra(self, value: str) -> None:
        self._chat.system_extra = value

    def _replace_chat(self, provider: str) -> None:
        previous = self._chat
        self._chat = self._make_chat(provider)
        # Signed thinking blocks are specific to their provider. Text and user images are portable.
        self._chat.history = [
            {"role": m["role"], "content": [{"type": "text", "text": _message_text(m["content"])}]}
            if m["role"] == "assistant"
            else dict(m)
            for m in previous.history
        ]
        self._chat.context_tokens = previous.context_tokens
        self._chat.system_extra = previous.system_extra
        self._provider = provider

        # No request is running here, so the old HTTP pool can be closed on its worker thread.
        def close():
            if previous.client is not None:
                with contextlib.suppress(Exception):
                    previous.client.close()

        threading.Thread(target=close, name="ai-close", daemon=True).start()

    def reload_keys(self) -> bool:
        if self.busy:
            self._reload_pending = True
            return False
        self._reload_pending = False
        self._keys.clear()
        self._replace_chat(provider_for_model(self.cfg["model"]))
        return True

    def ask(self, content, profile_key, effort, on_event) -> bool:
        if self.busy:
            return False
        if self._reload_pending:
            self.reload_keys()
        provider = provider_for_model(profile_key)
        if provider != self._provider:
            self._replace_chat(provider)
        return self._chat.ask(content, profile_key, effort, on_event)

    def cancel(self) -> None:
        self._chat.cancel()

    def reset(self) -> None:
        self._chat.reset()

    def recent_exchanges(self, count: int) -> list[tuple[str, str]]:
        return self._chat.recent_exchanges(count)

    def rollover(self, keep: int) -> list[tuple[str, str]]:
        return self._chat.rollover(keep)
