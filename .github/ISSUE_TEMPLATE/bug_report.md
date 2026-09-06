---
name: Bug report
about: Something behaved incorrectly
title: ""
labels: ["bug"]
---

<!-- If intentbench failed to read an app's catalog, please use the
     "Catalog parse failure" template instead — it asks for the right things. -->

## What happened

## What you expected

## Reproduction

```bash
$ intentbench ...
<output>
```

Please include:

- [ ] the exact command
- [ ] the corpus, or the relevant cases from it
- [ ] whether it reproduces with `--judge mock` (which needs no API key)

## Environment

- **intentbench version:** <!-- intentbench --version -->
- **Python version:** <!-- python3 --version -->
- **OS:**
- **Judge and model:** <!-- e.g. anthropic / claude-sonnet-4-6 -->

## Notes

<!-- Does it reproduce against a committed fixture, e.g.
     tests/fixtures/catalogs/synthetic.actionsdata? That makes it much easier
     to turn into a regression test. -->
