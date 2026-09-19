"""CLI smoke tests. Every command runs offline, with no API key configured."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from intentbench.cli import (
    EXIT_BELOW_THRESHOLD,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    app,
)
from tests.conftest import CORPORA, catalog_path

runner = CliRunner()

SYNTHETIC = str(catalog_path("synthetic"))
CORPUS = str(CORPORA / "synthetic.yaml")
MOCK = str(CORPORA / "synthetic-mock.yaml")


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CI must pass with no key configured, and no test may touch the real cache."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == EXIT_OK
    assert "intentbench" in result.stdout


def test_help_mentions_the_honest_limitation() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == EXIT_OK
    assert "does not invoke Siri" in result.stdout.replace("\n", " ")


# --- catalog --------------------------------------------------------------


def test_catalog_table() -> None:
    result = runner.invoke(app, ["catalog", SYNTHETIC])
    assert result.exit_code == EXIT_OK
    assert "AddItemIntent" in result.stdout
    # Filtering is reported, never silent.
    assert "excluded" in result.stdout
    # Parse warnings surface as one dim line.
    assert "skipped" in result.stdout


def test_catalog_verbose_shows_warnings() -> None:
    result = runner.invoke(app, ["catalog", SYNTHETIC, "--verbose"])
    assert result.exit_code == EXIT_OK
    assert "MalformedIntent" in result.stdout


def test_catalog_include_all() -> None:
    default = runner.invoke(app, ["catalog", SYNTHETIC])
    everything = runner.invoke(app, ["catalog", SYNTHETIC, "--include-all"])
    assert "HiddenInternalIntent" not in default.stdout
    assert "HiddenInternalIntent" in everything.stdout
    assert "OldRenameListIntent" in everything.stdout


def test_catalog_json() -> None:
    result = runner.invoke(app, ["catalog", SYNTHETIC, "--json"])
    assert result.exit_code == EXIT_OK
    payload = json.loads(result.stdout)
    assert payload["filtered"]["hidden"] == 1
    assert payload["filtered"]["deprecated"] == 1
    assert {i["identifier"] for i in payload["intents"]} >= {"AddItemIntent"}


def test_catalog_dump_raw() -> None:
    result = runner.invoke(app, ["catalog", SYNTHETIC, "--dump-raw", "AddItemIntent"])
    assert result.exit_code == EXIT_OK
    raw = json.loads(result.stdout)
    # The original node, verbatim — including keys the parser ignores.
    assert raw["identifier"] == "AddItemIntent"
    assert raw["descriptionMetadata"]["descriptionText"]["key"].startswith("Adds an item")


def test_catalog_dump_raw_unknown_identifier() -> None:
    result = runner.invoke(app, ["catalog", SYNTHETIC, "--dump-raw", "Nope"])
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_catalog_debug_prints_format_detection() -> None:
    """--debug output is what the parse-failure issue template asks for."""
    result = runner.invoke(app, ["catalog", SYNTHETIC, "--debug"])
    assert result.exit_code == EXIT_OK
    # Diagnostics go to stderr so `catalog --json --debug` stays pipeable.
    assert "JSON (utf-8)" in result.stderr
    assert "top-level keys" in result.stderr
    assert "actions file" in result.stderr


def test_catalog_missing_bundle_is_a_config_error() -> None:
    result = runner.invoke(app, ["catalog", "/nonexistent/Nope.app"])
    assert result.exit_code == EXIT_CONFIG_ERROR


@pytest.mark.parametrize("slug", ["reminders", "weather", "voicememos", "freeform", "podcasts"])
def test_catalog_runs_on_every_real_fixture(slug: str) -> None:
    result = runner.invoke(app, ["catalog", str(catalog_path(slug))])
    assert result.exit_code == EXIT_OK


# --- validate -------------------------------------------------------------


def test_validate_clean() -> None:
    result = runner.invoke(app, ["validate", "--app", SYNTHETIC, "--corpus", CORPUS])
    assert result.exit_code == EXIT_OK
    assert "4 case(s) validated" in result.stdout


def test_validate_catches_unknown_intent() -> None:
    result = runner.invoke(
        app,
        ["validate", "--app", SYNTHETIC, "--corpus", str(CORPORA / "bad-unknown-intent.yaml")],
    )
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_validate_catches_duplicates() -> None:
    result = runner.invoke(app, ["validate", "--corpus", str(CORPORA / "bad-duplicate.yaml")])
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_validate_without_a_catalog_still_checks_structure() -> None:
    result = runner.invoke(app, ["validate", "--corpus", CORPUS])
    assert result.exit_code == EXIT_OK


def test_validate_rejects_expectations_on_filtered_intents(tmp_path: Path) -> None:
    """A corpus naming a hidden intent is an error — the judge never sees it."""
    corpus = tmp_path / "phrases.yaml"
    corpus.write_text(
        "version: 1\ncases:\n  - phrase: dump diagnostics\n"
        "    expect:\n      intent: HiddenInternalIntent\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", "--app", SYNTHETIC, "--corpus", str(corpus)])
    assert result.exit_code == EXIT_CONFIG_ERROR

    allowed = runner.invoke(
        app, ["validate", "--app", SYNTHETIC, "--corpus", str(corpus), "--include-all"]
    )
    assert allowed.exit_code == EXIT_OK


# --- run ------------------------------------------------------------------


def _run_args(*extra: str) -> list[str]:
    return [
        "run",
        "--app",
        SYNTHETIC,
        "--corpus",
        CORPUS,
        "--judge",
        "mock",
        "--mock-script",
        MOCK,
        *extra,
    ]


def test_run_with_mock_judge() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0"))
    assert result.exit_code == EXIT_OK
    assert "Intent accuracy" in result.stdout
    assert "no API calls" in result.stdout


def test_run_validates_the_corpus_before_spending() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--app",
            SYNTHETIC,
            "--corpus",
            str(CORPORA / "bad-unknown-intent.yaml"),
            "--judge",
            "mock",
        ],
    )
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_run_below_threshold_exits_one() -> None:
    """Exit 1 means "the eval got worse", not "the tool is broken"."""
    result = runner.invoke(app, _run_args("--threshold", "1.0"))
    assert result.exit_code == EXIT_BELOW_THRESHOLD
    assert "below the 100% threshold" in result.stdout


def test_run_above_threshold_exits_zero() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0.5"))
    assert result.exit_code == EXIT_OK


def test_run_renders_failure_detail() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0"))
    assert "Failures" in result.stdout
    assert "sync everything" in result.stdout
    assert "expected" in result.stdout and "selected" in result.stdout


def test_run_writes_json_and_markdown(tmp_path: Path) -> None:
    json_path = tmp_path / "results.json"
    md_path = tmp_path / "summary.md"
    result = runner.invoke(
        app,
        _run_args("--threshold", "0", "--json", str(json_path), "--markdown", str(md_path)),
    )
    assert result.exit_code == EXIT_OK

    payload = json.loads(json_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["metadata"]["prompt_version"] == 1
    assert payload["metadata"]["catalog_hash"]
    assert len(payload["cases"]) == 4

    summary = md_path.read_text()
    assert "| Metric | Value |" in summary
    assert "intentbench" in summary


def test_run_tag_filter() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0", "--filter", "smoke"))
    assert result.exit_code == EXIT_OK
    assert "2/2" in result.stdout


def test_run_unknown_tag_is_a_config_error() -> None:
    result = runner.invoke(app, _run_args("--filter", "nonexistent"))
    assert result.exit_code == EXIT_CONFIG_ERROR


def test_run_repeat() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0", "--repeat", "3"))
    assert result.exit_code == EXIT_OK
    assert "3× repeat" in result.stdout


def test_run_reports_uncovered_intents() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0"))
    assert "Uncovered intents" in result.stdout
    assert "EveryTypeIntent" in result.stdout


def test_run_reports_intents_with_no_description() -> None:
    result = runner.invoke(app, _run_args("--threshold", "0"))
    assert "No description" in result.stdout
    assert "NoProseAtAllIntent" in result.stdout


def test_run_without_api_key_is_a_config_error_not_a_crash() -> None:
    result = runner.invoke(
        app, ["run", "--app", SYNTHETIC, "--corpus", CORPUS, "--judge", "anthropic"]
    )
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "ANTHROPIC_API_KEY" in result.output


def test_square_brackets_in_output_are_not_eaten_as_markup(tmp_path: Path) -> None:
    """Almost everything we print is data we did not write.

    Without markup disabled, rich reads `[anything]` as a style tag and silently
    drops it — mangling install hints like `intentbench[openai]` and any
    app-authored description that happens to contain brackets.
    """
    catalog = tmp_path / "extract.actionsdata"
    catalog.write_text(
        json.dumps(
            {
                "actions": {
                    "BracketIntent": {
                        "identifier": "BracketIntent",
                        "fullyQualifiedTypeName": "App.BracketIntent",
                        "title": {"key": "Bracket"},
                        "descriptionMetadata": {
                            "descriptionText": {"key": "Handles [square] brackets."}
                        },
                        "parameters": [],
                        "visibilityMetadata": {"isDiscoverable": True},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["catalog", str(catalog)])
    assert result.exit_code == EXIT_OK
    assert "[square]" in result.stdout


def test_run_on_a_catalog_with_no_intents(tmp_path: Path) -> None:
    corpus = tmp_path / "phrases.yaml"
    corpus.write_text(
        "version: 1\ncases:\n  - phrase: anything\n    expect:\n      intent: null\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--app",
            str(catalog_path("podcasts")),
            "--corpus",
            str(corpus),
            "--judge",
            "mock",
        ],
    )
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "no evaluable intents" in result.output


# --- diff -----------------------------------------------------------------


def _write_run(tmp_path: Path, name: str, mock_script: str) -> Path:
    path = tmp_path / name
    result = runner.invoke(
        app,
        [
            "run",
            "--app",
            SYNTHETIC,
            "--corpus",
            CORPUS,
            "--judge",
            "mock",
            "--mock-script",
            mock_script,
            "--threshold",
            "0",
            "--json",
            str(path),
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    return path


def test_diff_no_change(tmp_path: Path) -> None:
    baseline = _write_run(tmp_path, "a.json", MOCK)
    current = _write_run(tmp_path, "b.json", MOCK)
    result = runner.invoke(app, ["diff", str(baseline), str(current)])
    assert result.exit_code == EXIT_OK
    assert "Regressions (0)" in result.stdout
    assert "Fixes (0)" in result.stdout
    assert "Unchanged  4" in result.stdout


def test_diff_detects_regression_and_fails_when_asked(tmp_path: Path) -> None:
    baseline = _write_run(tmp_path, "a.json", MOCK)

    # Change exactly one answer, so exactly one case regresses.
    regressed = tmp_path / "regressed.yaml"
    regressed.write_text(
        Path(MOCK)
        .read_text()
        .replace(
            '  "add milk to my shopping list":\n    intent: AddItemIntent',
            '  "add milk to my shopping list":\n    intent: CreateShoppingListIntent',
        ),
        encoding="utf-8",
    )
    current = _write_run(tmp_path, "b.json", str(regressed))

    result = runner.invoke(app, ["diff", str(baseline), str(current)])
    assert result.exit_code == EXIT_OK
    assert "Regressions (1)" in result.stdout
    assert "AddItemIntent → CreateShoppingListIntent" in result.stdout

    strict = runner.invoke(app, ["diff", str(baseline), str(current), "--fail-on-regression"])
    assert strict.exit_code == EXIT_BELOW_THRESHOLD


def test_diff_json(tmp_path: Path) -> None:
    baseline = _write_run(tmp_path, "a.json", MOCK)
    result = runner.invoke(app, ["diff", str(baseline), str(baseline), "--json"])
    assert result.exit_code == EXIT_OK
    payload = json.loads(result.stdout)
    assert payload["unchanged"] == 4


def test_diff_missing_file_is_a_config_error(tmp_path: Path) -> None:
    baseline = _write_run(tmp_path, "a.json", MOCK)
    result = runner.invoke(app, ["diff", str(baseline), str(tmp_path / "nope.json")])
    assert result.exit_code == EXIT_CONFIG_ERROR


# --- cache ----------------------------------------------------------------


def test_cache_info_and_clear() -> None:
    info = runner.invoke(app, ["cache", "info"])
    assert info.exit_code == EXIT_OK
    assert "entries" in info.stdout

    cleared = runner.invoke(app, ["cache", "clear"])
    assert cleared.exit_code == EXIT_OK
    assert "cleared" in cleared.stdout


# --- the worked example ---------------------------------------------------


def test_the_documented_example_actually_works() -> None:
    """The README quickstart must not rot."""
    root = Path(__file__).resolve().parent.parent / "examples" / "weather-app"
    result = runner.invoke(
        app,
        [
            "run",
            "--app",
            str(root / "weather.actionsdata"),
            "--corpus",
            str(root / "phrases.yaml"),
            "--judge",
            "mock",
            "--mock-script",
            str(root / "mock-responses.yaml"),
            "--threshold",
            "0",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "Intent accuracy" in result.stdout


def test_no_test_configured_an_api_key() -> None:
    assert "ANTHROPIC_API_KEY" not in os.environ
