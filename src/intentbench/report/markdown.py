"""A Markdown report sized for `$GITHUB_STEP_SUMMARY`.

A summary table and a failures table. Nothing else — a CI summary that scrolls
is a CI summary nobody reads.
"""

from __future__ import annotations

from pathlib import Path

from intentbench.models import CaseOutcome, RunReport


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _fmt_params(params: dict[str, object]) -> str:
    if not params:
        return ""
    inner = ", ".join(f"{k}: {v}" for k, v in sorted(params.items()))
    return f" {{ {inner} }}"


def render_markdown(report: RunReport) -> str:
    metrics = report.metrics
    meta = report.metadata

    lines: list[str] = []
    title = meta.app_label or Path(meta.catalog_source).name
    lines.append(f"## intentbench — {title}")
    lines.append("")
    lines.append(
        f"`{meta.model_id}` · catalog `{meta.catalog_hash}` · {meta.timestamp:%Y-%m-%d %H:%M UTC}"
    )
    lines.append("")

    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(
        f"| Intent accuracy | {metrics.matched}/{metrics.total - metrics.errors} "
        f"({_percent(metrics.intent_accuracy)}) |"
    )
    lines.append(
        f"| Parameter accuracy | {metrics.parameter_matched}/{metrics.parameter_scored} "
        f"({_percent(metrics.parameter_accuracy)}) |"
    )
    lines.append(f"| False positives | {metrics.false_positives}/{metrics.abstention_expected} |")
    if metrics.errors:
        lines.append(f"| Errors | {metrics.errors} |")
    if metrics.unstable:
        lines.append(f"| Unstable cases | {metrics.unstable} |")
    lines.append("")

    failures = [case for case in report.cases if not case.passed]
    if failures:
        lines.append(f"### Failures ({len(failures)})")
        lines.append("")
        lines.append("| Phrase | Expected | Selected | Outcome |")
        lines.append("| --- | --- | --- | --- |")
        for case in failures:
            expected = (case.expected_intent or "_no match_") + _fmt_params(
                case.expected_parameters
            )
            if case.outcome is CaseOutcome.ERROR:
                selected = f"error: {case.error or 'unknown'}"
            else:
                selected = (case.selected_intent or "_no match_") + _fmt_params(
                    case.selected_parameters
                )
            phrase = case.phrase.replace("|", "\\|")
            lines.append(f"| {phrase} | {expected} | {selected} | `{case.outcome.value}` |")
        lines.append("")
    else:
        lines.append("All cases passed.")
        lines.append("")

    uncovered = metrics.uncovered_intents
    if uncovered:
        lines.append(f"### Uncovered intents ({len(uncovered)})")
        lines.append("")
        lines.append(", ".join(f"`{name}`" for name in uncovered))
        lines.append("")

    if report.weak_descriptions:
        lines.append(f"### No usable description ({len(report.weak_descriptions)})")
        lines.append("")
        lines.append(", ".join(f"`{name}`" for name in report.weak_descriptions))
        lines.append("")

    if report.unresolved_localization:
        lines.append(f"### Unresolved localization keys ({len(report.unresolved_localization)})")
        lines.append("")
        lines.append(
            "These carry `.strings` lookup keys instead of prose, and were treated as missing."
        )
        lines.append("")
        lines.append(", ".join(f"`{n}`" for n in report.unresolved_localization))
        lines.append("")

    return "\n".join(lines)


def write_markdown(report: RunReport, path: Path) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
