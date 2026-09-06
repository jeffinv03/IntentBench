"""The judge protocol and the prompt every judge uses.

The prompt is deliberately bare. No few-shot examples, no chain-of-thought
scaffolding, no hints about the app. The question being asked is whether the
*catalog* is unambiguous on its own; prompt scaffolding would paper over exactly
the defects the tool exists to surface.

``PROMPT_VERSION`` participates in the cache key, so editing the prompt
invalidates every cached response rather than silently mixing results from two
different prompts into one run.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from intentbench.models import JudgeResult, ToolSchema

#: Bump whenever SYSTEM_PROMPT changes. Part of the cache key.
PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are the request router for a mobile app. The user has spoken a request "
    "out loud. Choose exactly one action that best matches their request and fill "
    "in its parameters from what they said.\n\n"
    "Only use information present in the request. Do not invent parameter values. "
    "If no action matches, use no_matching_intent."
)

#: Deterministic by construction. Not configurable in v0.1 — a judge that
#: wanders between runs makes the diff meaningless.
TEMPERATURE = 0.0

#: Selection is a single short tool call; the cap only needs to cover arguments.
MAX_TOKENS = 1024


class JudgeError(Exception):
    """The judge could not be constructed or configured (missing key, bad model)."""


@runtime_checkable
class Judge(Protocol):
    """Anything that can route a phrase against a tool list."""

    name: str
    model_id: str

    def select(self, phrase: str, tools: list[ToolSchema], locale: str) -> JudgeResult:
        """Pick at most one tool for *phrase*, returning the selection."""
        ...


def build_messages(phrase: str, locale: str) -> list[dict[str, Any]]:
    """The user turn: the phrase, and nothing else.

    The locale rides in the cache key and is available to providers that accept
    it, but it is deliberately not injected into the prompt — doing so would be
    extra scaffolding that the catalog does not get in production.
    """
    del locale
    return [{"role": "user", "content": phrase}]


def cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated cost in USD, from a small built-in price table.

    Prices are per million tokens and are a convenience, not billing truth. An
    unknown model yields 0.0 and the report says the estimate is unavailable.
    """
    rates = PRICES.get(model_id)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


#: (input $/1M, output $/1M). Kept deliberately short; update as needed.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
