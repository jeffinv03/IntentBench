"""Diff tests: regression, fix, added, removed, and no change."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from intentbench.baseline import BaselineError, diff_reports, load_report, save_report
from intentbench.models import (
    CaseOutcome,
    CaseResult,
    RunMetadata,
    RunMetrics,
    RunReport,
)


def make_report(cases: list[tuple[str, str | None, CaseOutcome]], **meta: object) -> RunReport:
    metadata = RunMetadata(
        intentbench_version="0.1.0",
        judge="mock",
        model_id=str(meta.get("model_id", "mock")),
        prompt_version=1,
        catalog_hash=str(meta.get("catalog_hash", "abc123")),
        catalog_source="Fake.app",
        timestamp=datetime(2026, 9, 5, tzinfo=UTC),
    )
    return RunReport(
        metadata=metadata,
        metrics=RunMetrics(total=len(cases)),
        cases=[
            CaseResult(
                phrase=phrase, expected_intent="X", selected_intent=selected, outcome=outcome
            )
            for phrase, selected, outcome in cases
        ],
    )


def test_regression_detected() -> None:
    before = make_report([("a", "X", CaseOutcome.MATCH)])
    after = make_report([("a", "Y", CaseOutcome.WRONG_INTENT)])
    diff = diff_reports(before, after)
    assert len(diff.regressions) == 1
    assert diff.regressions[0].before == "X"
    assert diff.regressions[0].after == "Y"
    assert diff.has_regressions is True


def test_fix_detected() -> None:
    before = make_report([("a", None, CaseOutcome.FALSE_NEGATIVE)])
    after = make_report([("a", "X", CaseOutcome.MATCH)])
    diff = diff_reports(before, after)
    assert len(diff.fixes) == 1
    assert diff.fixes[0].before == "no_matching_intent"
    assert diff.fixes[0].after == "X"
    assert diff.has_regressions is False


def test_no_change() -> None:
    before = make_report([("a", "X", CaseOutcome.MATCH), ("b", "Y", CaseOutcome.WRONG_INTENT)])
    diff = diff_reports(before, before)
    assert diff.unchanged == 2
    assert diff.regressions == []
    assert diff.fixes == []


def test_added_case_is_reported_not_ignored() -> None:
    before = make_report([("a", "X", CaseOutcome.MATCH)])
    after = make_report([("a", "X", CaseOutcome.MATCH), ("b", "Y", CaseOutcome.MATCH)])
    diff = diff_reports(before, after)
    assert [entry.phrase for entry in diff.added] == ["b"]
    assert diff.unchanged == 1


def test_removed_case_is_reported_not_ignored() -> None:
    """A shrinking corpus must never masquerade as a clean run."""
    before = make_report([("a", "X", CaseOutcome.MATCH), ("b", "Y", CaseOutcome.WRONG_INTENT)])
    after = make_report([("a", "X", CaseOutcome.MATCH)])
    diff = diff_reports(before, after)
    assert [entry.phrase for entry in diff.removed] == ["b"]
    assert diff.removed[0].before == "Y"
    assert diff.unchanged == 1


def test_failing_to_differently_failing_is_not_a_regression() -> None:
    before = make_report([("a", "Y", CaseOutcome.WRONG_INTENT)])
    after = make_report([("a", None, CaseOutcome.FALSE_NEGATIVE)])
    diff = diff_reports(before, after)
    assert diff.regressions == []
    assert diff.fixes == []
    assert diff.unchanged == 1


# --- round trip: one schema for results and baselines ---------------------


def test_report_round_trips_through_disk(tmp_path: Path) -> None:
    report = make_report([("a", "X", CaseOutcome.MATCH)])
    path = tmp_path / "results.json"
    save_report(report, path)
    loaded = load_report(path)
    assert loaded.cases[0].phrase == "a"
    assert loaded.metadata.catalog_hash == "abc123"
    assert diff_reports(report, loaded).unchanged == 1


def test_load_rejects_non_json(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="not valid JSON"):
        load_report(path)


def test_load_rejects_unrelated_json(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(BaselineError, match="not an intentbench results file"):
        load_report(path)


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="No results file"):
        load_report(tmp_path / "nope.json")


def test_raw_response_is_excluded_from_serialization(tmp_path: Path) -> None:
    """Raw payloads stay in memory for debugging but never bloat the baseline."""
    report = make_report([("a", "X", CaseOutcome.MATCH)])
    path = tmp_path / "results.json"
    save_report(report, path)
    assert "raw_response" not in path.read_text()
