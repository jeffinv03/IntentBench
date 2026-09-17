"""Read provider credentials from a local ``.env`` file.

Pasting a key into a gitignored file is easier than editing shell profiles, and
it keeps the key next to the project that uses it. ``.env.example`` in the repo
root is the template.

Only the variables in :data:`KNOWN_KEYS` are read, so a ``.env`` written for
some other tool cannot change how this one behaves. A variable already set in
the environment always wins: CI secrets and one-off ``export``\\ s override the
file, never the other way round.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The file looked for in the current working directory.
ENV_FILE = ".env"

KNOWN_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_WORKSPACE_ID",
        "OPENAI_API_KEY",
    }
)


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Blank lines and ``#`` comments are skipped.

    Accepts an optional ``export`` prefix and single or double quotes around
    the value, which covers what people paste from provider consoles and docs.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """The known, non-empty keys in *path* (default: ``./.env``).

    A missing or unreadable file yields nothing rather than an error: most
    commands need no credentials at all.
    """
    target = path if path is not None else Path.cwd() / ENV_FILE
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return {name: value for name, value in parse_env(text).items() if name in KNOWN_KEYS and value}


def load_env_file(path: Path | None = None) -> list[str]:
    """Copy keys from :func:`read_env_file` into ``os.environ``, unless already set.

    Returns the names that were set.
    """
    loaded = []
    for name, value in read_env_file(path).items():
        if not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded
