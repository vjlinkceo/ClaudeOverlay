"""Deferred calls on the UI thread, independent of the toolkit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class Scheduler(Protocol):
    """Tk's root satisfies this directly; Qt gets a QTimer-based implementation; tests use a fake clock."""

    def after(self, ms: int, fn: Callable[[], None]) -> Any: ...

    def after_cancel(self, handle: Any) -> None: ...
