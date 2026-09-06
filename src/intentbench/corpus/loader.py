"""Read a corpus off disk and check it against a catalog.

Validation runs at the start of every ``run``, not just under ``validate``.
Discovering a typo'd intent identifier after 200 paid API calls is not an
acceptable failure mode.
"""

from __future__ import annotations

import difflib
from collections import Counter
from pathlib import Path

import yaml
from pydantic import ValidationError

from intentbench.corpus.schema import (
    CorpusFile,
    IssueSeverity,
    ValidationIssue,
)
from intentbench.models import Catalog, IntentDefinition


class CorpusError(Exception):
    """The corpus file could not be read or parsed at all."""


def _format_pydantic_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"  {location or '<root>'}: {error['msg']}")
    return "\n".join(lines)


def load_corpus(path: Path) -> CorpusFile:
    """Load and structurally validate a corpus YAML file."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise CorpusError(f"No corpus file at {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(f"Could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path} is not valid YAML:\n{exc}") from exc

    if raw is None:
        raise CorpusError(f"{path} is empty. A corpus needs `version: 1` and a `cases:` list.")
    if not isinstance(raw, dict):
        raise CorpusError(f"{path}: expected a mapping at the top level, got {type(raw).__name__}.")

    try:
        corpus = CorpusFile.model_validate(raw)
    except ValidationError as exc:
        raise CorpusError(f"{path} is not a valid corpus:\n{_format_pydantic_error(exc)}") from exc

    if not corpus.cases:
        raise CorpusError(f"{path} defines no cases. Add at least one entry under `cases:`.")

    return corpus


def _suggest(name: str, options: list[str]) -> str | None:
    matches = difflib.get_close_matches(name, options, n=1, cutoff=0.6)
    return matches[0] if matches else None


def validate_corpus(corpus: CorpusFile, catalog: Catalog | None = None) -> list[ValidationIssue]:
    """Check a corpus for the problems that would waste a run.

    Returns issues rather than raising, so the CLI can report every problem at
    once instead of one per invocation.
    """
    issues: list[ValidationIssue] = []

    # Duplicate phrases: the JSON report and the diff are both keyed by phrase,
    # so duplicates would silently collide.
    counts = Counter(case.phrase.strip().casefold() for case in corpus.cases)
    reported: set[str] = set()
    for index, case in enumerate(corpus.cases):
        key = case.phrase.strip().casefold()
        if counts[key] > 1 and key not in reported:
            reported.add(key)
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    message=f"duplicate phrase, appears {counts[key]} times",
                    case_index=index,
                    phrase=case.phrase,
                )
            )

    if catalog is None:
        return issues

    known = catalog.identifiers
    by_identifier: dict[str, IntentDefinition] = {i.identifier: i for i in catalog.intents}

    for index, case in enumerate(corpus.cases):
        expected = case.expect.intent
        if expected is None:
            continue

        intent = by_identifier.get(expected)
        if intent is None:
            issues.append(
                ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    message=f"expects intent {expected!r}, which is not in the catalog",
                    case_index=index,
                    phrase=case.phrase,
                    suggestion=_suggest(expected, known),
                )
            )
            continue

        parameter_names = [p.name for p in intent.parameters]
        for name in case.expect.parameters:
            if name not in parameter_names:
                issues.append(
                    ValidationIssue(
                        severity=IssueSeverity.ERROR,
                        message=(f"expects parameter {name!r}, which {expected} does not declare"),
                        case_index=index,
                        phrase=case.phrase,
                        suggestion=_suggest(name, parameter_names),
                    )
                )

    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity is IssueSeverity.ERROR for issue in issues)
