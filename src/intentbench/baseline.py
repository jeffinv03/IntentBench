"""Save, load, and diff runs.

There is one schema for results and baselines, not two: the file written by
``run --json`` is exactly the file read by ``diff``. Anything else drifts.

The diff is keyed on the phrase, which is why the corpus loader rejects
duplicate phrases — two cases with the same text could not be told apart here.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from intentbench.models import CaseResult, DiffEntry, DiffReport, RunReport

#: How an abstention is written in diff output.
ABSTAIN_LABEL = "no_matching_intent"


class BaselineError(Exception):
    """A results file could not be read or is not a valid run report."""


def save_report(report: RunReport, path: Path) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_report(path: Path) -> RunReport:
    path = Path(path).expanduser()
    if not path.is_file():
        raise BaselineError(f"No results file at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path} is not valid JSON: {exc}") from exc

    try:
        return RunReport.model_validate(payload)
    except ValidationError as exc:
        raise BaselineError(
            f"{path} is not an intentbench results file "
            f"(written by `intentbench run --json`):\n{exc}"
        ) from exc


def _label(result: CaseResult) -> str:
    return result.selected_intent or ABSTAIN_LABEL


def _entry(phrase: str, before: CaseResult | None, after: CaseResult | None) -> DiffEntry:
    return DiffEntry(
        phrase=phrase,
        before=_label(before) if before else None,
        after=_label(after) if after else None,
        before_outcome=before.outcome if before else None,
        after_outcome=after.outcome if after else None,
    )


def diff_reports(baseline: RunReport, current: RunReport) -> DiffReport:
    """Compare two runs.

    A *regression* is a case that passed in the baseline and does not now. A
    *fix* is the reverse. Cases present in only one file are reported as added
    or removed — never silently dropped, which would let a shrinking corpus
    masquerade as a clean run.
    """
    before_by_phrase = {case.phrase: case for case in baseline.cases}
    after_by_phrase = {case.phrase: case for case in current.cases}

    report = DiffReport()

    for phrase, after in after_by_phrase.items():
        before = before_by_phrase.get(phrase)
        if before is None:
            report.added.append(_entry(phrase, None, after))
            continue

        if before.passed and not after.passed:
            report.regressions.append(_entry(phrase, before, after))
        elif not before.passed and after.passed:
            report.fixes.append(_entry(phrase, before, after))
        else:
            report.unchanged += 1

    for phrase, before in before_by_phrase.items():
        if phrase not in after_by_phrase:
            report.removed.append(_entry(phrase, before, None))

    return report
