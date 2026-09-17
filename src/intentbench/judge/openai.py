"""The OpenAI judge — **experimental** in v0.1.

Shipped so cross-provider comparison is possible today, but the default and the
supported path is the Anthropic judge. Treat disagreements between the two as
interesting rather than authoritative; proper multi-model cross-agreement
scoring is v0.4 work.

Structurally identical to the Anthropic judge: the catalog becomes a real
function list, ``tool_choice="required"`` forces a call, and the
``no_matching_intent`` escape hatch keeps abstention expressible.
"""

from __future__ import annotations

import json
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

DEFAULT_MODEL = "gpt-4.1"
MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0


class OpenAIJudge:
    """Routes phrases with the OpenAI Chat Completions API. Experimental."""

    name = "openai"
    experimental = True

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

        if client is not None:
            self._client = client
            return

        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise JudgeError(
                "The OpenAI judge needs the `openai` package.\n  pip install 'intentbench[openai]'"
            ) from exc

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise JudgeError(
                "No OpenAI API key found. Either:\n"
                "  cp .env.example .env   # then paste the key into .env\n"
                "  export OPENAI_API_KEY=sk-...\n"
                "Or run with `--judge mock` to work offline."
            )
        self._client = openai.OpenAI(api_key=key)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status == 429 or status >= 500
        return type(exc).__name__ in {
            "RateLimitError",
            "InternalServerError",
            "APIConnectionError",
            "APITimeoutError",
        }

    def _backoff(self, attempt: int) -> float:
        ceiling = min(BASE_DELAY_SECONDS * (2**attempt), MAX_DELAY_SECONDS)
        return random.uniform(0, ceiling)

    def select(self, phrase: str, tools: list[ToolSchema], locale: str) -> JudgeResult:
        started = time.monotonic()
        functions = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *build_messages(phrase, locale)]

        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self._client.chat.completions.create(
                    model=self.model_id,
                    max_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE,
                    messages=messages,
                    tools=functions,
                    tool_choice="required",
                    parallel_tool_calls=False,
                )
            except Exception as exc:
                last_error = exc
                if not self._is_retryable(exc) or attempt == self.max_attempts - 1:
                    break
                self._sleep(self._backoff(attempt))
                continue

            return _to_result(response, started)

        return JudgeResult(
            error=f"{type(last_error).__name__}: {last_error}",
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _to_result(response: Any, started: float) -> JudgeResult:
    latency_ms = int((time.monotonic() - started) * 1000)

    selected: str | None = None
    parameters: dict[str, Any] = {}

    choices = getattr(response, "choices", None) or []
    if choices:
        calls = getattr(choices[0].message, "tool_calls", None) or []
        if calls:
            call = calls[0]
            selected = str(call.function.name)
            try:
                # Never string-match serialized tool arguments; always parse.
                decoded = json.loads(call.function.arguments or "{}")
                parameters = decoded if isinstance(decoded, dict) else {}
            except (TypeError, ValueError):
                parameters = {}

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
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )
