# Decisions

An ADR log. One entry per choice that a future reader would otherwise have to
reverse-engineer. Entries are append-only; if a decision is reversed, add a new
entry that supersedes the old one rather than editing history.

Decisions inherited from the implementation spec are recorded here only where
this repo learned something the spec could not know in advance, or where the
implementation deviated.

---

## ADR-0001 — Python, not Swift

**Status:** accepted (from spec)

App Intents is Swift technology, so Swift is the obvious guess. It is the wrong
one. The product here is the eval logic, not the metadata reader, and the
metadata reader turned out to be ~250 lines of JSON traversal (ADR-0002).
Writing it in Python means the parser runs on Linux CI against committed
fixtures, so contributors without a Mac can work on everything except capturing
new fixtures.

---

## ADR-0002 — `extract.actionsdata` is plain UTF-8 JSON

**Status:** accepted — resolves the Phase 0 `[SPIKE]`

### How this was established

Surveyed every App Intents catalog present on a macOS 26.6.2 machine: 22 files
across 17 Apple system apps and 5 third-party apps.

```
$ file "/System/Applications/Notes.app/Contents/Resources/Metadata.appintents/extract.actionsdata"
extract.actionsdata: JSON data
```

Not a binary plist, not an XML plist. Plain JSON, UTF-8, no BOM. `plistlib` is
not needed for the actions file at all — it is still used to read
`CFBundleIdentifier` out of the bundle's `Info.plist`, which is a binary plist.

A sibling file, `version.json`, sits in the same directory:

```json
{ "version" : "3.0", "toolsVersion" : "17E6107" }
```

`toolsVersion` is the Xcode toolchain that produced the metadata. It is recorded
alongside every committed fixture.

### Structure

```jsonc
{
  "actions":  { "<intent identifier>": { ...intent node... } },
  "entities": { "<type name>":         { ...entity node... } },
  "enums":    [ { "identifier": "...", "cases": [ ... ] } ],   // a LIST, not a map
  "queries":  { ... },
  "generator": { "name": "xcode-tools", "version": "17E6107" },
  "version": 1
}
```

Note the asymmetry: `actions` and `entities` are objects keyed by name, but
`enums` is a list whose members carry their own `identifier`. This is not a
typo in the spec — it is what the file does.

An abridged real intent node (Reminders.app, complete except for indentation):

```jsonc
{
  "identifier": "TTROpenSmartListAppIntent",
  "fullyQualifiedTypeName": "Reminders.OpenAnyListAppIntent",
  "title": { "alternatives": [], "key": "Open List" },
  "descriptionMetadata": {
    "descriptionText": { "alternatives": [], "key": "Opens a list in Reminders." }
  },
  "assistantDefinedSchemas": [],
  "openAppWhenRun": true,
  "visibilityMetadata": { "assistantOnly": false, "isDiscoverable": true },
  "parameters": [
    {
      "name": "target",
      "isOptional": false,
      "title": { "alternatives": [], "key": "Any List" },
      "parameterDescription": { "alternatives": [], "key": "List to open" },
      "typeSpecificMetadata": [],
      "valueType": { "entity": { "wrapper": { "typeName": "AnyListEntity" } } }
    }
  ]
}
```

Three encoding conventions matter:

1. **Localized strings** are `{"alternatives": [], "key": "..."}`. Despite the
   name, `key` usually holds the literal display string rather than a lookup
   key — but not always, and the exception matters enough to have its own entry
   (ADR-0009).
2. **`valueType` is a single-key tagged union.** The key names the wrapper kind
   (`primitive`, `entity`, `linkEnumeration`, `array`, `measurement`, `intents`,
   `searchCriteria`) and the payload sits under `wrapper`.
3. **`typeSpecificMetadata` is a flat alternating list**, not a map:
   `[key, value, key, value, ...]`. Default values live here, under
   `LNValueTypeSpecificMetadataKeyDefaultValue`, as a tagged scalar such as
   `{"int": {"wrapper": 1}}`. There is no `defaultValue` key on the parameter
   itself, which is the single most surprising thing in the format.

---

## ADR-0003 — Type identifiers are undocumented integers, mapped empirically

**Status:** accepted

`valueType` payloads carry an integer `typeIdentifier` with no published
meaning. The mapping below was derived by correlating identifiers against
parameter names and their default values across all 340 parameters in the
22 surveyed catalogs.

| Wrapper | `typeIdentifier` | Mapped to | Evidence |
|---|---|---|---|
| `primitive` | 0 | `STRING` | `title`, `subject`, `searchPhrase`; defaults are `{"string": ...}` |
| `primitive` | 1 | `BOOLEAN` | `isMute`, `isReplyAll`, `read`; defaults are `{"int": 0}` / `{"int": 1}` |
| `primitive` | 2 | `INTEGER` | `number`; default `{"int": {"wrapper": 1}}` |
| `primitive` | 7 | `NUMBER` | `width`, `height` (Preview's Resize intent) |
| `primitive` | 8 | `DATE` | `date`, `entryDate`, `sendLaterDate` |
| `primitive` | 11 | `STRING` | `url` — a URL is a string to the model |
| `primitive` | 12 | `STRING` | `text`, `body`, `message`, `contents` — rich/attributed text |
| `intents` | 12 | `FILE` | `file`, `folder`, `image`, `audioFile` (`IntentFile`) |
| `entity` | — | `ENTITY` | carries `typeName` instead of an identifier |
| `linkEnumeration` | — | `ENUM` | carries `identifier`, a key into `enums` |
| `array` | — | member type | carries `memberValueType`; recursed into |
| `measurement` | `unitType` 7 | `DURATION` | the one occurrence has `defaultUnitSymbol: "min"` |

**The `12` collision is real, not a mistake.** Under `primitive`, 12 is textual;
under `intents`, 12 is a file. The integer namespaces are per-wrapper.

Two consequences are load-bearing:

- **Unknown identifiers are never errors.** Anything not in the table maps to
  `ParameterType.UNKNOWN`, keeps its `raw_type` verbatim, and renders to the
  judge as a string with the raw type named in the description. Apple will add
  types, and a new one must degrade rather than break a run.
- **This table is a hypothesis, not documentation.** It is exercised by the
  synthetic fixture, which includes an unknown identifier (`primitive<9999>`), an
  unknown wrapper kind, and a parameter with no `valueType` at all.

---

## ADR-0004 — The metadata contains everything needed to render a tool schema

**Status:** accepted — answers the Phase 0 §3.3 question

The spike's gating question was: *does this file contain parameter names, types,
optionality, descriptions, and enum cases?* If any were absent, that would have
been a scope-changing finding requiring a stop-and-report.

**All five are present.** Nothing was missing, so Phase 1 proceeded as specified.

| Needed | Source | Coverage across 199 real intents |
|---|---|---|
| Parameter name | `parameters[].name` | 340/340 (100%) |
| Type | `parameters[].valueType` | 340/340 (100%) |
| Optionality | `parameters[].isOptional` | 340/340 (100%) |
| Intent description | `descriptionMetadata.descriptionText.key` | 155/199 (78%) |
| Intent title | `title.key` | 177/199 (89%) |
| Parameter description | `parameters[].parameterDescription.key` | 192/340 (56%) |
| Enum cases | `enums[].cases[].identifier` + display title | 54 enum definitions |

The coverage numbers are the interesting part, and they shaped the product.

**Descriptions are frequently absent** — 22% of shipping intents have none, and
44% of parameters have none. This is not a parser gap; the prose was never
written. It also validates the whole premise: an intent the model sees only as a
humanized Swift type name is exactly the intent that loses a selection race.

So the renderer records *which* fallback each intent used
(`description` → `title` → humanized type name), and any intent that falls all
the way through is named in its own report section. That list is free to produce
and is one of the more actionable things the tool outputs.

**Deprecation and visibility are both representable**, which the spec flagged as
needing verification:

- `visibilityMetadata.isDiscoverable: false` marks an intent hidden from
  Shortcuts and Siri. Observed in 8 of 17 Apple apps.
- `deprecationMetadata` is present on deprecated intents, sometimes with
  `replacedByIntentIdentifier` naming the successor. Only Freeform.app ships
  any, which is why it is a committed fixture.

Both are filtered out by default and their counts are always reported.

**Schema-conformant intents** (`@AppIntent(schema:)`) declare
`assistantDefinedSchemas: [{domain, name, version}]`. Custom intents have an
empty list. Stored as `schema_id` in the form `domain.name` — for example
`wordProcessor.CreateWordProcessorDocumentIntent`.

**The bundle identifier is not in this file.** It comes from the bundle's
`Info.plist`, so it is only available when the input is a `.app` rather than a
bare data file.

---

## ADR-0005 — Fixtures are Apple system apps, committed unredacted

**Status:** accepted

The spec's guidance was to prefer Apple system apps over third-party commercial
apps, and to redact if in doubt.

Five real fixtures are committed, all from Apple system apps that ship with
macOS. No third-party app metadata is committed, even though five such catalogs
were surveyed (Microsoft Office, Maccy) — those were used to validate the parser
and then discarded.

| Fixture | Intents | Why it is here |
|---|---|---|
| `reminders` | 1 | Smallest real catalog. One intent, one entity parameter |
| `weather` | 6 | Four enums. Exercises enum rendering end to end |
| `voicememos` | 14 | Four hidden intents. Exercises default filtering |
| `freeform` | 24 | The only system catalog with deprecated intents |
| `podcasts` | 0 | A valid catalog declaring zero intents |
| `synthetic` | 8 | Hand-written; covers what none of the above hit |

Each has a `.meta.json` sidecar recording the source app, OS version, Xcode
version, and metadata tools version, plus a `redacted` flag and a note on why
that fixture earns its place.

These are build-time interface metadata, a few kilobytes each, present on every
Mac. They are committed unredacted because redaction would destroy their value —
the whole point of a real fixture is that its prose is real prose. If Apple
objects, the fixtures can be replaced with structural skeletons without touching
the parser, and the `redacted` flag in each sidecar exists for that.

---

## ADR-0006 — Forced tool choice, with an escape hatch

**Status:** accepted (from spec), with an implementation note

The judge is called with `tool_choice: {"type": "any",
"disable_parallel_tool_use": true}`. Forcing a call removes an entire class of
unparseable non-answers, and it is safe only because `no_matching_intent` is
always in the tool list — "nothing fits" stays expressible.

**Implementation note not in the spec:** some newer models reject forced tool
choice outright (Claude Fable 5.1 and later return a 400 for
`tool_choice: any`). Since `--model` lets a user pick any model, the Anthropic
judge catches that specific rejection once, falls back to
`{"type": "auto", "disable_parallel_tool_use": true}`, and remembers the fallback
for the rest of the run. The default model, `claude-sonnet-4-6`, supports forced
choice, so this path is dormant in normal use.

`strict: true` on tool definitions was considered and rejected: it requires
every property to appear in `required`, which would misrepresent optional
parameters — and optionality is something the eval is specifically trying to
measure.

---

## ADR-0007 — Deviations from the spec

**Status:** accepted

Small, deliberate, and each with a reason.

**`examples/notes-app/` is `examples/weather-app/`.** The spec named a directory
for a worked example against "a real bundle's catalog". Notes.app was the
obvious candidate and is a poor teaching example: 49 intents, 160 KB, far too
much to reason about while learning the corpus format. Weather.app has six
intents, four enums, and semantics anyone can follow — and it produces a
genuinely interesting abstention case ("what's the weather like in Paris" has no
matching intent, because Weather ships only specific-detail screens).

**`ParameterDefinition` gained `is_array`.** The spec's model had no array
notion, but 72 of 340 real parameters are arrays. Without the flag they would
render as scalars and every array-valued selection would score wrong. The member
type is preserved in `type`, the full shape in `raw_type`.

**`IntentDefinition` gained `is_deprecated`, `deprecation_message`,
`replaced_by`, and `opens_app_when_run`.** The first three are needed for the
filtering the spec asks for in §4.4; the spike confirmed they are representable
(ADR-0004). `opens_app_when_run` is one field, comes free, and is genuinely
useful when reading a catalog.

**`intentbench cache info` exists** alongside the specified `cache clear`.
Telling someone where the cache lives is a one-line command and saves a support
round-trip.

**`--debug` writes to stderr, not stdout.** The spec asks for `--debug` output
to attach to bug reports. Putting it on stderr keeps `catalog --json --debug`
pipeable, which is how someone would actually capture both at once.

**The repository root is the project directory**, rather than a nested
`intentbench/` folder. The layout inside `src/intentbench/` matches the spec
exactly.

---

## ADR-0008 — Dates are compared as literal text in v0.1

**Status:** accepted (from spec) — resolves the Phase 4 `[SPIKE]`

Date and duration parameters are compared as opaque strings after the usual
casefold-and-strip normalization. `"6pm"` matches `"6PM"` and does not match
`"18:00"`.

Building a datetime normalizer is a genuine rabbit hole — relative expressions,
time zones, locale-dependent parsing, ambiguity between `6` meaning 6am and 6pm
— and date parameters are a small minority: 4 of 340 real parameters, about 1%.

The limitation is not silent. A comparison against a parameter the catalog types
as `DATE` or `DURATION` is flagged, and the terminal report says the comparison
was literal whenever such a case fails. Revisit if real corpora hit it often.


---

## ADR-0009 — Unresolved localization keys are treated as missing prose

**Status:** accepted

### The finding

ADR-0002 originally claimed that the `key` field of a localized string always
holds literal display text. That is wrong, and it was caught by running
`intentbench catalog` against VoiceMemos.app:

```
Identifier               Title                        Description
RecordVoiceMemoIntent    CREATE_RECORDING_INTENT_...  CREATE_RECORDING_INTENT_DESCRIPTION
StopRecording            STOP_RECORDING_INTENT_TITLE  STOP_RECORDING_INTENT_DESCRIPTION
```

These are `.strings` lookup keys that the metadata extractor did not resolve.
The actual prose lives in the bundle's `.lproj` directories, which the actions
file does not reference.

Across the 22 catalogs surveyed, **38 of 332 intent strings (11%) are
unresolved localization keys**, in three apps: VoiceMemos (28), Mail (8), and
Tips (2).

### Why this matters more than it first appears

An intent whose description is `SEARCH_RECORDINGS_INTENT_DESCRIPTION` is *worse
off* than one with no description at all. It looks like content — it occupies
the description slot, so the title fallback never fires — while carrying almost
no signal a router can use. Handing it to the judge verbatim would quietly
degrade selection and, worse, would make the tool blame the model for a metadata
problem.

### What we do

Strings matching `^[A-Z0-9]+([_.-][A-Z0-9]+)+$` — all caps, no spaces, at least
one separator — are treated as absent, so the normal fallback chain continues to
the title and then to the humanized Swift type name. `StopRecording` therefore
reaches the judge as "Stop recording" rather than
`STOP_RECORDING_INTENT_DESCRIPTION`, which is strictly more useful.

They are also reported in their own section, separate from "no description at
all", because the fix is different: wire up your strings file, versus write a
description.

**The separator requirement is what makes the heuristic safe.** Of the 65
all-caps strings across every surveyed catalog, 63 are localization keys and
every one contains an underscore. The two that do not are both the parameter
title `URL` — real prose that must survive. Requiring a separator keeps it.

### Not resolving them

Reading the `.lproj` strings files was considered and rejected for v0.1. It
would only work when the input is a full `.app` bundle (not the bare data file,
which is what people attach to bug reports), it means picking a locale, and
`.strings` files come in both binary-plist and text flavors. Flagging the
problem is most of the value; resolving it is a v0.2 candidate.

VoiceMemos is a committed fixture specifically so this path stays tested.