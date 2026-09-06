"""Deciding whether a selection was right.

Two rules shape everything here:

1. **Intent match is scored before parameter match.** Picking the wrong action
   and picking the right action with a wrong argument are different failures
   with different fixes, and the report must never collapse them.
2. **Only listed parameters are scored.** A corpus author is not forced to pin
   down what the phrase leaves open, and extra parameters the model supplied are
   recorded but do not fail the case in v0.1.
"""

from __future__ import annotations

import re
from typing import Any

from intentbench.corpus.schema import CorpusCase
from intentbench.models import (
    NO_MATCH,
    CaseOutcome,
    CaseResult,
    IntentDefinition,
    ParameterComparison,
    ParameterType,
)

_WHITESPACE = re.compile(r"\s+")

_TRUTHY = {"true", "yes", "on", "1"}
_FALSY = {"false", "no", "off", "0"}


def normalize_value(value: Any) -> Any:
    """Normalize one side of a comparison.

    Strings casefold, strip, and collapse internal whitespace. Booleans accept
    the usual yes/on/1 spellings. Numbers compare numerically, so ``2`` and
    ``"2.0"`` agree.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = _WHITESPACE.sub(" ", value.strip())
        lowered = text.casefold()
        if lowered in _TRUTHY:
            return True
        if lowered in _FALSY:
            return False
        try:
            return float(text)
        except ValueError:
            return lowered
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    return value


def _as_bool(value: Any) -> bool | None:
    """Coerce a normalized value to a bool, or None if it isn't boolean-ish."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    return None


def values_match(expected: Any, actual: Any) -> bool:
    """Compare two parameter values after normalization.

    When either side is a boolean, the whole comparison is boolean — so an
    expected ``true`` matches an actual ``1``, ``"yes"``, or ``"on"``, but not
    ``2`` and not ``"milk"``. Otherwise numbers compare numerically and strings
    compare casefolded.
    """
    left = normalize_value(expected)
    right = normalize_value(actual)

    if isinstance(left, bool) or isinstance(right, bool):
        left_bool, right_bool = _as_bool(left), _as_bool(right)
        return left_bool is not None and left_bool == right_bool

    if isinstance(left, float) and isinstance(right, float):
        return left == right
    return bool(left == right)


def compare_parameters(
    expected: dict[str, Any],
    actual: dict[str, Any],
    intent: IntentDefinition | None = None,
) -> list[ParameterComparison]:
    """Compare only the parameters the corpus listed.

    Date and duration parameters are compared **literally, as strings**. v0.1
    does not normalize datetimes — that is a genuine rabbit hole and date
    parameters are a minority case — so a comparison against a date parameter is
    flagged so the report can say the comparison was literal.
    """
    types: dict[str, ParameterType] = {}
    if intent is not None:
        types = {p.name: p.type for p in intent.parameters}

    comparisons: list[ParameterComparison] = []
    for name, expected_value in expected.items():
        actual_value = actual.get(name)
        is_temporal = types.get(name) in (ParameterType.DATE, ParameterType.DURATION)
        comparisons.append(
            ParameterComparison(
                name=name,
                expected=expected_value,
                actual=actual_value,
                matched=(name in actual and values_match(expected_value, actual_value)),
                literal_date_comparison=is_temporal,
            )
        )
    return comparisons


def _normalize_selection(selected: str | None) -> str | None:
    """Map the abstention sentinel onto ``None``.

    The judge answers with a tool name; ``no_matching_intent`` means "nothing
    fits", which is the same thing the corpus writes as ``intent: null``.
    """
    if selected is None or selected == NO_MATCH:
        return None
    return selected


def score_case(
    case: CorpusCase,
    *,
    locale: str,
    selected_intent: str | None,
    selected_parameters: dict[str, Any],
    intent: IntentDefinition | None = None,
    error: str | None = None,
) -> CaseResult:
    """Score one case into a :class:`CaseResult`."""
    expected_intent = case.expect.intent
    result = CaseResult(
        phrase=case.phrase,
        locale=locale,
        tags=list(case.tags),
        note=case.note,
        expected_intent=expected_intent,
        expected_parameters=dict(case.expect.parameters),
        selected_parameters=dict(selected_parameters),
        error=error,
    )

    if error is not None:
        result.outcome = CaseOutcome.ERROR
        return result

    actual = _normalize_selection(selected_intent)
    result.selected_intent = actual

    # 1. Abstention, in both directions.
    if expected_intent is None:
        result.outcome = CaseOutcome.MATCH if actual is None else CaseOutcome.FALSE_POSITIVE
        return result
    if actual is None:
        result.outcome = CaseOutcome.FALSE_NEGATIVE
        return result

    # 2. Wrong action beats any parameter consideration.
    if actual != expected_intent:
        result.outcome = CaseOutcome.WRONG_INTENT
        return result

    # 3. Right action — now judge the arguments.
    comparisons = compare_parameters(case.expect.parameters, selected_parameters, intent)
    result.parameter_comparisons = comparisons
    result.extra_parameters = {
        name: value
        for name, value in selected_parameters.items()
        if name not in case.expect.parameters
    }
    result.outcome = (
        CaseOutcome.MATCH if all(c.matched for c in comparisons) else CaseOutcome.PARAM_MISMATCH
    )
    return result
