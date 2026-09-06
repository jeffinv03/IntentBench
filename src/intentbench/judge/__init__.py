"""Judges — the stand-in routers that pick an intent for a phrase."""

from intentbench.judge.base import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    Judge,
    JudgeError,
    build_messages,
)
from intentbench.judge.cache import ResponseCache, cache_dir
from intentbench.judge.mock import MockJudge

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "Judge",
    "JudgeError",
    "MockJudge",
    "ResponseCache",
    "build_messages",
    "cache_dir",
    "get_judge",
]


def get_judge(name: str, model_id: str | None = None, **kwargs: object) -> Judge:
    """Construct a judge by name. Provider SDKs are imported lazily.

    Importing lazily keeps ``intentbench catalog`` and ``intentbench validate``
    working with no provider SDK installed at all.
    """
    key = name.strip().lower()
    if key == "mock":
        from intentbench.judge.mock import MockJudge as _Mock

        script = kwargs.get("script")
        return _Mock(script_path=script)  # type: ignore[arg-type]
    if key == "anthropic":
        from intentbench.judge.anthropic import AnthropicJudge

        return AnthropicJudge(model_id=model_id) if model_id else AnthropicJudge()
    if key == "openai":
        from intentbench.judge.openai import OpenAIJudge

        return OpenAIJudge(model_id=model_id) if model_id else OpenAIJudge()
    raise JudgeError(f"Unknown judge {name!r}. Available: anthropic, openai, mock.")
