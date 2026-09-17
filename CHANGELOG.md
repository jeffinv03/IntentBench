# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Credentials can be pasted into a gitignored `.env` file in the working
  directory instead of exported. `.env.example` is the template. Only
  `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID`, and `OPENAI_API_KEY` are read,
  and exported variables take precedence.
- `ANTHROPIC_WORKSPACE_ID` is sent as the `anthropic-workspace-id` header, for
  API keys that are not scoped to a workspace.

### Fixed

- `--repeat N` now makes N fresh judge calls per case and bypasses the response
  cache. Previously every repeat shared one cache key, so a warm cache replayed
  the same answer N times and a case could never be flagged unstable; with a
  cold cache, how many repeats hit the API depended on thread timing. The cost
  confirmation now counts cases × N regardless of the cache.
- Usage is counted per attempt before repeats are collapsed, so the API-call and
  cached-call totals are exact.
- Cached responses no longer contribute tokens to the run's usage, so a fully
  cached run reports no cost instead of the cost of the original run.
- The Anthropic judge works with `anthropic` 1.x, which removed `temperature`
  from `messages.create()` and made every request fail with a `TypeError`.
  Temperature is now sent in the request body, and dropped once for models that
  reject it (Opus 4.7 and later).

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
- Reports name uncovered intents, intents that reach the judge with no usable
  description at all, and intents whose title or description is an unresolved
  `.strings` localization key rather than prose (11% of intent strings across
  the catalogs shipped on macOS 26).
- Five real catalog fixtures from Apple system apps plus one hand-written
  synthetic fixture, each with a provenance sidecar.

### Known limitations

- Not Siri. The result is a proxy signal — see the README.
- Dates and durations are compared as literal text.
- Entity references are not resolved.
- Extra parameters the model supplies are recorded but do not fail a case.

[Unreleased]: https://github.com/jeffinv/intentbench/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jeffinv/intentbench/releases/tag/v0.1.0
