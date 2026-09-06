---
name: Catalog parse failure
about: intentbench could not read an app's intent catalog, or read it wrong
title: "[parse] "
labels: ["parse-failure"]
---

<!--
The format of extract.actionsdata is not documented by Apple and changes with
Xcode. Parse failures are expected, and a good report usually gets fixed fast.
-->

## What happened

<!-- e.g. "error: ... is not valid JSON", or "parsed 3 intents but the app has 11" -->

## Output of `--debug`

This is the most useful single thing you can include. It prints the detected
format and the file's top-level keys, which is often enough to diagnose the
problem without the file itself.

```
$ intentbench catalog /path/to/Your.app --debug --verbose

<paste the full output here, stderr included>
```

## Environment

- **macOS version:** <!-- sw_vers -->
- **Xcode version, if known:** <!-- xcodebuild -version -->
- **Metadata tools version:** <!-- cat "<bundle>/Contents/Resources/Metadata.appintents/version.json" -->
- **intentbench version:** <!-- intentbench --version -->
- **Installed via:** <!-- uvx / pipx / pip -->

## The bundle

- **Which app?** <!-- your own, an Apple system app, or a third-party one -->
- **Platform:** <!-- macOS / iOS / watchOS / tvOS build -->
- **Where is the metadata directory?**
  - [ ] `Contents/Resources/Metadata.appintents/` (macOS layout)
  - [ ] `Metadata.appintents/` at the bundle root (iOS layout)
  - [ ] somewhere else — please say where

## Can you share the file?

Enormously helpful if so. `Metadata.appintents` is usually a few kilobytes and
contains only your intent definitions — no user data.

- [ ] Attached, zipped
- [ ] Can't share — it's proprietary <!-- that's fine; --debug output usually suffices -->

<!--
If you can't share it, this often pins the problem down:

  python3 -c "import json;d=json.load(open('extract.actionsdata'));print(sorted(d));print(len(d.get('actions',{})))"
-->
