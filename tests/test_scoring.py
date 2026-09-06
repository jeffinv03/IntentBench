"""Scoring tests — table-driven across every CaseOutcome, both abstentions included."""

from __future__ import annotations

from typing import Any

import pytest

from intentbench.corpus.schema import CorpusCase
from intentbench.models import (
    NO_MATCH,
    CaseOutcome,
    CaseResult,
    Catalog,
    IntentDefinition,
    ParameterDefinition,
    ParameterType,
)
from intentbench.scoring import aggregate, normalize_value, resolve_repeats, score_case
from intentbench.scoring.match import compare_parameters, values_match


def make_case(
    phrase: str = "add milk",
    intent: str | None = "AddItemIntent",
    parameters: dict[str, Any] | None = None,
) -> CorpusCase:
    return CorpusCase.model_validate(
        {"phrase": phrase, "expect": {"intent": intent, "parameters": parameters or {}}}
    )


# --- every outcome --------------------------------------------------------

OUTCOME_TABLE = [
    pytest.param(
        make_case(parameters={"item": "milk"}),
        "AddItemIntent",
        {"item": "milk"},
        CaseOutcome.MATCH,
        id="match",
    ),
    pytest.param(
        make_case(intent=None), None, {}, CaseOutcome.MATCH, id="match-correct-abstention"
    ),
    pytest.param(
        make_case(), "CreateShoppingListIntent", {}, CaseOutcome.WRONG_INTENT, id="wrong-intent"
    ),
    pytest.param(
        make_case(intent=None),
        "AddItemIntent",
        {},
        CaseOutcome.FALSE_POSITIVE,
        id="false-positive-expected-null",
    ),
    pytest.param(
        make_case(), None, {}, CaseOutcome.FALSE_NEGATIVE, id="false-negative-model-abstained"
    ),
    pytest.param(
        make_case(parameters={"item": "milk"}),
        "AddItemIntent",
        {"item": "bread"},
        CaseOutcome.PARAM_MISMATCH,
        id="param-mismatch",
    ),
]


@pytest.mark.parametrize(("case", "selected", "params", "expected"), OUTCOME_TABLE)
def test_outcomes(
    case: CorpusCase, selected: str | None, params: dict[str, Any], expected: CaseOutcome
) -> None:
    result = score_case(case, locale="en-US", selected_intent=selected, selected_parameters=params)
    assert result.outcome is expected


def test_error_outcome() -> None:
    result = score_case(
        make_case(), locale="en-US", selected_intent=None, selected_parameters={}, error="boom"
    )
    assert result.outcome is CaseOutcome.ERROR
    assert result.error == "boom"


def test_abstention_sentinel_maps_to_none() -> None:
    """The judge answers with a tool name; no_matching_intent means abstain."""
    result = score_case(
        make_case(intent=None),
        locale="en-US",
        selected_intent=NO_MATCH,
        selected_parameters={},
    )
    assert result.outcome is CaseOutcome.MATCH
    assert result.selected_intent is None


def test_intent_is_scored_before_parameters() -> None:
    """Wrong intent is WRONG_INTENT even when the parameters happen to match."""
    result = score_case(
        make_case(parameters={"item": "milk"}),
        locale="en-US",
        selected_intent="SomethingElse",
        selected_parameters={"item": "milk"},
    )
    assert result.outcome is CaseOutcome.WRONG_INTENT
    assert result.parameter_comparisons == []


def test_missing_expected_parameter_is_a_mismatch() -> None:
    result = score_case(
        make_case(parameters={"item": "milk"}),
        locale="en-US",
        selected_intent="AddItemIntent",
        selected_parameters={},
    )
    assert result.outcome is CaseOutcome.PARAM_MISMATCH


def test_extra_parameters_are_recorded_but_do_not_fail() -> None:
    result = score_case(
        make_case(parameters={"item": "milk"}),
        locale="en-US",
        selected_intent="AddItemIntent",
        selected_parameters={"item": "milk", "quantity": 3},
    )
    assert result.outcome is CaseOutcome.MATCH
    assert result.extra_parameters == {"quantity": 3}


# --- normalization --------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("Milk", "milk"),
        ("  milk  ", "milk"),
        ("shopping   list", "shopping list"),
        (2, "2"),
        (2, 2.0),
        (2.5, "2.5"),
        (True, "yes"),
        (True, "TRUE"),
        (True, "on"),
        (True, 1),
        (False, "no"),
        (False, "off"),
        (False, 0),
    ],
)
def test_values_match(expected: Any, actual: Any) -> None:
    assert values_match(expected, actual)


@pytest.mark.parametrize(
    ("expected", "actual"),
    [("milk", "bread"), (2, 3), (True, False), ("milk", None), (1, "one")],
)
def test_values_do_not_match(expected: Any, actual: Any) -> None:
    assert not values_match(expected, actual)


def test_normalize_nested_structures() -> None:
    assert normalize_value(["A", " b "]) == ["a", "b"]
    assert normalize_value({"K": " V "}) == {"K": "v"}


def test_date_comparison_is_literal_and_flagged() -> None:
    """v0.1 compares dates as opaque strings, and says so."""
    intent = IntentDefinition(
        identifier="X",
        type_name="X",
        parameters=[
            ParameterDefinition(name="date", type=ParameterType.DATE, raw_type="primitive<8>")
        ],
        raw={},
    )
    comparisons = compare_parameters({"date": "6pm"}, {"date": "18:00"}, intent)
    assert comparisons[0].matched is False
    assert comparisons[0].literal_date_comparison is True

    same = compare_parameters({"date": "6pm"}, {"date": "6PM"}, intent)
    assert same[0].matched is True


# --- repeats and variance -------------------------------------------------


def _result(intent: str | None, outcome: CaseOutcome) -> CaseResult:
    return CaseResult(phrase="p", expected_intent="A", selected_intent=intent, outcome=outcome)


def test_single_run_passes_through() -> None:
    collapsed = resolve_repeats([_result("A", CaseOutcome.MATCH)])
    assert collapsed.repeats == 1
    assert collapsed.unstable is False
    assert collapsed.observed_selections == ["A"]


def test_majority_match_wins() -> None:
    collapsed = resolve_repeats(
        [
            _result("A", CaseOutcome.MATCH),
            _result("A", CaseOutcome.MATCH),
            _result("B", CaseOutcome.WRONG_INTENT),
        ]
    )
    assert collapsed.outcome is CaseOutcome.MATCH
    assert collapsed.agreement == 2
    assert collapsed.unstable is True, "a split result is signal, not noise"


def test_majority_failure_reports_the_dominant_failure() -> None:
    collapsed = resolve_repeats(
        [
            _result("B", CaseOutcome.WRONG_INTENT),
            _result("B", CaseOutcome.WRONG_INTENT),
            _result("A", CaseOutcome.MATCH),
        ]
    )
    assert collapsed.outcome is CaseOutcome.WRONG_INTENT
    assert collapsed.unstable is True


def test_unanimous_runs_are_stable() -> None:
    collapsed = resolve_repeats([_result("A", CaseOutcome.MATCH)] * 3)
    assert collapsed.unstable is False
    assert collapsed.agreement == 3


def test_all_errors_stay_an_error() -> None:
    collapsed = resolve_repeats([_result(None, CaseOutcome.ERROR)] * 2)
    assert collapsed.outcome is CaseOutcome.ERROR


# --- aggregate ------------------------------------------------------------


def _scored(
    expected: str | None, selected: str | None, outcome: CaseOutcome, **kwargs: Any
) -> CaseResult:
    return CaseResult(
        phrase=kwargs.pop("phrase", f"{expected}->{selected}"),
        expected_intent=expected,
        selected_intent=selected,
        outcome=outcome,
        **kwargs,
    )


def test_metrics() -> None:
    results = [
        _scored("A", "A", CaseOutcome.MATCH),
        _scored("A", "B", CaseOutcome.WRONG_INTENT),
        _scored(None, None, CaseOutcome.MATCH),
        _scored(None, "A", CaseOutcome.FALSE_POSITIVE),
        _scored("B", None, CaseOutcome.FALSE_NEGATIVE),
        _scored("A", None, CaseOutcome.ERROR),
    ]
    metrics = aggregate(results)
    assert metrics.total == 6
    assert metrics.errors == 1
    assert metrics.matched == 2
    # Errors are excluded from the denominator: an unreachable API is not a
    # wrong answer.
    assert metrics.intent_accuracy == pytest.approx(2 / 5)
    assert metrics.abstention_expected == 2
    assert metrics.false_positives == 1
    assert metrics.false_positive_rate == pytest.approx(0.5)
    assert metrics.false_negatives == 1


def test_parameter_accuracy_is_conditioned_on_the_right_intent() -> None:
    from intentbench.models import ParameterComparison

    results = [
        _scored(
            "A",
            "A",
            CaseOutcome.MATCH,
            expected_parameters={"x": 1},
            parameter_comparisons=[ParameterComparison(name="x", matched=True)],
        ),
        _scored(
            "A",
            "A",
            CaseOutcome.PARAM_MISMATCH,
            expected_parameters={"x": 1},
            parameter_comparisons=[ParameterComparison(name="x", matched=False)],
        ),
        # Wrong intent — must not enter the parameter denominator at all.
        _scored("A", "B", CaseOutcome.WRONG_INTENT, expected_parameters={"x": 1}),
    ]
    metrics = aggregate(results)
    assert metrics.parameter_scored == 2
    assert metrics.parameter_matched == 1
    assert metrics.parameter_accuracy == pytest.approx(0.5)


def test_coverage_flags_untargeted_intents(synthetic: Catalog) -> None:
    results = [_scored("AddItemIntent", "AddItemIntent", CaseOutcome.MATCH)]
    metrics = aggregate(results, catalog=synthetic)
    covered = {c.identifier for c in metrics.coverage if c.covered}
    assert covered == {"AddItemIntent"}
    assert "TitleOnlyIntent" in metrics.uncovered_intents
    assert "AddItemIntent" not in metrics.uncovered_intents


def test_accuracy_is_none_when_everything_errored() -> None:
    metrics = aggregate([_scored("A", None, CaseOutcome.ERROR)])
    assert metrics.intent_accuracy is None
    assert metrics.parameter_accuracy is None
    assert metrics.false_positive_rate is None
