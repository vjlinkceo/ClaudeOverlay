"""What an answer cost, from the token counts the API reports."""

from __future__ import annotations

from overlay.ai.base import AnswerStats
from overlay.ai.prompts import SYSTEM_PROMPT

# USD per million tokens: input, output, cache write, cache read. Anthropic API list prices as of
# 2026-10; cache writes are the standard 1.25x of input. Update when the price list changes.
PRICES: dict[str, tuple[float, float, float, float]] = {
    # OpenAI model pages checked 2026-10-16. OpenAI has no cache-write surcharge for these models.
    "gpt-5.4-mini": (0.75, 4.50, 0.75, 0.075),
    "gpt-5.5": (5.00, 30.00, 5.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
FAST_MULTIPLIER = 2.0
# Rough token estimates for the price shown before sending: UTF-8 bytes per token (Cyrillic takes two
# bytes per letter and about two letters per token, Latin one byte and about four letters per token).
BYTES_PER_TOKEN = 4.5
PIXELS_PER_TOKEN = 750
OUTPUT_TOKENS = {"quick": 180, "interview": 220, "explain": 600, "solve": 800}
THINKING_TOKENS = {"low": 0, "medium": 400, "high": 1500}


def _prices(model_id: str, input_tokens: int):
    prices = PRICES.get(model_id)
    if prices and model_id == "gpt-5.5" and input_tokens > 272_000:
        p_in, p_out, p_write, p_read = prices
        return p_in * 2, p_out * 1.5, p_write * 2, p_read * 2
    return prices


def estimate_cost(stats: AnswerStats) -> float | None:
    """None for a model whose price is not known."""
    prices = _prices(stats.model_id, stats.input_tokens + stats.cache_read + stats.cache_write)
    if prices is None:
        return None
    p_in, p_out, p_write, p_read = prices
    usd = (
        stats.input_tokens * p_in
        + stats.output_tokens * p_out
        + stats.cache_write * p_write
        + stats.cache_read * p_read
    ) / 1e6
    return usd * (FAST_MULTIPLIER if stats.fast else 1.0)


def estimate_tokens(text: str) -> int:
    return int(len(text.encode("utf-8")) / BYTES_PER_TOKEN) + (1 if text else 0)


def image_tokens(pixels: int) -> int:
    return pixels // PIXELS_PER_TOKEN


def system_tokens() -> int:
    return estimate_tokens(SYSTEM_PROMPT)


def estimate_question(
    model_id: str, *, new_tokens: int, cached_tokens: int, output_tokens: int, fast: bool = False
) -> float | None:
    """What the next question will roughly cost: the new text is written to the cache, the system
    prompt, profile and history are read from it, and the answer is billed as output."""
    prices = _prices(model_id, new_tokens + cached_tokens)
    if prices is None:
        return None
    p_in, p_out, p_write, p_read = prices
    if model_id.startswith("gpt-"):
        # Cache hits are not guaranteed; show a conservative estimate before sending.
        usd = ((new_tokens + cached_tokens) * p_in + output_tokens * p_out) / 1e6
    else:
        usd = (new_tokens * p_write + cached_tokens * p_read + output_tokens * p_out) / 1e6
    return usd * (FAST_MULTIPLIER if fast else 1.0)


def format_cost(usd: float) -> str:
    if usd < 0.0005:
        return "<0.1¢"
    if usd < 1.0:
        return f"{usd * 100:.1f}¢"
    return f"${usd:.2f}"
