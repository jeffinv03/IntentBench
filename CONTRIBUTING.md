# Contributing

Thanks for looking. This is a young project and the surface area is small enough
that a first contribution is realistic.

## Setup

```bash
git clone https://github.com/jeffinv03/IntentBench
cd intentbench
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

**You do not need an API key.** The entire test suite runs against a scripted
mock judge, and CI passes with no key configured. No test in this repo may make
a network call — if you add one that does, mark it `@pytest.mark.live` so it is
deselected by default.

To run the live tests once before a release, put your key in `.env`
(`cp .env.example .env`, then paste it in) and run `pytest -m live`. They cost a
few cents. The offline suite never sees that file: every other test runs from
an empty temporary directory with the credential variables unset.

**You do not need a Mac** for most work. The parser is pure Python and runs on
Linux against the committed fixtures. The only thing that needs macOS is
capturing a *new* fixture.

Before opening a PR:

```bash
ruff format src tests
ruff check src tests
mypy
pytest
```

## The highest-value contribution: a new catalog fixture

The format of `extract.actionsdata` is not documented by Apple, and it changes
with Xcode. Every catalog we have came from one machine running one OS version.
If you have an app built with a different Xcode, your catalog is genuinely
valuable — it is the main way this parser gets more robust.

### Capturing one

1. Find a built app that ships App Intents:

   ```bash
   for app in /System/Applications/*.app /Applications/*.app; do
     f="$app/Contents/Resources/Metadata.appintents/extract.actionsdata"
     [ -f "$f" ] && echo "$(basename "$app"): $(stat -f%z "$f") bytes"
   done
   ```

   On an iOS build, the directory sits at the bundle root instead:
   `MyApp.app/Metadata.appintents/`.

2. Check whether it tells us something the existing fixtures don't:

   ```bash
   intentbench catalog /path/to/Some.app --debug --verbose
   ```

   A fixture earns its place by covering a shape we don't have — a new Xcode
   version, a parameter type that lands as `unknown`, an unusual enum, an
   iOS-layout bundle. A sixth catalog that looks like the five we have does not.
   Parse warnings or `unknown` types are a strong signal it's worth adding.

3. Copy it in and write the sidecar:

   ```bash
   cp .../extract.actionsdata tests/fixtures/catalogs/<slug>.actionsdata
   cat .../Metadata.appintents/version.json     # the toolsVersion goes in the sidecar
   ```

   ```json
   {
     "source_app": "Some.app",
     "source_path": "/Applications/Some.app/Contents/Resources/Metadata.appintents/extract.actionsdata",
     "bundle_identifier": "com.example.some",
     "captured_on": "2026-09-05",
     "os": "macOS 26.6.2 (25G83)",
     "xcode": "Xcode 16.2 (16C5032a)",
     "metadata_tools_version": "17E6107",
     "metadata_format_version": "3.0",
     "redacted": false,
     "why_this_fixture": "First fixture from an iOS-layout bundle."
   }
   ```

4. Register it in `tests/conftest.py` under `REAL_CATALOGS` with its intent
   count, and add a test asserting whatever makes it distinctive.

### Licensing

**Do not commit metadata from a third-party commercial app.** Apple system apps
are fine — they ship on every Mac and we already commit five. An app you built
yourself is ideal.

If you are unsure, redact instead: replace every human-readable string with a
placeholder, keep the structure intact, set `"redacted": true` in the sidecar,
and say in `why_this_fixture` what was replaced. A redacted fixture still
exercises the parser fully, which is the part that matters.

## Reporting a parse failure

This is the expected support burden, and there is a dedicated issue template for
it. Please include the output of:

```bash
intentbench catalog /path/to/Your.app --debug --verbose
```

`--debug` prints the detected format and the file's top-level keys, which is
usually enough to diagnose it without the file itself. If you can share the
`Metadata.appintents` directory, zip it and attach it.

## Project shape

```
src/intentbench/
  models.py       every pydantic model, single source of truth
  catalog/        locate → parse → render (bundle to judge tool schemas)
  corpus/         the YAML format and its validation
  judge/          the routing protocol, its implementations, and the cache
  scoring/        outcomes, normalization, run metrics
  report/         terminal, JSON, markdown
  runner.py       ties it together and handles concurrency
  baseline.py     save, load, diff
  cli.py          typer commands and exit codes
```

`models.py` is the single source of truth. If you are adding a field, it goes
there first.

## Conventions

- **Never fail a whole parse on one bad node.** Skip it, append a
  `ParseWarning`, keep going. A catalog with 14 of 15 intents and one loud
  warning beats a stack trace.
- **Preserve raw data at every boundary.** The parser keeps `raw`; the judge
  keeps `raw_response`. Debugging next year depends on it.
- **Fail loudly on config problems, quietly on data problems.** A malformed
  corpus stops the run. One weird intent node produces a warning.
- **Unknown types are not errors.** Map to `UNKNOWN`, keep `raw_type`, render as
  a string. Apple will add types.
- **Optimize the second run.** The workflow is: run, edit a description, run
  again, read the diff. Caching and fast validation serve that loop.

## Scope

v0.1 is deliberately narrow. These are already planned and are **not** open for
PRs yet — please open an issue to discuss instead:

- collision matrix / pairwise confusion analysis (v0.2)
- corpus auto-generation from the catalog (v0.2)
- entity-resolution fuzzing via `AppIntentsTesting` (v0.3)
- multi-model cross-agreement (v0.4)
- a GitHub Action wrapper and Homebrew tap (v0.5)

Anything that would make the tool claim to predict Siri's behavior is out of
scope permanently. See the README.

## Decisions

Non-obvious choices are recorded in [DECISIONS.md](DECISIONS.md), including how
the undocumented metadata format was reverse-engineered. If you change something
the log covers, add a new entry rather than editing the old one.
