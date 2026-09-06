"""A scripted, offline judge.

Every test in the suite uses this. No test makes a network call, ever — CI runs
green with no API key configured, and a contributor can work on scoring,
reporting, and diffing without an account anywhere.

The script is a YAML mapping of phrase to what the judge should "decide":

.. code-block:: yaml

    responses:
      "add milk to my shopping list":
        intent: AddItemIntent
        parameters: { item: milk, list: shopping }

      "what's the weather in Tokyo":
        intent: null            # abstain

      "remind me to call mom":
        error: "simulated API failure"

    default:
      intent: null
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from intentbench.judge.base import JudgeError
from intentbench.models import JudgeResult, ToolSchema


class MockJudge:
    """Replays scripted decisions. Deterministic by construction."""

    name = "mock"

    def __init__(
        self,
        script_path: Path | str | None = None,
        *,
        responses: dict[str, Any] | None = None,
        default: dict[str, Any] | None = None,
        model_id: str = "mock",
    ) -> None:
        self.model_id = model_id
        self.calls = 0
        self._responses: dict[str, Any] = {}
        self._default: dict[str, Any] = default or {"intent": None}

        if script_path is not None:
            self._load(Path(script_path))
        if responses is not None:
            self._responses.update({self._key(k): v for k, v in responses.items()})

    @staticmethod
    def _key(phrase: str) -> str:
        return phrase.strip().casefold()

    def _load(self, path: Path) -> None:
        if not path.is_file():
            raise JudgeError(f"No mock script at {path}")
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise JudgeError(f"Could not read mock script {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise JudgeError(f"{path}: expected a mapping at the top level.")

        responses = document.get("responses", document)
        if not isinstance(responses, dict):
            raise JudgeError(f"{path}: `responses` must be a mapping of phrase to result.")
        self._responses = {self._key(str(k)): v for k, v in responses.items()}

        default = document.get("default")
        if isinstance(default, dict):
            self._default = default

    def select(self, phrase: str, tools: list[ToolSchema], locale: str) -> JudgeResult:
        del locale
        self.calls += 1

        scripted = self._responses.get(self._key(phrase), self._default)
        if scripted is None:
            scripted = {"intent": None}
        if isinstance(scripted, str):
            # Shorthand: `"add milk": AddItemIntent`
            scripted = {"intent": scripted}
        if not isinstance(scripted, dict):
            return JudgeResult(error=f"mock script entry for {phrase!r} is not a mapping")

        if scripted.get("error"):
            return JudgeResult(error=str(scripted["error"]), raw_response=dict(scripted))

        selected = scripted.get("intent")
        # Guard the most likely scripting mistake: naming an intent the rendered
        # catalog does not contain, which would silently score as a wrong intent.
        if selected is not None:
            known = {tool.name for tool in tools}
            if str(selected) not in known:
                return JudgeResult(
                    error=(
                        f"mock script selects {selected!r}, which is not in the "
                        f"tool list for this catalog"
                    ),
                    raw_response=dict(scripted),
                )

        parameters = scripted.get("parameters") or {}
        if not isinstance(parameters, dict):
            return JudgeResult(error=f"mock parameters for {phrase!r} must be a mapping")

        return JudgeResult(
            selected_intent=None if selected is None else str(selected),
            parameters=dict(parameters),
            raw_response=dict(scripted),
            latency_ms=int(scripted.get("latency_ms", 0)),
            input_tokens=int(scripted.get("input_tokens", 0)),
            output_tokens=int(scripted.get("output_tokens", 0)),
        )
