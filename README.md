# intentbench

[![CI](https://github.com/jeffinv/intentbench/actions/workflows/ci.yml/badge.svg)](https://github.com/jeffinv/intentbench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/intentbench)](https://pypi.org/project/intentbench/)

Test whether your app's App Intents are distinct enough to be picked correctly —
automatically, in CI, without talking to a device.

## The gap

Apple's `AppIntentsTesting` framework covers what happens *after* an intent is
chosen: entity resolution and `perform()`. It does not cover the step before
that — the router deciding which of your intents a spoken request means, and
what to put in its parameters. Apple's guidance for testing that step is to
speak to a device and see what happens. `intentbench` automates it.

## It is not Siri, and this matters

Read this before anything else, because every other claim depends on it.

**`intentbench` does not invoke Siri.** Apple's router is not addressable from
third-party code — there is no public API for "here is a phrase, which intent
would you pick". Nothing in this tool touches it.

What it does instead: reads your intent catalog out of a built `.app`, hands it
to a judge model as a tool list, and asks that model to route each phrase. The
result is a **proxy signal**. The judge is a stand-in, not Apple's planner, and
a passing run does not mean Siri will get it right.

**So why is the proxy worth anything?** Because most selection failures are not
model failures — they are *catalog* failures. Two intents whose titles read the
same. A description that never says what the action does. A parameter named
`target` with no explanation of what it targets. Those defects are properties of
your metadata, and they are model-independent: they trip up any router, Apple's
included. If a competent model with your full catalog in front of it cannot tell
two intents apart, Siri has no better information to work with than it did.

What this tool is genuinely good at is finding the catalog defects, and telling
you the moment one of your edits makes selection worse. What it cannot do is
predict Apple's behavior. Any claim of the form "this predicts Siri" is false,
and an iOS developer will catch it within a day.

## Quickstart

```bash
uvx intentbench catalog /System/Applications/Weather.app     # see what an app ships
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env                   # see "API keys" below
uvx intentbench validate --app ./MyApp.app --corpus phrases.yaml   # free, catches typos
uvx intentbench run --app ./MyApp.app --corpus phrases.yaml --json results.json
```

`intentbench catalog` needs no API key and no corpus, so it works the moment you
install it. Point it at any app on your Mac.

Prefer a persistent install? `pipx install intentbench`, or
`pip install 'intentbench[anthropic]'`.

### API keys

`catalog`, `validate`, and `run --judge mock` need no key. `run` with a real
judge does. intentbench reads keys from a `.env` file in the directory you run
it from, so the simplest setup is to paste them there:

1. Create an Anthropic API key at [console.anthropic.com](https://console.anthropic.com)
   under **Settings → API Keys**. It is shown once, so copy it straight away.
   API usage is billed separately from a Claude.ai subscription, so the account
   needs credits.
2. Create `.env` next to your corpus. In a clone of this repo,
   `cp .env.example .env` gives you a commented template. Otherwise, create it
   by hand:

   ```dotenv
   ANTHROPIC_API_KEY=sk-ant-...
   # Only if the API says "This API key is not scoped to a workspace":
   # ANTHROPIC_WORKSPACE_ID=...    (Console → Settings → Workspaces)
   # Only for --judge openai:
   # OPENAI_API_KEY=sk-...
   ```

3. **Keep it out of git.** This repo already ignores `.env`. In your own
   project, add it: `echo .env >> .gitignore`. Optionally, run `chmod 600 .env`
   so only your user can read it.

Only the three variables above are read from the file. Anything already
exported in your shell takes precedence, so `export ANTHROPIC_API_KEY=...` and CI
secrets (see below) keep working unchanged. If a key ever leaks, delete it in
the Console and create a new one.

> **What is a `.app` bundle?** On macOS and iOS, an app is a directory named
> `MyApp.app`. When Xcode builds one, it writes a `Metadata.appintents`
> directory inside it listing every app intent, with parameters, types, defaults
> and enum cases. That file is what `intentbench` reads — it is essentially a
> `tools/list` response for your app. You do not need Xcode installed to use
> this tool, only a built app. You can also point `--app` at the
> `Metadata.appintents` directory, or at the `extract.actionsdata` file inside
> it, which is handy for sharing a single file on a bug report.

## Example output

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

  No description (2)
      These intents have neither a description nor a title; the judge saw only
      a name derived from the Swift type.
      ArchiveNoteIntent, SyncAllIntent

  4 API calls · 38 cached · $0.0031
```

Three things in there are worth more than the headline number:

- **Failures name the wrong pick, not just the miss.** "Selected
  `CreateNoteIntent`" tells you the two descriptions overlap. That is a fixable
  bug with an address.
- **Uncovered intents** are intents no phrase in your corpus targets. You cannot
  have tested what you never wrote a phrase for.
- **No description** lists intents the model saw only as a humanized Swift type
  name. Across every app shipped on macOS 26, 22% of intents have no description
  at all — these are reliably the ones that lose selection races.

A fourth section appears when it applies: **unresolved localization keys**.
Some apps ship catalogs whose title and description fields contain `.strings`
lookup keys (`STOP_RECORDING_INTENT_DESCRIPTION`) rather than prose — 11% of
intent strings on macOS 26 do. Those are treated as missing rather than handed
to the judge, and named separately, because the fix is different: wire up your
strings file, rather than write a description.

## Corpus format

A corpus is a YAML file of things someone might say, paired with what your app
should do about it.

```yaml
version: 1              # required, so the format can change without silent misreads

defaults:
  locale: en-US

cases:
  - phrase: "add milk to my shopping list"
    expect:
      intent: AddItemIntent
      parameters:
        item: "milk"
        list: "shopping"

  # Parameter expectations are PARTIAL — only what you list is scored. "at 6"
  # could be 6am or 6pm, so dueDate simply isn't listed and isn't judged.
  - phrase: "remind me to call mom at 6"
    expect:
      intent: CreateReminderIntent
      parameters:
        title: "call mom"

  # `intent: null` means "nothing in this app should match". Over-matching is a
  # real failure mode, and scoring abstention is most of what makes this useful.
  - phrase: "what's the weather in Tokyo"
    expect:
      intent: null
    note: "Out-of-scope probe — catches over-eager matching."

  # Tags carve out a fast subset: --filter smoke in a pre-commit hook, the whole
  # corpus in CI.
  - phrase: "open my notes"
    expect:
      intent: OpenNotesIntent
    tags: [smoke, navigation]
```

Values are compared leniently: strings casefold and collapse whitespace, numbers
compare numerically, and `true`/`yes`/`on`/`1` are equivalent. Dates are compared
as literal text in v0.1 — see [Limitations](#limitations).

Check a corpus before spending anything:

```bash
intentbench validate --app ./MyApp.app --corpus phrases.yaml
```

This catches intent identifiers that do not exist (with a "did you mean"
suggestion), parameters that intent does not declare, duplicate phrases, and
malformed YAML. The same validation runs at the start of every `run`, because
discovering a typo after 200 paid API calls is not acceptable.

A fully annotated example lives in
[`examples/phrases.example.yaml`](examples/phrases.example.yaml), and a working
one against a real catalog is in [`examples/weather-app/`](examples/weather-app/).

## CI usage

```yaml
name: intents
on: [pull_request]

jobs:
  intentbench:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4

      # Build your app however you normally do; intentbench needs the .app.
      - run: xcodebuild -scheme MyApp -derivedDataPath build

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pipx install 'intentbench[anthropic]'

      - name: Cache judge responses
        uses: actions/cache@v4
        with:
          path: ~/.cache/intentbench
          key: intentbench-${{ hashFiles('phrases.yaml') }}

      - name: Run the eval
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          intentbench run \
            --app build/Build/Products/Debug/MyApp.app \
            --corpus phrases.yaml \
            --json results.json \
            --markdown "$GITHUB_STEP_SUMMARY" \
            --threshold 0.9 \
            --yes

      # Optional: fail only on regressions against a committed baseline.
      - run: intentbench diff baseline.json results.json --fail-on-regression
```

Exit codes make the two failure kinds distinguishable, which is the whole point:

| Code | Meaning |
|---|---|
| 0 | Ran, met the threshold |
| 1 | Ran, fell below `--threshold` — or `diff --fail-on-regression` found regressions |
| 2 | Configuration error — bad corpus, unparseable bundle, missing API key |
| 3 | Runtime error — the API was unreachable after retries |

Caching the response directory matters. The cache key covers the model, the
prompt, the full rendered tool list, and the phrase — so an unchanged catalog
re-runs for free, and editing one intent's description invalidates exactly what
it should.

`--repeat N` is the exception: it never reads or writes the cache, so every
repeat is a fresh, billed call. Repeats measure run-to-run variance, which a
cached answer replayed N times cannot have. Budget for cases × N calls.

## How it works

Four layers, each independently inspectable:

1. **Extract.** Locate `Metadata.appintents/extract.actionsdata` inside the
   bundle and parse it into a normalized catalog. macOS bundles nest it under
   `Contents/Resources/`; iOS bundles are flat and put it at the root. Both are
   handled. One malformed node produces a warning, never a stack trace, and the
   original JSON for every intent is retained for `--dump-raw`.

2. **Render.** Turn each intent into one tool: identifier becomes the tool name,
   description becomes the tool description (falling back to title, then to a
   humanized type name — and recording which), parameters become a JSON Schema.
   Enums become `enum` arrays. Entity parameters become strings that name the
   entity type. One extra tool, `no_matching_intent`, is always appended.

3. **Judge.** Send one phrase with that tool list, using native tool calling
   rather than asking for JSON in prose. The prompt is deliberately bare — no
   few-shot examples, no chain of thought, temperature 0. You are measuring
   whether the *catalog* is unambiguous; prompt scaffolding would hide exactly
   the defects you are looking for. `tool_choice: any` forces a pick, which is
   safe because the escape hatch is right there in the list.

4. **Score and diff.** Intent match is decided before parameter match, so
   "right action, wrong argument" stays a distinct and less severe failure than
   "wrong action". Write the run to JSON and diff it against the last one.

The JSON that `run --json` writes is exactly the file `diff` reads. One schema,
not two.

## Limitations

- **Not Siri.** Covered above; it bears repeating.
- **Dates are compared as literal text.** `"6pm"` matches `"6PM"` but not
  `"18:00"`. Building a datetime normalizer is a rabbit hole and date parameters
  are ~1% of real parameters. Cases affected are flagged in the report.
- **Entity references are not resolved.** "the note about Mira" is scored as the
  string the model produced. Actual entity resolution is `AppIntentsTesting`'s
  job, and is on the roadmap for v0.3.
- **Extra parameters do not fail a case.** If the model supplies a parameter you
  did not ask about, it is recorded in the report but not scored.
- **One judge, one model, by default.** Agreement across models is a much
  stronger signal than any single model's opinion. That is v0.4.
- **The type-identifier mapping is empirical.** Apple does not document the
  integers in this file; ours were derived from every catalog shipped on macOS
  26 (see [DECISIONS.md](DECISIONS.md) ADR-0003). Unknown types degrade to
  strings rather than breaking.
- **Unresolved localization keys are flagged, not resolved.** Reading `.lproj`
  strings files would require a full bundle and a locale choice; it is a v0.2
  candidate. See ADR-0009.

## Roadmap

- **v0.2** — collision matrix (which pairs of intents are confusable, before you
  write any phrases) and corpus auto-generation from the catalog
- **v0.3** — entity-resolution fuzzing via `AppIntentsTesting`
- **v0.4** — multi-model cross-agreement, the strongest form of this signal
- **v0.5** — a GitHub Action wrapper and a Homebrew tap

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The whole test suite runs offline against
a mock judge, so you can work on scoring, reporting and diffing with no API key
at all — and CI passes with none configured.

The highest-value contribution from outside is **a new catalog fixture**. The
metadata format is undocumented and changes with Xcode; every real catalog from
a version we have not seen makes the parser more robust. CONTRIBUTING has a
step-by-step for capturing and submitting one.

## License

MIT
