# IntentBench — Implementation Specification, Part Two

**Version:** post-0.1 spec, draft 1 (2026-09-16)
**Audience:** the coding agent continuing this repo, and the maintainer reviewing it
**Status:** continues [`intentbench-implementation-spec.md`](intentbench-implementation-spec.md) (Part One). Part One stays authoritative for everything it covers. This document records what building v0.1 taught us, finishes the 0.1.x line, and scopes v0.2.

The labels from Part One keep their meaning. `[DECIDED]` is settled. `[SPIKE]` is an unknown to resolve empirically before the work that depends on it. This part adds `[DECIDE]`: a call for the maintainer, not the agent. Do not act on a `[DECIDE]` item until it has an answer.

---

## 0. Read this first

### 0.1 Where things stand

| Item | State |
|---|---|
| Phases 0–6 of Part One | Built. `v0.1.0` is tagged on `main` (`04e8cae`) |
| GitHub | `origin` → `github.com/jeffinv03/IntentBench`. `main` and the tag are pushed |
| Open branch | `fix/repeat-cache`: two commits, pushed, not yet merged (§2) |
| Tests | 226 offline tests pass. ruff and `mypy --strict` are clean |
| Live tests | **Both pass** against the real Anthropic API, as of `90f94b0` |
| GitHub Actions | Never checked. `gh` is not installed on the dev machine |
| PyPI | **Not published.** `pypi.org/pypi/intentbench` returns 404, so the name looks free |

### 0.2 Environment notes

- `git` on the dev machine is a broken Xcode shim. Use `/Library/Developer/CommandLineTools/usr/bin/git`.
- Credentials live in `./.env`, which is gitignored (§2.3). Never print a key, and never paste one into a chat or a commit. Before any commit, stage files by name, not with `git add -A`.
- The live tests read `.env` on their own: `.venv/bin/pytest -m live`.

---

## 1. What v0.1 taught us

These findings are already built into the code, the ADRs and the tests. They are summarized here because they should shape what comes next.

### 1.1 The metadata is good enough, and it has real gaps

- **Format:** `extract.actionsdata` is plain UTF-8 JSON (ADR-0002). The `typeIdentifier` integers are undocumented and were mapped empirically (ADR-0003). The survey covered 22 catalogs, 199 intents and 340 parameters, across four Xcode toolchains.
- **The §3.3 question has a yes answer:** names, types, optionality, descriptions and enum cases are all present (ADR-0004).
- **Coverage is poor, which makes it the product:** 22% of shipping intents have no description, and 44% of parameters have none. The report names them.
- **Unresolved localization keys:** 11% of intent strings (38 of 332) are `.strings` lookup keys such as `STOP_RECORDING_INTENT_DESCRIPTION`, not prose. They are treated as missing and reported separately (ADR-0009). Passing them to the judge would blame the model for a metadata problem.
- **Arrays are common:** 72 of 340 parameters are arrays, so `is_array` became part of the model (ADR-0007).
- **Dates are rare:** they are 1% of parameters, so literal comparison is fine for now (ADR-0008).

### 1.2 Fakes hid two shipped bugs

Both bugs were in `v0.1.0`, and 209 green tests missed both.

1. **`--repeat` never measured anything** (ADR-0010).
   - **What went wrong:** every repeat shared one cache key. With a warm cache, one stored answer was replayed N times, so no case could be flagged unstable. With a cold cache, how many repeats hit the API depended on thread timing. Usage and cost figures were also wrong.
   - **Why tests missed it:** the CLI test used the mock judge, which runs with the cache off.
2. **Every real Anthropic run failed** (ADR-0011).
   - **What went wrong:** `anthropic` 1.x removed `temperature` from `messages.create()`, so each call raised a `TypeError` before any request was sent.
   - **Why tests missed it:** the fake client's `create(**kwargs)` accepted any argument.

**The rule this produces** `[DECIDED]`:

- **Fakes must be at least as strict as the thing they replace.** A fake that accepts anything proves nothing. `test_anthropic_judge_request_fits_the_installed_sdk` now checks every argument against the real SDK signature. Any new fake needs an equivalent check.
- **Test the combination that is used for real.** Runner tests now use a real cache and a counting judge (`tests/test_runner.py`).
- **Run `pytest -m live` once on the exact commit you release.** It costs cents. Not doing so is how 0.1.0 shipped a judge that could not make a single call.

### 1.3 Credentials are a first-run problem

The first real run hit two account problems before any code problem: a key in the wrong place, then a key not scoped to a workspace. Part One §0 says a confusing install means the tool has failed, and that applies to credentials too. §2.3 is the fix.

---

## 2. Decisions added since Part One

`[DECIDED]`: implement as written. Each one has an ADR or a changelog entry.

| Decision | Choice | Where |
|---|---|---|
| `--repeat N` and the cache | Repeats neither read nor write the cache. Every repeat is a fresh, billed call. The cost prompt counts cases × N | ADR-0010 |
| Usage accounting | Counted per attempt, before repeats are combined. Cached attempts add no tokens and no cost | ADR-0010 |
| Temperature | Sent as `extra_body={"temperature": 0.0}`, which works on SDK 0.x and 1.x. Dropped for the rest of the run after the first 400 that names it | ADR-0011 |
| Credentials | Read from `./.env`, with `.env.example` as the template. Only `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` and `OPENAI_API_KEY` are read. Exported variables win | README "API keys" |
| Workspace-less keys | `ANTHROPIC_WORKSPACE_ID` is sent as the `anthropic-workspace-id` header | CHANGELOG |
| Test isolation | Every offline test runs from an empty temporary directory with credential variables unset | `tests/conftest.py` |
| Default judge for 0.1.x | Stays `claude-sonnet-4-6`. Changing it changes results and cache keys, so it does not belong in a patch release | §3.2 |

### 2.1 Deviations from Part One

- **Part One §6.3 is incomplete.** It says "`temperature=0` always". On Opus 4.7 and later that is impossible, because the API rejects the parameter. On those models, stability is up to the model, and `--repeat` is how you measure it.
- **Part One §3.3 is extended.** Beyond "is the field present", the spike needed a second question: "is the field real prose?" Keep asking it (see §1.1).

---

## 3. Decisions for the maintainer

`[DECIDE]`: answer these before the phase that depends on them.

### 3.1 Should the spec files be public?

Part One and this file are committed and pushed. If the GitHub repo is public, they already are.

- **Keep them (recommended):** they contain design rationale and no secrets, and they make good raw material for the launch post.
- **Remove them:** deleting them in a new commit is not enough, because they stay in the history. A real removal means rewriting history and force-pushing, before anyone else clones the repo.

Blocks Phase 7 (publishing).

### 3.2 Which default model for v0.2?

| | `claude-sonnet-4-6` (current) | `claude-sonnet-5` |
|---|---|---|
| Price per 1M tokens (in / out) | $3 / $15 | $2 / $10 |
| `temperature: 0` | Accepted | Rejected (only the default is allowed) |
| Run-to-run stability | Pinned by temperature | Up to the model; measure with `--repeat` |

The recommendation is to keep `claude-sonnet-4-6` for v0.2. First, measure `claude-sonnet-5` with `--repeat 5` on the example corpus. Switch only if its instability rate is near zero. A default switch invalidates every user's cache and baseline, so it needs a minor version and a changelog note. Blocks nothing in Phase 7.

### 3.3 What is v0.2's one objective?

The README lists two v0.2 items, and a third candidate came out of the spike:

| Candidate | Cost to build | Fit |
|---|---|---|
| **Collision analysis** | Moderate. Reuses the catalog → render → judge pipeline | The core question: which intents are confusable? |
| Corpus auto-generation | High. Needs generated files, a review workflow, reproducibility and model cost | Useful, but it is a content pipeline |
| `.lproj` localization resolution | Moderate. Needs locale choice, binary and text `.strings`, and full bundles only | Fixes 11% of strings, in three Apple apps |

The recommendation is **collision analysis**, specified in §5. Part One §12 warns that scope creep is the main failure mode for a first open-source project, so pick one.

---

## 4. Phase 7 — release 0.1.1

**Goal:** a version on PyPI that a stranger can install and run against the real API the first time they try.

### 4.1 Checklist

1. **Merge `fix/repeat-cache`.**
   - Open the PR at `github.com/jeffinv03/IntentBench/pull/new/fix/repeat-cache`.
   - Confirm CI is green on all six matrix jobs plus lint, smoke and build. This is the first time anyone will look at CI.
2. **Fix stale repository URLs.** The repo is `jeffinv03/IntentBench`, but these files point at `jeffinv/intentbench`:
   - `pyproject.toml` (Homepage, Issues, Changelog)
   - the `README.md` CI badge
   - `CONTRIBUTING.md` (the clone command)
   - `CHANGELOG.md` (the compare and release links)
   - `.github/ISSUE_TEMPLATE/config.yml` (two links)

   These URLs appear on PyPI, so they must be right before the first upload.
3. **Fix the quickstart install.** `uvx intentbench run ...` installs the package without the `anthropic` extra, so a first-time user's `run` fails with "needs the `anthropic` package". Use `uvx --from 'intentbench[anthropic]' intentbench run ...`. Alternatively, make `anthropic` a core dependency, since it is the default judge. `[DECIDE]`: the recommendation is to make it a core dependency, because the default path should not need an extra.
4. **Run the real CLI end to end.** So far only the judge has run against the API, in isolation. The full `run` command, with the cache, the cost prompt, the report and the `.env` loading, has never made a real call. Run it on the Weather example (about 11 cases, a few cents):
   - once cold;
   - once warm, which should show 0 API calls and no cost;
   - once with `--repeat 3`, which should make about 33 API calls and nothing from the cache.
5. **Bump the version** to `0.1.1`:
   - `pyproject.toml`
   - `src/intentbench/__init__.py`
   - date the `[Unreleased]` section of `CHANGELOG.md`
6. **Run `pytest -m live`** on that exact commit (§1.2).
7. **Build and check:** `python -m build && twine check dist/*`.
8. **Publish to PyPI.** Use Trusted Publishing: a `release.yml` workflow triggered by a `v*` tag, with no API token stored anywhere. The name is free today, so reserve it soon.
9. **Tag and release.**
   - Tag `v0.1.1` on the merge commit and push the tag.
   - Create a GitHub release with the changelog section as its notes.
   - Leave `v0.1.0` where it is: it was never published, so there is nothing to retract.

### 4.2 Done when

- `uvx --from 'intentbench[anthropic]' intentbench --version` prints `0.1.1` on a clean machine. If the extra became a core dependency, `uvx intentbench --version` is enough.
- Following the README from a clean checkout, with only a key in `.env`, gives a real scored run.
- CI is green on `main`, and the badge in the README shows it.

---

## 5. Phase 8 — v0.2: collision analysis

**Assumes §3.3 is answered "collision analysis".** If it is not, stop and wait for a new spec for whichever candidate was chosen.

### 5.1 The question it answers

*Which pairs of my intents will a router confuse, and how badly?* Part One's `run` answers "did this phrase route correctly?". Collision analysis answers "why do phrases keep landing on the wrong intent?" at the catalog level, and it names the pair to fix.

### 5.2 Build it in three steps, cheapest first

Each step ships on its own.

**8a — Confusion from an existing run (free, offline).**

```
intentbench confusion results.json
```

- **Input:** a run report. It already contains `expected_intent` and `selected_intent` for every case, and `observed_selections` when `--repeat` was used.
- **Output:** a matrix of expected intent × selected intent (with `no_matching_intent` as its own row and column), plus a ranked list of off-diagonal pairs.
- **Cost:** no model calls at all, and it is testable with the mock judge alone.

This step alone turns every existing run into collision data.

**8b — Static similarity from metadata (free, deterministic).**

```
intentbench collisions ./MyApp.app --static
```

- **Method:** compare every pair of rendered tools on their title, description and parameter names, using token overlap. Do not use embeddings in v0.2, because they need a model and a dependency.
- **Output:** the pairs above a threshold, each with the overlapping words that caused it, so the fix is obvious.

`[SPIKE]` Does static similarity predict the confusion 8a measures? Check it on the five committed fixtures plus the Weather example before promoting `--static` in the README. If it doesn't, ship it as "lexical overlap", not "collisions".

**8c — Probe phrases (costs money).**

```
intentbench collisions ./MyApp.app --probes N
```

- **Method:** for each intent, generate N short phrases from its metadata alone, then route each one through the normal judge.
- **Output:** the same matrix as 8a. The difference is that it needs no hand-written corpus.
- **Shared work:** the phrase generator is the seed of corpus auto-generation. Write it once, and write the generated phrases to a reviewable YAML file in the Part One §5.1 format. A person should read and commit that file before it is ever used as a corpus.
- **Cost and caching:**
  - The cost prompt counts intents × N × repeats.
  - Generation calls get their own cache key that includes the generator prompt version.
  - Routing calls reuse the existing cache key.
- **Mock judge:** needs a scripted generation mode so the whole path is testable offline.

### 5.3 Output contract

- **Terminal:** the top 10 colliding pairs, each showing the pair, its score and the evidence behind it (overlapping words, or the phrases that crossed over). Show the full matrix only with `--matrix`.
- **JSON:** `--json` writes the matrix, the ranked pairs and run metadata (catalog hash, model, prompt versions). Reuse the report metadata model; do not create a second one.
- **Markdown:** `--markdown` writes the ranked-pairs table only.
- **Exit codes:** these are the Part One §8.5 codes. `--fail-above X` exits 1 when any pair scores above X. Without the flag, analysis always exits 0.

### 5.4 Tests

- **8a:** a hand-built report fixture with known confusions, asserting exact matrix cells and ranking. Include abstention in both directions.
- **8b:** pairs from the synthetic catalog that are identical, disjoint and partially overlapping.
- **8c:** a scripted mock for generation and routing. Assert the call counts, the cache behaviour (repeats stay uncached, as in ADR-0010) and the YAML output format.
- **Fake strictness:** any new fake follows the §1.2 rule.

### 5.5 Done when

| Step | Done when |
|---|---|
| 8a | `confusion` works on any existing report, runs fully offline, and has the JSON and terminal outputs |
| 8b | The `[SPIKE]` is answered in `DECISIONS.md`, and `--static` output names the overlapping words |
| 8c | A real run on the Weather example produces a matrix and a reviewable probe file. The cost prompt and cache are verified as in §4.1 step 4 |

---

## 6. Later roadmap

These are unchanged from Part One §1, and still out of scope until their own spec exists:

- **v0.3:** entity-resolution fuzzing via `AppIntentsTesting`.
- **v0.4:** multi-model cross-agreement. §3.2's stability measurement is a small first step toward it.
- **v0.5:** a GitHub Action wrapper and a Homebrew tap.

**Candidate backlog.** These are not scheduled. Open an issue for each rather than building it.

- **`.lproj` resolution:** see §3.3. It needs a locale flag, parsing of binary and text `.strings`, and a fallback when only a bare `extract.actionsdata` file is given.
- **Resolution of `DATE` and `DURATION` values:** only if real corpora hit ADR-0008 often.
- **A warning when the judge drops temperature:** today the fallback in ADR-0011 is silent. A single dim report line would make that visible.

---

## 7. Additions to Part One §12

- **Treat a passing test with suspicion when it uses a fake.** Ask what the fake accepts that the real thing would reject.
- **Money and secrets need two checks.** Before a paid run, confirm the call count. Before a commit, confirm no key is staged (`git diff --cached | grep sk-ant-`).
- **When a safety check stops you, find out why before loosening it.** During this work, a format check refused to move a key because the shell line had a second command stuck to it. Fixing the input was right; loosening the check would not have been.
- **Keep the ADR log append-only.** When a decision changes, add a new entry that supersedes the old one, as ADR-0010 and ADR-0011 do.

---

## 8. Build order

| Phase | Deliverable | Blocked by |
|---|---|---|
| 7 | 0.1.1 on PyPI | §3.1 |
| 8a | `intentbench confusion` | Phase 7, §3.3 |
| 8b | `collisions --static` and its `[SPIKE]` | 8a |
| 8c | `collisions --probes` and the probe file | 8b, §3.2 |
| — | v0.2.0 release | 8a–8c, and the §4.1 checklist repeated |

Do not start Phase 8 before 0.1.1 is published. A v0.2 built on an unreleased v0.1 means debugging two versions at once.
