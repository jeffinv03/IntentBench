"""Run-level metrics, coverage, and repeat handling."""

from __future__ import annotations

from collections import Counter
from typing import Any

from intentbench.models import (
    CaseOutcome,
    CaseResult,
    Catalog,
    IntentCoverage,
    IntentDefinition,
    RunMetrics,
)


def _majority(selections: list[str | None]) -> tuple[str | None, int]:
    """The most common selection and how many of N agreed with it."""
    counts = Counter(selections)
    winner, agreement = counts.most_common(1)[0]
    return winner, agreement


def resolve_repeats(results: list[CaseResult]) -> CaseResult:
    """Collapse N runs of the same case into one result.

    With ``--repeat N``, a case scores as MATCH only if the majority of runs
    matched, and any case whose selections split is flagged ``unstable``.

    Instability is signal, not noise: a phrase that routes differently across
    identical runs is a phrase whose catalog entry is ambiguous, and that is
    exactly the defect this tool exists to find.
    """
    if not results:
        raise ValueError("resolve_repeats needs at least one result")
    if len(results) == 1:
        result = results[0].model_copy(deep=True)
        result.repeats = 1
        result.agreement = 1
        result.observed_selections = [result.selected_intent]
        return result

    selections = [r.selected_intent for r in results]
    winner, agreement = _majority(selections)

    # Report the run that produced the majority selection, so the parameters and
    # raw detail shown alongside it are internally consistent.
    representative = next(
        (r for r in results if r.selected_intent == winner),
        results[0],
    ).model_copy(deep=True)

    passing = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.outcome is CaseOutcome.ERROR)

    if errors == len(results):
        representative.outcome = CaseOutcome.ERROR
    elif passing * 2 > len(results):
        representative.outcome = CaseOutcome.MATCH
    else:
        # Majority did not pass — report the most common non-passing outcome, so
        # the failure reason shown is the one that actually dominated.
        failing = [r.outcome for r in results if not r.passed]
        representative.outcome = Counter(failing).most_common(1)[0][0]

    representative.repeats = len(results)
    representative.agreement = agreement
    representative.unstable = len(set(selections)) > 1
    representative.observed_selections = selections
    representative.input_tokens = sum(r.input_tokens for r in results)
    representative.output_tokens = sum(r.output_tokens for r in results)
    representative.latency_ms = max(r.latency_ms for r in results)
    representative.from_cache = all(r.from_cache for r in results)
    return representative


def _coverage(results: list[CaseResult], intents: list[IntentDefinition]) -> list[IntentCoverage]:
    """Per-intent: how many cases target it, and how many passed.

    Intents with zero coverage are the most immediately actionable thing this
    tool produces — you cannot have tested what you never wrote a phrase for.
    """
    targeted: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    for result in results:
        if result.expected_intent is None:
            continue
        targeted[result.expected_intent] += 1
        if result.passed:
            passed[result.expected_intent] += 1

    return [
        IntentCoverage(
            identifier=intent.identifier,
            targeted=targeted.get(intent.identifier, 0),
            passed=passed.get(intent.identifier, 0),
        )
        for intent in intents
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(
    results: list[CaseResult],
    catalog: Catalog | None = None,
    intents: list[IntentDefinition] | None = None,
) -> RunMetrics:
    """Compute run-level metrics from scored cases."""
    counts: Counter[str] = Counter(r.outcome.value for r in results)

    total = len(results)
    errors = counts.get(CaseOutcome.ERROR.value, 0)
    matched = counts.get(CaseOutcome.MATCH.value, 0)

    # Parameter accuracy is conditioned on the intent being right — a wrong
    # action tells you nothing about parameter filling.
    param_scored = [r for r in results if r.intent_correct and r.expected_parameters]
    param_matched = sum(1 for r in param_scored if all(c.matched for c in r.parameter_comparisons))

    abstention_expected = sum(1 for r in results if r.expected_intent is None)
    false_positives = counts.get(CaseOutcome.FALSE_POSITIVE.value, 0)

    coverage: list[IntentCoverage] = []
    if intents is not None:
        coverage = _coverage(results, intents)
    elif catalog is not None:
        coverage = _coverage(results, catalog.intents)

    return RunMetrics(
        total=total,
        errors=errors,
        matched=matched,
        intent_accuracy=_ratio(matched, total - errors),
        parameter_scored=len(param_scored),
        parameter_matched=param_matched,
        parameter_accuracy=_ratio(param_matched, len(param_scored)),
        abstention_expected=abstention_expected,
        false_positives=false_positives,
        false_positive_rate=_ratio(false_positives, abstention_expected),
        false_negatives=counts.get(CaseOutcome.FALSE_NEGATIVE.value, 0),
        unstable=sum(1 for r in results if r.unstable),
        outcome_counts=dict(counts),
        coverage=coverage,
    )


def summarize_usage(results: list[CaseResult]) -> dict[str, Any]:
    return {
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "cached_calls": sum(1 for r in results if r.from_cache),
        "api_calls": sum(1 for r in results if not r.from_cache),
    }
