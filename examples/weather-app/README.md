# Worked example — Apple's Weather app

A complete, runnable example against a real shipping catalog.

Weather.app is a good teaching case: six intents, four enums, and semantics
anyone can follow without knowing anything about the app. Five of the six intents
start with "Open" and three of them take a location — so telling them apart is a
genuine selection problem, not a toy one.

## Files

| File | What it is |
|---|---|
| `weather.actionsdata` | The real catalog, copied out of `/System/Applications/Weather.app` |
| `weather.meta.json` | Where it came from — OS, Xcode, and metadata tools version |
| `phrases.yaml` | An 11-case corpus, annotated |
| `mock-responses.yaml` | Scripted judge answers, so this runs offline |

## Look at the catalog

No API key needed:

```bash
intentbench catalog ./weather.actionsdata
```

`--app` accepts a `.app` bundle, a `Metadata.appintents` directory, or (as here)
the data file on its own.

## Run it offline

```bash
intentbench run \
  --app ./weather.actionsdata \
  --corpus ./phrases.yaml \
  --judge mock --mock-script ./mock-responses.yaml
```

The scripted answers are illustrative rather than a recording of a real run.
Three are deliberately wrong so the failure output has something to show — one
of each interesting kind:

- **`param_mismatch`** — "how windy is it in Chicago" picks the right intent but
  fills `specificCondition: conditions` instead of `wind`. Right action, wrong
  argument: a real failure, but a much less severe one than picking the wrong
  action, and the report keeps them separate.
- **`wrong_intent`** — "turn on severe weather alerts" selects the Units screen
  instead of the Notifications screen. Two settings intents whose descriptions
  read similarly. This is the archetypal catalog-quality defect.
- **`false_positive`** — "what's the weather like in Paris" should select
  nothing, and doesn't. Weather ships no general "show the forecast" intent, only
  specific-detail screens, so a router that picks one is guessing which detail
  the user meant. Over-matching is invisible to any harness that doesn't score
  abstention.

This run exits 1, because 72.7% is below the default 0.9 threshold.

## Run it for real

```bash
export ANTHROPIC_API_KEY=sk-ant-...
intentbench run --app ./weather.actionsdata --corpus ./phrases.yaml --json results.json
```

Eleven calls, well under a cent. Then try the loop this tool exists for: change a
description in `weather.actionsdata`, re-run, and diff.

```bash
intentbench run --app ./weather.actionsdata --corpus ./phrases.yaml --json after.json
intentbench diff results.json after.json
```

Editing a description invalidates exactly the cache entries it should, so the
unchanged cases come back free.
