# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-09-05

First release.

### Added

- `intentbench catalog` — read the intent catalog out of a `.app` bundle, a
  `Metadata.appintents` directory, or an `extract.actionsdata` file. Table,
  `--json`, `--dump-raw <identifier>`, and `--debug` output. Hidden and
  deprecated intents are filtered by default, with counts always reported and
  `--include-all` to override.
- `intentbench validate` — check a corpus against a catalog before spending
  anything. Catches unknown intent identifiers (with "did you mean"), unknown
  parameters, duplicate phrases, malformed YAML, empty corpora, and unknown
  keys. Also runs automatically at the start of every `run`.
- `intentbench run` — score a corpus against a judge model that sees the catalog
  as its tool list. `--filter` by tag, `--repeat N` with instability detection,
  `--concurrency`, `--threshold`, `--json`, `--markdown`.
- `intentbench diff` — three-section comparison of two runs (regressions, fixes,
  unchanged), with added and removed cases reported separately, and
  `--fail-on-regression`.
- `intentbench cache clear` / `cache info` — manage the content-addressed
  response cache at `$XDG_CACHE_HOME/intentbench`.
- Judges: Anthropic (default, `claude-sonnet-4-6`), OpenAI (experimental), and a
  scripted offline mock used by the entire test suite.
- Abstention as a first-class expectation: `intent: null` in a corpus, and a
  synthetic `no_matching_intent` tool in every rendered catalog.
- Reports name uncovered intents and intents that reach the judge with no
  human-authored description at all.
- Five real catalog fixtures from Apple system apps plus one hand-written
  synthetic fixture, each with a provenance sidecar.

### Known limitations

- Not Siri. The result is a proxy signal — see the README.
- Dates and durations are compared as literal text.
- Entity references are not resolved.
- Extra parameters the model supplies are recorded but do not fail a case.

[Unreleased]: https://github.com/jeffinv/intentbench/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jeffinv/intentbench/releases/tag/v0.1.0
