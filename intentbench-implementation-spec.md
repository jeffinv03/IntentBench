# IntentBench — Implementation Specification

**Version:** 0.1 spec, draft 1
**Audience:** the coding agent implementing this repo
**Status:** authoritative for v0.1 scope. Decisions marked `[DECIDED]` are settled — do not relitigate them. Items marked `[SPIKE]` are genuine unknowns that must be resolved empirically before dependent work begins.

---

## 0. Read this first

You are building a command-line eval harness for Apple's App Intents. The person commissioning this knows the Model Context Protocol (MCP) and LLM eval tooling well, and knows almost nothing about iOS development. Write the code and the docs accordingly: explain iOS-specific concepts where they surface, and do not assume Swift or Xcode familiarity anywhere in the developer-facing output.

This is also their first open source project. Repo hygiene, README quality, and a clean first-run experience matter as much as the core logic. A tool that works but is confusing to install has failed.

### 0.1 Domain primer

If you are unfamiliar with App Intents, this mapping is sufficient to build the tool:

| App Intents concept | MCP equivalent | What it actually is |
|---|---|---|
| App intent | Tool | A Swift type with typed parameters and a `perform()` method |
| App entity | Resource | A lightweight representation of an app's data object (a note, a contact) |
| App enum | Enum in a tool schema | A closed set of options |
| Siri / Spotlight / Shortcuts | Host | The system surfaces that invoke intents |
| The app itself | Server | Ships the intents inside its bundle |
| `Metadata.appintents/extract.actionsdata` | `tools/list` response | A machine-readable file, written at build time, listing every intent with parameters, types, defaults, and enum cases |

When a user speaks to Siri, four things happen in order:

1. Speech becomes text.
2. Siri picks one intent and fills its parameters. **No public API exposes this step.**
3. The app resolves entity references ("the note about Mira").
4. The app runs `perform()`.

Apple ships a testing framework (`AppIntentsTesting`, WWDC 2026) covering steps 3 and 4. Apple's guidance for step 2 is to test manually by speaking to a device. Step 2 is what this project addresses.

### 0.2 What this tool is, in one sentence

Read the intent catalog out of a built `.app` bundle, run a corpus of natural-language phrases against a judge model that sees that catalog as its tool list, and report whether the right intent was selected — with a diff against the previous run.

### 0.3 What this tool is NOT

State this in the README, first section, not buried:

- It does not invoke Siri. Apple's router is not addressable from third-party code.
- Results are a **proxy signal**. The judge is a stand-in model, not Apple's planner.
- The proxy is defensible because most selection failures are *catalog-quality* failures — overlapping titles, ambiguous parameter descriptions, two intents that read the same. Those are properties of the metadata and are model-independent. Cross-model agreement (v0.4) is the strongest form of this signal.
- Do not oversell. Every claim of the form "this predicts Siri behavior" is false and will be caught by an iOS developer within a day of the repo going public.

---

## 1. Settled decisions

`[DECIDED]` — implement as written. If you believe one is wrong, note it in `DECISIONS.md` and proceed anyway; the human will review.

| Decision | Choice | Rationale |
|---|---|---|
| Name | `intentbench` | Package, CLI binary, and repo name all identical |
| Language | Python 3.11+ | The maintainer does not write Swift. `plistlib` is stdlib and likely needed for the parser. Fast iteration on the eval logic, which is the actual product |
| Distribution v0.1 | `uvx intentbench` / `pipx install intentbench`, published to PyPI | Zero-install invocation for trial users |
| Distribution v0.5 | Homebrew tap | This is how Mac developers expect to get CLI tools. Not in v0.1 scope |
| CLI framework | `typer` | Type-hint driven, good `--help` output for free |
| Terminal output | `rich` | Tables and color; degrades correctly when piped |
| Config/corpus format | YAML | Human-authored, comment-friendly |
| Data models | `pydantic` v2 | Validation of user-authored corpora is a first-class need |
| Default judge | Anthropic API, `claude-sonnet-4-6` | Tool-calling support, cheap enough for CI |
| Judge mechanism | Native tool-calling, not JSON-in-prose | The catalog becomes a real tool list. Closest structural analogue to a router, and avoids output-format drift entirely |
| Test framework | `pytest` | — |
| License | MIT | Maximum adoption, no ambiguity for a first project |
| Python packaging | `pyproject.toml`, hatchling backend | — |
| Lint/format | `ruff` (lint + format) | One tool, fast |
| Type checking | `mypy --strict` on `src/` | — |

**Explicitly out of scope for v0.1** — do not build these, do not add abstractions "ready for" them beyond what is described in §11:

- Collision matrix / pairwise confusion analysis (v0.2)
- Entity-resolution fuzzing via `AppIntentsTesting` (v0.3)
- Multi-model cross-agreement (v0.4)
- GitHub Action wrapper (v0.5)
- Corpus auto-generation from the catalog (v0.2)
- Any Swift code
- Any interaction with a simulator or a device
- Web UI, dashboard, or hosted service

---

## 2. Repository layout

```
intentbench/
├── README.md
├── LICENSE                     MIT
├── CHANGELOG.md                Keep a Changelog format
├── CONTRIBUTING.md
├── DECISIONS.md                ADR log, one entry per significant choice
├── pyproject.toml
├── .github/
│   ├── workflows/ci.yml
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── catalog_parse_failure.md    See §4.6 — this will be the most common issue
├── src/intentbench/
│   ├── __init__.py
│   ├── cli.py                  typer app, argument parsing, exit codes
│   ├── models.py               All pydantic models, single source of truth
│   ├── catalog/
│   │   ├── __init__.py
│   │   ├── locate.py           Find the metadata file inside a bundle
│   │   ├── parse.py            Raw file → normalized Catalog
│   │   └── render.py           Catalog → judge tool schemas
│   ├── corpus/
│   │   ├── __init__.py
│   │   ├── schema.py           Corpus YAML models + validation
│   │   └── loader.py           Load, validate, resolve against a catalog
│   ├── judge/
│   │   ├── __init__.py
│   │   ├── base.py             Judge protocol
│   │   ├── anthropic.py
│   │   ├── openai.py           v0.1: implement, mark experimental
│   │   ├── mock.py             Scripted, offline, used by the whole test suite
│   │   └── cache.py            Content-addressed response cache
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── match.py            Intent match, parameter match, normalization
│   │   └── aggregate.py        Run-level metrics
│   ├── report/
│   │   ├── __init__.py
│   │   ├── terminal.py         rich tables
│   │   ├── json_out.py         Machine-readable, also the baseline format
│   │   └── markdown.py         For CI job summaries
│   └── baseline.py             Save/load/diff against a previous run
├── tests/
│   ├── fixtures/
│   │   ├── catalogs/           Committed real + synthetic metadata files
│   │   └── corpora/
│   ├── test_catalog_parse.py
│   ├── test_corpus_schema.py
│   ├── test_scoring.py
│   ├── test_baseline_diff.py
│   └── test_cli_smoke.py
└── examples/
    ├── notes-app/              Worked example with a real bundle's catalog
    └── phrases.example.yaml
```

---

## 3. Phase 0 — the spike (do this before anything else)

**This phase gates everything downstream. Do not write the parser before completing it.**

### 3.1 The unknown

`[SPIKE]` The internal format of `extract.actionsdata` is not publicly documented by Apple. Known facts:

- Xcode runs a build step (`appintentsmetadataprocessor`) that emits a `Metadata.appintents` directory into the built product.
- That directory contains `extract.actionsdata`.
- On macOS app bundles the path is `SomeApp.app/Contents/Resources/Metadata.appintents/extract.actionsdata`.
- It contains intent identifiers, titles, descriptions, parameter definitions with types and defaults, enum definitions with cases, and output type information.
- Third-party projects have parsed it successfully, so it is tractable.

`[SPIKE]` Unknown and must be determined empirically:

1. Serialization format. Candidates in order of likelihood: binary plist, JSON, XML plist. Test with `file`, then `plutil -p`, then `python3 -c "import plistlib; ..."`.
2. The exact key structure and nesting.
3. Whether an iOS `.app` bundle places the directory at the bundle root rather than under `Contents/Resources/` (iOS bundles are flat; macOS bundles are not). **Assume they differ and handle both.**
4. Whether the schema differs across Xcode versions. Note the Xcode/OS version alongside every fixture you capture.
5. How schema-conformant intents (`@AppIntent(schema:)`) are represented versus custom intents. This distinction matters for scoring later, so capture it now.

### 3.2 Spike procedure

Run on macOS. Sample bundles are already on the machine:

```bash
# Survey what's available
for app in /System/Applications/*.app /Applications/*.app; do
  f="$app/Contents/Resources/Metadata.appintents/extract.actionsdata"
  [ -f "$f" ] && echo "$(basename "$app"): $(stat -f%z "$f") bytes"
done

# Identify format
file "/System/Applications/Notes.app/Contents/Resources/Metadata.appintents/extract.actionsdata"
plutil -p "/System/Applications/Notes.app/Contents/Resources/Metadata.appintents/extract.actionsdata" | head -100
```

Also inspect the sibling files in `Metadata.appintents/` — there may be more than one, and one may carry the human-readable strings.

### 3.3 Spike deliverables

Before proceeding to Phase 1, produce:

1. `DECISIONS.md` entry documenting the format, with a representative structure excerpt.
2. At least **three** committed fixtures in `tests/fixtures/catalogs/` from different apps with different shapes (one large, one small, one with enums if you can find it). Record the source app and OS/Xcode version in a sidecar `.meta.json`.
3. One **synthetic** fixture you construct by hand, covering every field the parser handles, including cases the real fixtures don't hit.
4. A written answer to: *does this file contain everything needed to render a tool schema — parameter names, types, optionality, descriptions, enum cases?* If a needed field is absent, that is a scope-changing finding. Stop and report it rather than working around it.

**Licensing note:** do not commit fixtures derived from a third-party commercial app. Prefer Apple system apps or, better, build a small sample app yourself. If in doubt, redact strings and commit only the structural skeleton, documenting what was redacted.

---

## 4. Phase 1 — catalog extraction

### 4.1 Public surface

```python
def load_catalog(path: Path) -> Catalog:
    """Accepts a .app bundle, a Metadata.appintents directory,
    or an extract.actionsdata file directly."""
```

Accepting all three input shapes is deliberate: it makes the tool debuggable when bundle layout assumptions fail, and lets users share a single file when filing a parse bug.

### 4.2 Models (`models.py`)

```python
class ParameterType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DURATION = "duration"
    ENUM = "enum"
    ENTITY = "entity"
    FILE = "file"
    UNKNOWN = "unknown"          # Preserve raw_type; never drop the parameter


class ParameterDefinition(BaseModel):
    name: str
    title: str | None
    description: str | None
    type: ParameterType
    raw_type: str                 # Verbatim from the file, always retained
    is_optional: bool
    default_value: Any | None
    enum_id: str | None           # FK into Catalog.enums
    entity_type: str | None


class IntentDefinition(BaseModel):
    identifier: str               # Stable key used everywhere
    type_name: str                # Swift type name
    title: str | None
    description: str | None
    schema_id: str | None         # e.g. "reminders.createReminder"; None = custom
    parameters: list[ParameterDefinition]
    is_discoverable: bool         # False if hidden from Shortcuts/Siri
    raw: dict[str, Any] = Field(exclude=True)   # Full original node


class EnumDefinition(BaseModel):
    identifier: str
    cases: list[EnumCase]         # value + display title


class Catalog(BaseModel):
    source_path: Path
    bundle_identifier: str | None
    intents: list[IntentDefinition]
    enums: dict[str, EnumDefinition]
    entities: dict[str, EntityDefinition]
    parse_warnings: list[ParseWarning]
    extractor_version: str | None
```

### 4.3 Parser rules

- **Never fail the whole parse on one bad node.** Skip the node, append a `ParseWarning` with the node's identifier and the reason, continue. A catalog with 14 of 15 intents plus one loud warning is far more useful than a stack trace.
- **Always keep `raw`.** When someone files a parse bug, `--dump-raw <identifier>` must be able to show exactly what the file said.
- **Unknown types are not errors.** Map to `ParameterType.UNKNOWN`, keep `raw_type`, and render the parameter to the judge as a string with a note. Apple will add types; the tool must not break when they do.
- `parse_warnings` surfaces in terminal output as a single dim line: `3 intents skipped (run with --verbose)`.

### 4.4 Filtering

Exclude from the evaluated catalog by default, with `--include-all` to override:

- Intents marked as not discoverable / hidden from Shortcuts.
- Deprecated intents (`DeprecatedAppIntent`, if representable in the metadata — verify during the spike).

Report counts of what was filtered. Silent filtering causes "why is my intent missing" bug reports.

### 4.5 CLI

```
intentbench catalog ./MyApp.app                 # table of intents
intentbench catalog ./MyApp.app --json          # full catalog to stdout
intentbench catalog ./MyApp.app --dump-raw AddItemIntent
```

`intentbench catalog` shipping before any eval logic gives you a working, useful command on day two and something to demo.

### 4.6 Parse-failure issue template

Because the file format is undocumented and version-dependent, parse failures are the expected support burden. The issue template must ask for: macOS version, Xcode version if known, the output of `intentbench catalog <path> --debug`, and the `Metadata.appintents` directory zipped if shareable. Wire `--debug` to print the format detection result and top-level key names.

---

## 5. Phase 2 — corpus

### 5.1 Format

```yaml
version: 1

defaults:
  locale: en-US

cases:
  - phrase: "add milk to my shopping list"
    expect:
      intent: AddItemIntent
      parameters:
        item: "milk"
        list: "shopping"

  - phrase: "remind me to call mom at 6"
    expect:
      intent: CreateReminderIntent
      parameters:
        title: "call mom"
      # Parameters not listed are not scored. Omit anything ambiguous.

  - phrase: "what's the weather in Tokyo"
    expect:
      intent: null          # Nothing in this app should match
    note: "Out-of-scope probe — catches over-eager matching"

  - phrase: "open my notes"
    expect:
      intent: OpenNotesIntent
    tags: [smoke, navigation]
```

Design points, each load-bearing:

- **`intent: null` is a first-class case.** Over-matching is a real failure mode and most eval harnesses ignore it. Supporting abstention is a differentiator and costs almost nothing.
- **Partial parameter expectations.** Scoring only listed parameters means authors aren't forced to specify things the phrase doesn't determine.
- **`tags`** enable `--filter smoke` for a fast subset in pre-commit versus the full corpus in CI.
- **`version: 1`** at the top so the format can evolve without silent misinterpretation.

### 5.2 Validation

`intentbench validate --app ./MyApp.app --corpus phrases.yaml` must catch, before any API spend:

- Expected intent identifiers that don't exist in the catalog (with a "did you mean" suggestion via `difflib.get_close_matches`).
- Expected parameter names that don't exist on that intent.
- Duplicate phrases.
- Empty corpus, malformed YAML, unknown top-level keys.

Run this validation automatically at the start of every `run` too. Failing after spending money on 200 API calls is unacceptable.

---

## 6. Phase 3 — the judge

### 6.1 Protocol

```python
class Judge(Protocol):
    name: str
    model_id: str

    def select(
        self,
        phrase: str,
        tools: list[ToolSchema],
        locale: str,
    ) -> JudgeResult: ...


class JudgeResult(BaseModel):
    selected_intent: str | None       # None = abstained
    parameters: dict[str, Any]
    raw_response: dict[str, Any] = Field(exclude=True)
    latency_ms: int
    input_tokens: int
    output_tokens: int
    from_cache: bool = False
```

### 6.2 Catalog → tool schemas (`catalog/render.py`)

Each intent becomes one tool:

- `name`: the intent identifier, sanitized to `^[a-zA-Z0-9_-]{1,64}$`. Keep a bidirectional map — do not lose the original identifier.
- `description`: the intent's description; fall back to title; fall back to a humanized type name. **Record which fallback was used** — intents with no description are exactly the ones that will fail selection, and naming that in the report is genuinely valuable output.
- `input_schema`: JSON Schema from parameters. Enums become `enum` arrays. Entity parameters become strings with the entity type named in the description. Optional parameters stay out of `required`.

Plus one synthetic tool, always appended:

```json
{
  "name": "no_matching_intent",
  "description": "Use this when the request does not correspond to any of the other available actions.",
  "input_schema": {"type": "object", "properties": {}}
}
```

This is how abstention is captured. Use `tool_choice: {"type": "any"}` so the model must choose something — with the escape hatch present, forcing a choice is safe and removes an entire class of unparseable responses.

### 6.3 Prompt

Keep the system prompt minimal and version it as a constant (`PROMPT_VERSION = 1`), because it participates in the cache key and in result comparability.

```
You are the request router for a mobile app. The user has spoken a request
out loud. Choose exactly one action that best matches their request and fill
in its parameters from what they said.

Only use information present in the request. Do not invent parameter values.
If no action matches, use no_matching_intent.
```

Then a single user message containing only the phrase. Nothing else — no few-shot examples, no chain of thought. You are measuring whether the *catalog* is unambiguous, and additional prompt scaffolding masks exactly the defects you're trying to surface.

`temperature=0` always. Not configurable in v0.1.

### 6.4 Cache

Content-addressed, on by default, at `~/.cache/intentbench/` (respect `XDG_CACHE_HOME`).

Key: `sha256(model_id + PROMPT_VERSION + normalized_tool_schemas_json + phrase + locale)`.

The tool schemas must be in the key — changing an intent description must invalidate. Serialize them canonically (sorted keys, no whitespace) so key stability doesn't depend on dict ordering.

`--no-cache` bypasses; `intentbench cache clear` empties. Cache hits make re-runs free, which matters enormously for the iterate-on-descriptions workflow this tool exists to enable.

### 6.5 Concurrency and cost

- Default 8 concurrent requests, `--concurrency N`.
- Exponential backoff with jitter on 429 and 5xx; 5 attempts; surface the final failure per-case rather than aborting the run.
- Print an estimated cost before running when the corpus exceeds 50 uncached cases, and require `--yes` to skip the confirmation in non-interactive contexts.
- Always print actual token usage and cost at the end.

### 6.6 MockJudge

Reads a YAML map of phrase → scripted result. Every test in the suite uses it. **No test may make a network call.** This also gives contributors a way to work on scoring and reporting with no API key at all, which materially lowers the contribution barrier.

---

## 7. Phase 4 — scoring

### 7.1 Per-case outcome

```python
class CaseOutcome(str, Enum):
    MATCH = "match"                       # Correct intent (or correct abstention)
    WRONG_INTENT = "wrong_intent"
    FALSE_POSITIVE = "false_positive"     # Expected null, model picked one
    FALSE_NEGATIVE = "false_negative"     # Expected an intent, model abstained
    PARAM_MISMATCH = "param_mismatch"     # Right intent, wrong parameters
    ERROR = "error"                       # API or parse failure
```

Intent match is scored before parameter match. A case with the right intent and wrong parameters is `PARAM_MISMATCH` — a distinct, less severe failure than picking the wrong intent, and the report must not collapse them.

### 7.2 Parameter matching

Normalize both sides before comparison:

- Strings: casefold, strip, collapse internal whitespace.
- Numbers: numeric comparison, not string.
- Booleans: accept `true/yes/on/1` equivalences.
- Dates and durations: `[SPIKE]` decide during Phase 4. Default position for v0.1: **compare dates as opaque strings and document the limitation.** Do not build a datetime normalizer in v0.1 — it is a rabbit hole and date parameters are a minority case. If a corpus expects a date, emit a warning that comparison is literal.

Only parameters listed in `expect.parameters` are scored. Extra parameters the model supplied are recorded in the report but do not fail the case in v0.1.

### 7.3 Run metrics

- `intent_accuracy` = MATCH / (total − ERROR)
- `parameter_accuracy` = cases with all expected params correct / cases where intent was correct
- `false_positive_rate` over cases expecting null
- Per-intent breakdown: for each intent in the catalog, how many corpus cases target it and how many passed. **Flag intents with zero coverage** — that list is one of the most immediately actionable things the tool produces.

### 7.4 Repeats and variance

`--repeat N` (default 1) runs each case N times. When N > 1:

- Report per-case consistency (how many of N agreed).
- Score a case as MATCH only if the majority matched.
- Flag any case with split results as `unstable` — surfaced in its own report section.

Instability is signal, not noise. A phrase that selects differently across identical runs is a phrase whose catalog entry is ambiguous.

---

## 8. Phase 5 — reporting and baselines

### 8.1 Terminal output

The default view is a summary, not a wall of rows. Only failures get detail:

```
intentbench 0.1.0 · MyApp.app · claude-sonnet-4-6

  Intent accuracy      38/42   90.5%
  Parameter accuracy   35/38   92.1%
  False positives       0/4

  Failures
  ─────────────────────────────────────────────────────────
  "remind me to call mom at 6"
      expected  CreateReminderIntent
      selected  CreateNoteIntent
  
  "add milk to my shopping list"
      expected  AddItemIntent { item: milk, list: shopping }
      selected  AddItemIntent { item: milk, list: groceries }

  Uncovered intents (3)
      DeleteListIntent, ArchiveNoteIntent, ShareListIntent

  4 API calls · 38 cached · $0.003
```

Detect non-TTY and drop color and progress bars automatically.

### 8.2 JSON output

`--json <path>` writes the full run: metadata (version, model, prompt version, catalog hash, timestamp), every case with its outcome and the judge's raw selection, and aggregate metrics. **This is also the baseline format** — one schema, not two.

### 8.3 Markdown output

`--markdown <path>` writes a report suitable for `$GITHUB_STEP_SUMMARY`. Summary table plus a failures table. Nothing else.

### 8.4 Baselines and diffing

```bash
intentbench run --app ./MyApp.app --corpus phrases.yaml --json results.json
intentbench diff baseline.json results.json
```

Diff output has exactly three sections, and the first is the one that matters:

```
Regressions (2)
  "remind me to call mom at 6"      CreateReminder → CreateNote
  "clear my list"                   ClearListIntent → no_matching_intent

Fixes (1)
  "start a new note"                no_matching_intent → CreateNoteIntent

Unchanged  39
```

Cases present in only one file are listed separately as `added` / `removed`, never silently ignored.

### 8.5 Exit codes

| Code | Meaning |
|---|---|
| 0 | Run completed, threshold met |
| 1 | Run completed, below `--threshold` (default 0.9) or regressions found in `diff --fail-on-regression` |
| 2 | Configuration error — bad corpus, unparseable bundle, missing API key |
| 3 | Runtime error — API unreachable after retries |

Distinguishing 1 from 2 is what makes CI usable: "the eval got worse" and "the tool is broken" must not look the same.

---

## 9. Testing requirements

- **Unit tests, no network, ever.** MockJudge everywhere. CI must pass with no API key configured.
- Parser: one test per committed fixture asserting intent count and spot-checked fields; one test per malformed-input class (truncated file, wrong format, missing directory, empty catalog).
- Scoring: table-driven across every `CaseOutcome` including both abstention directions.
- Diff: regression, fix, added, removed, and no-change.
- CLI smoke: `catalog`, `validate`, `run --judge mock`, `diff` all exit 0 on fixtures.
- One optional integration test behind `-m live`, skipped by default, requiring `ANTHROPIC_API_KEY`.

CI matrix: Python 3.11, 3.12, 3.13 on `ubuntu-latest` and `macos-latest`. The parser must be pure-Python and work on Linux against committed fixtures, so contributors without a Mac can still work on everything except capturing new fixtures.

---

## 10. README structure

Order matters. Write it in this sequence:

1. **One sentence** on what it does.
2. **The gap**, in three sentences: Apple's testing framework covers execution; selection is manual; this automates selection.
3. **The honest limitation**, immediately — before installation, before examples. It is not Siri. It is a proxy. Here is why the proxy is useful. Burying this reads as a sales pitch; leading with it reads as engineering.
4. **Quickstart**, four commands or fewer, working end to end.
5. **Example output**, as a code block. This is what people screenshot.
6. **Corpus format** with a full annotated example.
7. **CI usage** with a copyable workflow snippet.
8. **How it works**, brief, with the four-layer diagram described in text.
9. **Roadmap**, as an explicit list of what's coming. Signals the project is alive.
10. **Contributing**, especially "how to capture and submit a new catalog fixture" — that is the highest-value contribution outsiders can make.

Do not put a badge wall at the top. One CI badge and one PyPI badge.

---

## 11. Build order and acceptance criteria

Ship each phase to `main` independently. Each is demoable on its own.

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Format spike | Three real fixtures committed, format documented in `DECISIONS.md`, §3.3 question answered |
| 1 | `intentbench catalog` | Parses all fixtures, table + JSON output, warnings surface, `--dump-raw` works |
| 2 | `intentbench validate` | Catches all five validation classes in §5.2 with useful messages |
| 3 | `intentbench run --judge mock` | End-to-end scoring with no network, terminal + JSON output |
| 4 | Anthropic judge + cache | Real runs work, cache hits verified, cost reporting accurate |
| 5 | `intentbench diff` | Three-section diff, `--fail-on-regression`, correct exit codes |
| 6 | Polish and release | README complete, CI green, published to PyPI, tagged `v0.1.0` |

Phases 1–3 are the critical path to something demoable. Phase 4 is where it becomes real. Do not start Phase 5 before Phase 4 is merged.

---

## 12. Guidance on judgment calls

When the spec is silent or the spec is wrong:

- **Prefer failing loudly on config problems and quietly on data problems.** A malformed corpus should stop the run. One weird intent node should produce a warning.
- **Preserve raw data at every boundary.** Parser keeps `raw`. Judge keeps `raw_response`. Every debugging session for the next year depends on this.
- **Optimize the second run, not the first.** The workflow this tool enables is: run, edit an intent description, run again. Caching, fast validation, and clear diffs serve that loop.
- **When tempted to add a feature, check §1's out-of-scope list.** If it's there, open an issue instead and move on. Scope creep is the main failure mode for a first open source project.
- **If a `[SPIKE]` resolves badly** — for example the metadata file turns out to lack parameter descriptions — stop and report rather than engineering around it. That finding changes the product, and the human needs to make that call.

Log every non-trivial deviation from this spec in `DECISIONS.md` with the reason. That file is also useful raw material for the launch post.
