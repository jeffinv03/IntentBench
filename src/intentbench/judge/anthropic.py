"""The Anthropic judge — the default.

Selection uses **native tool calling**, not JSON-in-prose. The rendered catalog
becomes a real tool list and the model picks one, which is the closest
structural analogue to a router we can build from outside Apple's stack. It also
removes output-format drift as a source of noise: there is no free text to
misparse.

``tool_choice: {"type": "any"}`` forces a call. That is safe because the tool
list always carries the ``no_matching_intent`` escape hatch, so "nothing fits"
remains expressible — while an entire class of unparseable non-answers
disappears. ``disable_parallel_tool_use`` pins it to exactly one selection.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from intentbench.judge.base import (
    MAX_TOKENS,
    SYSTEM_PROMPT,
    TEMPERATURE,
    JudgeError,
    build_messages,
)
from intentbench.models import JudgeResult, ToolSchema

DEFAULT_MODEL = "claude-sonnet-4-6"

#: 429s and 5xx are retried; everything else surfaces immediately.
MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0


class AnthropicJudge:
    """Routes phrases with the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Any = time.sleep,
    ) -> None:
        self.model_id = model_id
        self.max_attempts = max_attempts
        self._sleep = sleep
        #: Set once the API rejects forced tool choice, so we stop retrying it.
        self._forced_choice_supported = True

        if client is not None:
            self._client = client
            return

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise JudgeError(
                "The Anthropic judge needs the `anthropic` package.\n"
                "  pip install 'intentbench[anthropic]'"
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise JudgeError(
                "No Anthropic API key found.\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "Or run with `--judge mock` to work offline."
            )
        self._client = anthropic.Anthropic(api_key=key)

    # -- retry ------------------------------------------------------------

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status == 429 or status >= 500
        name = type(exc).__name__
        return name in {
            "RateLimitError",
            "InternalServerError",
            "APIConnectionError",
            "APITimeoutError",
            "APIConnectionTimeoutError",
        }

    def _backoff(self, attempt: int) -> float:
        """Exponential with full jitter, so a burst of workers doesn't resynchronize."""
        ceiling = min(BASE_DELAY_SECONDS * (2**attempt), MAX_DELAY_SECONDS)
        return random.uniform(0, ceiling)

    # -- selection --------------------------------------------------------

    def _request(self, phrase: str, tools: list[ToolSchema], locale: str) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "system": SYSTEM_PROMPT,
            "messages": build_messages(phrase, locale),
            "tools": [tool.to_api_dict() for tool in tools],
        }
        if self._forced_choice_supported:
            kwargs["tool_choice"] = {"type": "any", "disable_parallel_tool_use": True}
        else:
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return self._client.messages.create(**kwargs)

    def select(self, phrase: str, tools: list[ToolSchema], locale: str) -> JudgeResult:
        started = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            try:
                response = self._request(phrase, tools, locale)
            except Exception as exc:
                # Some models (Claude Fable 5.1 and later) reject forced tool
                # choice. Fall back to `auto` once rather than failing the run.
                if self._forced_choice_supported and _is_forced_choice_rejection(exc):
                    self._forced_choice_supported = False
                    continue
                last_error = exc
                if not self._is_retryable(exc) or attempt == self.max_attempts - 1:
                    break
                self._sleep(self._backoff(attempt))
                continue

            return _to_result(response, started)

        # The run continues; this one case is marked ERROR. One flaky phrase must
        # not throw away the other 200 results.
        return JudgeResult(
            error=f"{type(last_error).__name__}: {last_error}",
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _is_forced_choice_rejection(exc: Exception) -> bool:
    message = str(exc).lower()
    return "tool_choice" in message and ("not supported" in message or "any" in message)


def _to_result(response: Any, started: float) -> JudgeResult:
    latency_ms = int((time.monotonic() - started) * 1000)

    selected: str | None = None
    parameters: dict[str, Any] = {}
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            selected = str(block.name)
            raw_input = getattr(block, "input", None)
            parameters = dict(raw_input) if isinstance(raw_input, dict) else {}
            break

    usage = getattr(response, "usage", None)

    try:
        raw = response.model_dump(mode="json")
    except Exception:
        raw = {"repr": repr(response)}

    return JudgeResult(
        selected_intent=selected,
        parameters=parameters,
        raw_response=raw,
        latency_ms=latency_ms,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )
