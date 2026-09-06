"""Terminal output.

The default view is a summary, not a wall of rows. Passing cases are a count;
only failures earn detail. Color and progress bars are dropped automatically
when stdout is not a TTY, so piping to a file or a CI log stays readable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from intentbench import __version__
from intentbench.catalog.render import looks_like_localization_key
from intentbench.models import (
    CaseOutcome,
    CaseResult,
    Catalog,
    DiffReport,
    FilterStats,
    IntentDefinition,
    RunReport,
)

_OUTCOME_STYLES: dict[CaseOutcome, str] = {
    CaseOutcome.MATCH: "green",
    CaseOutcome.WRONG_INTENT: "red",
    CaseOutcome.FALSE_POSITIVE: "yellow",
    CaseOutcome.FALSE_NEGATIVE: "yellow",
    CaseOutcome.PARAM_MISMATCH: "magenta",
    CaseOutcome.ERROR: "red",
}


def console_for(stream: str = "stdout", *, force_plain: bool = False) -> Console:
    """A console that degrades correctly when piped.

    ``markup=False`` is deliberate and load-bearing. Almost everything printed
    here is data we did not write — intent identifiers, app-authored
    descriptions, corpus phrases — and rich would otherwise read ``[anything]``
    in that text as a style tag and silently swallow it. Styling is applied via
    the explicit ``style=`` argument instead.
    """
    file = sys.stderr if stream == "stderr" else sys.stdout
    is_tty = file.isatty()
    return Console(
        file=file,
        no_color=force_plain or not is_tty,
        highlight=False,
        markup=False,
        soft_wrap=False,
    )


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _fmt_params(params: dict[str, object]) -> str:
    if not params:
        return ""
    inner = ", ".join(f"{k}: {v}" for k, v in sorted(params.items()))
    return f" {{ {inner} }}"


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def render_catalog_table(
    console: Console,
    catalog: Catalog,
    intents: list[IntentDefinition],
    *,
    filtered: FilterStats | None = None,
    verbose: bool = False,
) -> None:
    header = Text()
    header.append(f"intentbench {__version__}", style="bold")
    header.append(" · ")
    header.append(Path(catalog.source_path).name)
    if catalog.bundle_identifier:
        header.append(f" · {catalog.bundle_identifier}", style="dim")
    if catalog.extractor_version:
        header.append(f" · metadata tools {catalog.extractor_version}", style="dim")
    console.print(header)
    console.print()

    if not intents:
        console.print("  This app declares no app intents.", style="yellow")
        console.print()
        return

    table = Table(box=None, pad_edge=False, show_edge=False, header_style="bold dim")
    table.add_column("Identifier", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Params", justify="right")
    table.add_column("Description", overflow="fold", max_width=54)

    for intent in intents:
        flags = []
        if not intent.is_discoverable:
            flags.append("hidden")
        if intent.is_deprecated:
            flags.append("deprecated")
        if intent.schema_id:
            flags.append(intent.schema_id)

        identifier = Text(intent.identifier)
        if flags:
            identifier.append(f"\n{' · '.join(flags)}", style="dim italic")

        description = intent.description or ""
        if looks_like_localization_key(description):
            cell = Text(f"{description}\n(unresolved localization key)", style="yellow")
        elif description:
            cell = Text(description)
        else:
            cell = Text("(no description)", style="dim italic")

        table.add_row(
            identifier,
            intent.title or Text("—", style="dim"),
            str(len(intent.parameters)),
            cell,
        )

    console.print(table)
    console.print()

    parts = [_plural(len(intents), "intent")]
    if catalog.enums:
        parts.append(_plural(len(catalog.enums), "enum"))
    if catalog.entities:
        parts.append(_plural(len(catalog.entities), "entity", "entities"))
    console.print("  " + " · ".join(parts), style="dim")

    if filtered and filtered.total:
        parts = []
        if filtered.hidden:
            parts.append(f"{filtered.hidden} hidden")
        if filtered.deprecated:
            parts.append(f"{filtered.deprecated} deprecated")
        console.print(f"  {' · '.join(parts)} excluded (use --include-all to show)", style="dim")

    _print_warnings(console, catalog, verbose)
    console.print()


def _print_warnings(console: Console, catalog: Catalog, verbose: bool) -> None:
    warnings = catalog.parse_warnings
    if not warnings:
        return
    if verbose:
        console.print(f"  {len(warnings)} parse warning(s):", style="yellow")
        for warning in warnings:
            console.print(f"    {warning.kind} {warning.node}: {warning.reason}", style="dim")
    else:
        console.print(
            f"  {_plural(len(warnings), 'node')} skipped (run with --verbose)",
            style="dim yellow",
        )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _render_failure(console: Console, case: CaseResult) -> None:
    console.print(f'  "{case.phrase}"')

    if case.outcome is CaseOutcome.ERROR:
        console.print(f"      error     {case.error}", style="red")
        console.print()
        return

    expected = (case.expected_intent or "no_matching_intent") + _fmt_params(
        case.expected_parameters
    )
    selected = (case.selected_intent or "no_matching_intent") + _fmt_params(
        case.selected_parameters
    )
    console.print(f"      expected  {expected}", style="dim")
    console.print(f"      selected  {selected}", style=_OUTCOME_STYLES[case.outcome])

    literal_dates = [c.name for c in case.parameter_comparisons if c.literal_date_comparison]
    if literal_dates:
        console.print(
            f"      note      {', '.join(literal_dates)} compared as literal text "
            "(v0.1 does not normalize dates)",
            style="dim yellow",
        )
    console.print()


def render_run(console: Console, report: RunReport, *, verbose: bool = False) -> None:
    metrics = report.metrics
    meta = report.metadata

    header = Text()
    header.append(f"intentbench {meta.intentbench_version}", style="bold")
    header.append(" · ")
    header.append(meta.app_label or Path(meta.catalog_source).name)
    header.append(" · ")
    header.append(meta.model_id, style="cyan")
    if meta.repeat > 1:
        header.append(f" · {meta.repeat}× repeat", style="dim")
    console.print(header)
    console.print()

    scored = metrics.total - metrics.errors
    console.print(
        f"  Intent accuracy      {metrics.matched}/{scored}   {_percent(metrics.intent_accuracy)}"
    )
    console.print(
        f"  Parameter accuracy   {metrics.parameter_matched}/{metrics.parameter_scored}   "
        f"{_percent(metrics.parameter_accuracy)}"
    )
    console.print(f"  False positives      {metrics.false_positives}/{metrics.abstention_expected}")
    if metrics.errors:
        console.print(f"  Errors               {metrics.errors}", style="red")

    failures = [case for case in report.cases if not case.passed]
    if failures:
        console.print()
        console.print("  Failures", style="bold")
        console.print("  " + "─" * 57, style="dim")
        for case in failures:
            _render_failure(console, case)

    unstable = [case for case in report.cases if case.unstable]
    if unstable:
        console.print(f"  Unstable ({len(unstable)})", style="bold yellow")
        console.print(
            "  These phrases routed differently across identical runs — the "
            "catalog entry is ambiguous.",
            style="dim",
        )
        for case in unstable:
            selections = ", ".join(sel or "no_matching_intent" for sel in case.observed_selections)
            console.print(f'      "{case.phrase}" → {selections}', style="dim")
        console.print()

    uncovered = metrics.uncovered_intents
    if uncovered:
        console.print(f"  Uncovered intents ({len(uncovered)})", style="bold")
        console.print(f"      {', '.join(uncovered)}", style="dim")
        console.print()

    if report.weak_descriptions:
        console.print(f"  No description ({len(report.weak_descriptions)})", style="bold")
        console.print(
            "      These intents have no usable description or title; the judge "
            "saw only a name derived from the Swift type.",
            style="dim",
        )
        console.print(f"      {', '.join(report.weak_descriptions)}", style="dim")
        console.print()

    if report.unresolved_localization:
        console.print(
            f"  Unresolved localization keys ({len(report.unresolved_localization)})",
            style="bold yellow",
        )
        console.print(
            "      These intents carry .strings lookup keys instead of prose "
            "(e.g. STOP_RECORDING_INTENT_DESCRIPTION). They were treated as "
            "missing.",
            style="dim",
        )
        console.print(f"      {', '.join(report.unresolved_localization)}", style="dim")
        console.print()

    usage = report.usage
    if meta.judge == "mock":
        line = f"  {usage.api_calls + usage.cached_calls} scripted selections · no API calls"
    else:
        line = (
            f"  {usage.api_calls} API call{'s' if usage.api_calls != 1 else ''} · "
            f"{usage.cached_calls} cached"
        )
        if usage.estimated_cost_usd > 0:
            line += f" · ${usage.estimated_cost_usd:.4f}"
        elif usage.api_calls:
            line += " · cost unknown (no price on file for this model)"
    console.print(line, style="dim")

    if verbose and report.parse_warnings:
        console.print()
        console.print(f"  {len(report.parse_warnings)} parse warning(s):", style="yellow")
        for warning in report.parse_warnings:
            console.print(f"    {warning.kind} {warning.node}: {warning.reason}", style="dim")
    console.print()


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def render_diff(console: Console, diff: DiffReport) -> None:
    """Three sections, and the first is the one that matters."""
    console.print()
    console.print(f"Regressions ({len(diff.regressions)})", style="bold red")
    for entry in diff.regressions:
        console.print(f'  "{entry.phrase}"')
        console.print(f"      {entry.before} → {entry.after}", style="red")
    if not diff.regressions:
        console.print("  none", style="dim")
    console.print()

    console.print(f"Fixes ({len(diff.fixes)})", style="bold green")
    for entry in diff.fixes:
        console.print(f'  "{entry.phrase}"')
        console.print(f"      {entry.before} → {entry.after}", style="green")
    if not diff.fixes:
        console.print("  none", style="dim")
    console.print()

    console.print(f"Unchanged  {diff.unchanged}", style="dim")

    if diff.added:
        console.print()
        console.print(f"Added ({len(diff.added)})", style="bold")
        for entry in diff.added:
            console.print(f'  "{entry.phrase}" → {entry.after}', style="dim")

    if diff.removed:
        console.print()
        console.print(f"Removed ({len(diff.removed)})", style="bold")
        for entry in diff.removed:
            console.print(f'  "{entry.phrase}" (was {entry.before})', style="dim")

    console.print()
