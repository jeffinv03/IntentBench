"""The `intentbench` command line.

Exit codes are load-bearing for CI. "The eval got worse" and "the tool is
broken" must never look the same:

==== =========================================================
Code Meaning
==== =========================================================
0    Run completed and met the threshold
1    Run completed but fell below ``--threshold``, or ``diff``
     found regressions under ``--fail-on-regression``
2    Configuration error — bad corpus, unparseable bundle,
     missing API key
3    Runtime error — the API was unreachable after retries
==== =========================================================
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from intentbench import __version__
from intentbench.baseline import BaselineError, diff_reports, load_report
from intentbench.catalog import LocateError, ParseError, render_catalog
from intentbench.catalog.locate import locate_catalog
from intentbench.catalog.parse import load_located_catalog
from intentbench.corpus import CorpusError, load_corpus, validate_corpus
from intentbench.corpus.loader import has_errors
from intentbench.corpus.schema import CorpusFile, IssueSeverity
from intentbench.judge import JudgeError, get_judge
from intentbench.judge.base import Judge, cost_usd
from intentbench.judge.cache import ResponseCache, cache_dir
from intentbench.models import (
    Catalog,
    FilterStats,
    IntentDefinition,
    RenderedCatalog,
)
from intentbench.report import (
    console_for,
    render_catalog_table,
    render_diff,
    render_run,
    write_json,
    write_markdown,
)
from intentbench.runner import (
    COST_CONFIRM_THRESHOLD,
    DEFAULT_CONCURRENCY,
    count_uncached,
    run_corpus,
)

EXIT_OK = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3

#: Rough per-call token estimate used only for the pre-run cost warning.
_ESTIMATED_INPUT_TOKENS_PER_CALL = 1500
_ESTIMATED_OUTPUT_TOKENS_PER_CALL = 60

app = typer.Typer(
    name="intentbench",
    help=(
        "Evaluate an Apple App Intents catalog for selection quality.\n\n"
        "intentbench reads the intent catalog out of a built .app bundle, runs a "
        "corpus of spoken-style phrases against a judge model that sees that "
        "catalog as its tool list, and reports whether the right intent was "
        "selected.\n\n"
        "It does not invoke Siri, and it does not predict Siri's behavior. "
        "Apple's router is not addressable from third-party code. What it "
        "measures is whether your catalog is unambiguous enough for a competent "
        "router to get right — which is where most selection failures actually "
        "come from."
    ),
    no_args_is_help=True,
    add_completion=False,
)

cache_app = typer.Typer(help="Inspect and clear the judge response cache.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")


class JudgeName(str, Enum):
    anthropic = "anthropic"
    openai = "openai"
    mock = "mock"


def _die(message: str, code: int = EXIT_CONFIG_ERROR) -> NoReturn:
    console = console_for("stderr")
    console.print(f"error: {message}", style="bold red")
    raise typer.Exit(code)


def _load_catalog_or_die(path: Path, *, debug: bool = False) -> Catalog:
    try:
        located = locate_catalog(path)
    except LocateError as exc:
        _die(str(exc))

    if debug:
        console = console_for("stderr")
        console.print("debug: catalog location", style="bold")
        console.print(f"  actions file : {located.actions_path}")
        console.print(f"  metadata dir : {located.metadata_dir}")
        console.print(f"  bundle       : {located.bundle_path}")
        console.print(f"  bundle id    : {located.bundle_identifier}")
        console.print(f"  version.json : {located.version_json}")
        if located.extra_metadata_dirs:
            console.print(f"  also found   : {list(located.extra_metadata_dirs)}")
        try:
            head = located.actions_path.read_bytes()[:200]
            document = json.loads(located.actions_path.read_text(encoding="utf-8"))
            console.print("  format       : JSON (utf-8)")
            console.print(f"  top-level keys: {sorted(document)}")
        except (OSError, ValueError) as exc:
            console.print(f"  format       : NOT parseable as JSON — {exc}", style="red")
            console.print(f"  first bytes  : {head!r}")
        console.print()

    try:
        return load_located_catalog(located)
    except ParseError as exc:
        _die(str(exc))


def _select_intents(
    catalog: Catalog, *, include_all: bool
) -> tuple[list[IntentDefinition], FilterStats]:
    """Apply default filtering and report exactly what it removed.

    Hidden and deprecated intents are excluded by default because Siri and
    Shortcuts will not offer them either — evaluating them would measure a
    catalog no user can reach. Counts are always reported; silent filtering
    causes "why is my intent missing" bug reports.
    """
    stats = FilterStats()
    if include_all:
        return list(catalog.intents), stats

    kept: list[IntentDefinition] = []
    for intent in catalog.intents:
        if not intent.is_discoverable:
            stats.hidden += 1
            continue
        if intent.is_deprecated:
            stats.deprecated += 1
            continue
        kept.append(intent)
    return kept, stats


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"intentbench {__version__}")
        raise typer.Exit(EXIT_OK)


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    pass


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


@app.command()
def catalog(
    path: Annotated[
        Path,
        typer.Argument(
            help=("A .app bundle, a Metadata.appintents directory, or an extract.actionsdata file.")
        ),
    ],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the full catalog as JSON to stdout.")
    ] = False,
    dump_raw: Annotated[
        str | None,
        typer.Option(
            "--dump-raw",
            metavar="IDENTIFIER",
            help="Print the original metadata node for one intent, verbatim.",
        ),
    ] = None,
    include_all: Annotated[
        bool, typer.Option("--include-all", help="Include hidden and deprecated intents.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show every parse warning.")
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Print format detection and top-level keys. Attach this to bug reports."
        ),
    ] = False,
) -> None:
    """Read and print an app's intent catalog."""
    parsed = _load_catalog_or_die(path, debug=debug)
    intents, filtered = _select_intents(parsed, include_all=include_all)

    if dump_raw is not None:
        intent = parsed.by_identifier(dump_raw)
        if intent is None:
            available = ", ".join(parsed.identifiers[:12]) or "(none)"
            _die(
                f"No intent named {dump_raw!r} in this catalog.\n"
                f"Available: {available}{' ...' if len(parsed.identifiers) > 12 else ''}"
            )
        typer.echo(json.dumps(intent.raw, indent=2, sort_keys=True))
        raise typer.Exit(EXIT_OK)

    if as_json:
        payload = parsed.model_dump(mode="json")
        payload["intents"] = [
            intent.model_dump(mode="json")
            for intent in (parsed.intents if include_all else intents)
        ]
        payload["filtered"] = filtered.model_dump(mode="json")
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(EXIT_OK)

    console = console_for()
    render_catalog_table(console, parsed, intents, filtered=filtered, verbose=verbose)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _load_and_validate(
    app_path: Path | None, corpus_path: Path, *, include_all: bool, quiet: bool = False
) -> tuple[Catalog | None, CorpusFile, list[IntentDefinition]]:
    try:
        corpus_file = load_corpus(corpus_path)
    except CorpusError as exc:
        _die(str(exc))

    parsed: Catalog | None = None
    intents: list[IntentDefinition] = []
    if app_path is not None:
        parsed = _load_catalog_or_die(app_path)
        intents, _ = _select_intents(parsed, include_all=include_all)

    # Validate against the *filtered* catalog: an expectation naming a hidden
    # intent is a real error, because the judge never sees that intent.
    scoped = parsed.model_copy(update={"intents": intents}) if parsed is not None else None
    issues = validate_corpus(corpus_file, scoped)

    if issues:
        console = console_for("stderr")
        for issue in issues:
            style = "red" if issue.severity is IssueSeverity.ERROR else "yellow"
            console.print(f"{issue.severity.value}: {issue.render()}", style=style)
        if has_errors(issues):
            raise typer.Exit(EXIT_CONFIG_ERROR)

    if not quiet:
        console = console_for()
        console.print(
            f"  {len(corpus_file.cases)} case(s) validated"
            + (f" against {len(intents)} intent(s)" if parsed is not None else ""),
            style="green",
        )

    return parsed, corpus_file, intents


@app.command()
def validate(
    corpus: Annotated[Path, typer.Option("--corpus", "-c", help="Path to the corpus YAML file.")],
    app_path: Annotated[
        Path | None,
        typer.Option("--app", "-a", help="A .app bundle to validate the corpus against."),
    ] = None,
    include_all: Annotated[
        bool, typer.Option("--include-all", help="Include hidden and deprecated intents.")
    ] = False,
) -> None:
    """Check a corpus for problems before spending anything on a run."""
    _load_and_validate(app_path, corpus, include_all=include_all)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _confirm_cost(judge: Judge, uncached: int, yes: bool) -> None:
    estimated = cost_usd(
        judge.model_id,
        uncached * _ESTIMATED_INPUT_TOKENS_PER_CALL,
        uncached * _ESTIMATED_OUTPUT_TOKENS_PER_CALL,
    )
    console = console_for("stderr")
    price = f"about ${estimated:.2f}" if estimated > 0 else "an unknown amount"
    console.print(
        f"This run will make {uncached} API calls to {judge.model_id}, costing {price}.",
        style="yellow",
    )
    if yes:
        return
    if not sys.stdin.isatty():
        console.print("Not running interactively. Re-run with --yes to proceed.", style="yellow")
        raise typer.Exit(EXIT_CONFIG_ERROR)
    if not typer.confirm("Continue?", default=True):
        raise typer.Exit(EXIT_OK)


@app.command()
def run(
    app_path: Annotated[Path, typer.Option("--app", "-a", help="A .app bundle to evaluate.")],
    corpus: Annotated[Path, typer.Option("--corpus", "-c", help="Path to the corpus YAML file.")],
    judge_name: Annotated[
        JudgeName, typer.Option("--judge", help="Which judge to route with.")
    ] = JudgeName.anthropic,
    model: Annotated[
        str | None, typer.Option("--model", help="Override the judge's default model.")
    ] = None,
    mock_script: Annotated[
        Path | None,
        typer.Option("--mock-script", help="YAML of scripted responses for --judge mock."),
    ] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write the full run report here.")
    ] = None,
    markdown_out: Annotated[
        Path | None,
        typer.Option("--markdown", help="Write a CI job summary here ($GITHUB_STEP_SUMMARY)."),
    ] = None,
    tag_filter: Annotated[
        str | None,
        typer.Option("--filter", help="Only run cases carrying these tags (comma-separated)."),
    ] = None,
    repeat: Annotated[
        int,
        typer.Option(
            "--repeat",
            min=1,
            help="Run each case N times and flag unstable ones. "
            "Every repeat is a fresh, uncached call.",
        ),
    ] = 1,
    concurrency: Annotated[
        int, typer.Option("--concurrency", min=1, help="Concurrent requests.")
    ] = DEFAULT_CONCURRENCY,
    threshold: Annotated[
        float,
        typer.Option("--threshold", min=0.0, max=1.0, help="Exit 1 below this intent accuracy."),
    ] = 0.9,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Bypass the response cache.")
    ] = False,
    include_all: Annotated[
        bool, typer.Option("--include-all", help="Include hidden and deprecated intents.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the cost confirmation.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show parse warnings.")] = False,
) -> None:
    """Run a corpus against an app's catalog and score the result."""
    parsed, corpus_file, intents = _load_and_validate(
        app_path, corpus, include_all=include_all, quiet=True
    )
    assert parsed is not None  # --app is required for run

    if not intents:
        _die(
            f"{Path(parsed.source_path).name} has no evaluable intents"
            + (" after filtering (try --include-all)." if not include_all else ".")
        )

    tags = [t.strip() for t in tag_filter.split(",")] if tag_filter else None
    cases = corpus_file.filtered(tags)
    if not cases:
        _die(f"No cases match --filter {tag_filter!r}.")

    try:
        judge = get_judge(
            judge_name.value,
            model_id=model,
            script=mock_script,
        )
    except JudgeError as exc:
        _die(str(exc))

    rendered: RenderedCatalog = render_catalog(parsed, intents)
    # The mock judge is free and deterministic; caching it would only pollute
    # the user's cache directory and make "38 cached" a meaningless line.
    cache = ResponseCache(enabled=not no_cache and judge_name is not JudgeName.mock)

    if judge_name is not JudgeName.mock:
        uncached = count_uncached(
            cases=cases,
            corpus=corpus_file,
            rendered=rendered,
            judge=judge,
            cache=cache,
            repeat=repeat,
        )
        if uncached > COST_CONFIRM_THRESHOLD:
            _confirm_cost(judge, uncached, yes)

    report = run_corpus(
        catalog=parsed,
        rendered=rendered,
        corpus=corpus_file,
        cases=cases,
        judge=judge,
        intents=intents,
        cache=cache,
        concurrency=concurrency,
        repeat=repeat,
        app_label=Path(app_path).name,
        corpus_path=str(corpus),
    )

    console = console_for()
    render_run(console, report, verbose=verbose)

    if json_out is not None:
        write_json(report, json_out)
        console.print(f"  wrote {json_out}", style="dim")
    if markdown_out is not None:
        write_markdown(report, markdown_out)
        console.print(f"  wrote {markdown_out}", style="dim")

    # Every case erroring means the API never worked — that is a runtime
    # failure, not a bad eval score.
    if report.metrics.total and report.metrics.errors == report.metrics.total:
        _die("every case failed to reach the judge; see the errors above", EXIT_RUNTIME_ERROR)

    accuracy = report.metrics.intent_accuracy
    if accuracy is not None and accuracy < threshold:
        console.print(
            f"  intent accuracy {accuracy * 100:.1f}% is below the "
            f"{threshold * 100:.0f}% threshold",
            style="red",
        )
        raise typer.Exit(EXIT_BELOW_THRESHOLD)

    raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command()
def diff(
    baseline: Annotated[Path, typer.Argument(help="The earlier results JSON.")],
    current: Annotated[Path, typer.Argument(help="The newer results JSON.")],
    fail_on_regression: Annotated[
        bool, typer.Option("--fail-on-regression", help="Exit 1 if any case regressed.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the diff as JSON.")] = False,
) -> None:
    """Compare two runs and show what changed."""
    try:
        before = load_report(baseline)
        after = load_report(current)
    except BaselineError as exc:
        _die(str(exc))

    result = diff_reports(before, after)

    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        console = console_for()
        if before.metadata.catalog_hash != after.metadata.catalog_hash:
            console.print(
                "  note: the catalog changed between these runs "
                f"({before.metadata.catalog_hash} → {after.metadata.catalog_hash})",
                style="dim yellow",
            )
        if before.metadata.model_id != after.metadata.model_id:
            console.print(
                "  note: different judge models "
                f"({before.metadata.model_id} → {after.metadata.model_id})",
                style="dim yellow",
            )
        render_diff(console, result)

    if fail_on_regression and result.has_regressions:
        raise typer.Exit(EXIT_BELOW_THRESHOLD)
    raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete every cached judge response."""
    cache = ResponseCache()
    removed = cache.clear()
    console = console_for()
    console.print(f"  cleared {removed} cached response(s) from {cache.directory}")


@cache_app.command("info")
def cache_info() -> None:
    """Show where the cache lives and how much is in it."""
    cache = ResponseCache()
    console = console_for()
    console.print(f"  location  {cache_dir()}")
    console.print(f"  entries   {cache.count()}")


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except (LocateError, ParseError, CorpusError, JudgeError, BaselineError) as exc:
        console = console_for("stderr")
        console.print(f"error: {exc}", style="bold red")
        sys.exit(EXIT_CONFIG_ERROR)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)


__all__ = ["app", "main"]
