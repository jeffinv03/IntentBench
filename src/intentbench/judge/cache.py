"""Content-addressed cache for judge responses.

The workflow this tool exists to serve is: run, edit an intent description, run
again, read the diff. That loop is only pleasant if the second run is nearly
free — so caching is on by default and keyed on everything that could change an
answer.

The key covers the model, the prompt version, the *whole rendered tool list*,
the phrase, and the locale. Including the tool list is the important part:
editing one intent's description must invalidate that run's cache, and editing
nothing must not. The tool list is serialized canonically (sorted keys, no
whitespace) so key stability does not depend on dict iteration order.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path

from intentbench.models import JudgeResult


def cache_dir() -> Path:
    """``$XDG_CACHE_HOME/intentbench`` if set, else ``~/.cache/intentbench``."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "intentbench"


def make_key(
    *,
    model_id: str,
    prompt_version: int,
    tools_json: str,
    phrase: str,
    locale: str,
) -> str:
    digest = hashlib.sha256()
    for part in (model_id, str(prompt_version), tools_json, phrase, locale):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")  # unambiguous field separator
    return digest.hexdigest()


class ResponseCache:
    """A flat directory of ``<sha256>.json`` files.

    Deliberately not a database. Cache entries are inspectable with ``cat``,
    which matters the first time someone asks "why did it answer that".
    """

    def __init__(self, directory: Path | None = None, *, enabled: bool = True) -> None:
        self.directory = Path(directory) if directory is not None else cache_dir()
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        # One level of fan-out keeps the directory listing manageable.
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> JudgeResult | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = JudgeResult.model_validate(payload)
        except (OSError, ValueError):
            # A corrupt entry is a cache miss, never an error.
            return None
        result.from_cache = True
        return result

    def put(self, key: str, result: JudgeResult) -> None:
        if not self.enabled or result.error is not None:
            return  # never cache failures
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = result.model_dump(mode="json", exclude={"from_cache"})
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)  # atomic, so concurrent workers can't half-write
        except OSError:
            pass  # a cache we cannot write to is a slow tool, not a broken one

    def clear(self) -> int:
        """Delete every cached response. Returns the number of entries removed."""
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        for child in sorted(self.directory.rglob("*"), reverse=True):
            if child.is_dir():
                with contextlib.suppress(OSError):
                    child.rmdir()
        return removed

    def count(self) -> int:
        if not self.directory.is_dir():
            return 0
        return sum(1 for _ in self.directory.rglob("*.json"))
