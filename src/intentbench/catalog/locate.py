"""Find `extract.actionsdata` given whatever path the user pointed us at.

Background for readers who don't build iOS/macOS apps: an "app bundle" is just a
directory named ``Something.app``. Xcode runs a build step that writes a
``Metadata.appintents`` directory into that bundle, and that directory contains
``extract.actionsdata`` — a JSON file listing every app intent the app ships.

The catch is that the directory sits in different places depending on platform:

* **macOS** bundles are nested — ``MyApp.app/Contents/Resources/Metadata.appintents/``
* **iOS** bundles are flat — ``MyApp.app/Metadata.appintents/``

We handle both, plus app extensions (``.appex``) bundled inside the app, which
ship their own intents.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

METADATA_DIR_NAME = "Metadata.appintents"
ACTIONS_FILE_NAME = "extract.actionsdata"

#: Where the metadata directory lives, relative to a bundle root, in preference order.
_BUNDLE_RELATIVE_DIRS = (
    Path("Contents") / "Resources" / METADATA_DIR_NAME,  # macOS
    Path(METADATA_DIR_NAME),  # iOS / watchOS / tvOS (flat bundles)
    Path("Contents") / METADATA_DIR_NAME,
)

#: Where Info.plist lives, relative to a bundle root, in preference order.
_INFO_PLIST_PATHS = (
    Path("Contents") / "Info.plist",  # macOS
    Path("Info.plist"),  # iOS
)

#: Depth-limited search for extensions that ship their own intents.
_NESTED_GLOBS = (
    "Contents/PlugIns/*/Contents/Resources/" + METADATA_DIR_NAME,
    "PlugIns/*/" + METADATA_DIR_NAME,
    "Contents/Extensions/*/Contents/Resources/" + METADATA_DIR_NAME,
)


class LocateError(Exception):
    """We could not find an intent catalog at the given path."""


@dataclass(frozen=True)
class LocatedCatalog:
    """Where the catalog is, and what we learned on the way there."""

    actions_path: Path
    metadata_dir: Path | None
    bundle_path: Path | None
    bundle_identifier: str | None
    version_json: dict[str, str] | None
    extra_metadata_dirs: tuple[Path, ...] = ()

    @property
    def label(self) -> str:
        """A short human name for headers — the bundle name, else the file name."""
        if self.bundle_path is not None:
            return self.bundle_path.name
        return self.actions_path.name


def _read_version_json(metadata_dir: Path) -> dict[str, str] | None:
    """`version.json` sits beside the actions file and names the Xcode toolchain."""
    import json

    candidate = metadata_dir / "version.json"
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): str(v) for k, v in data.items()}


def _read_bundle_identifier(bundle_path: Path) -> str | None:
    """The bundle id is not in the actions file; it comes from Info.plist."""
    for rel in _INFO_PLIST_PATHS:
        plist = bundle_path / rel
        if not plist.is_file():
            continue
        try:
            with plist.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, ValueError, plistlib.InvalidFileException):
            return None
        value = data.get("CFBundleIdentifier")
        return str(value) if value is not None else None
    return None


def _find_metadata_dirs(bundle_path: Path) -> list[Path]:
    found: list[Path] = []
    for rel in _BUNDLE_RELATIVE_DIRS:
        candidate = bundle_path / rel
        if (candidate / ACTIONS_FILE_NAME).is_file():
            found.append(candidate)
    for pattern in _NESTED_GLOBS:
        for candidate in sorted(bundle_path.glob(pattern)):
            if (candidate / ACTIONS_FILE_NAME).is_file():
                found.append(candidate)
    return found


def locate_catalog(path: Path) -> LocatedCatalog:
    """Resolve *path* to an ``extract.actionsdata`` file.

    Accepts a ``.app`` (or ``.appex``) bundle, a ``Metadata.appintents``
    directory, or the ``extract.actionsdata`` file itself. Accepting all three
    is deliberate — it keeps the tool usable when our bundle-layout assumptions
    fail, and lets someone attach a single file to a parse bug report.
    """
    path = Path(path).expanduser()

    if not path.exists():
        raise LocateError(f"No such file or directory: {path}")

    # 1. Pointed straight at the data file (any name — people rename these when
    #    attaching them to issues).
    if path.is_file():
        metadata_dir = path.parent if path.parent.name == METADATA_DIR_NAME else None
        return LocatedCatalog(
            actions_path=path,
            metadata_dir=metadata_dir,
            bundle_path=None,
            bundle_identifier=None,
            version_json=_read_version_json(metadata_dir) if metadata_dir else None,
        )

    # 2. Pointed at a Metadata.appintents directory.
    if path.is_dir() and (path / ACTIONS_FILE_NAME).is_file():
        return LocatedCatalog(
            actions_path=path / ACTIONS_FILE_NAME,
            metadata_dir=path,
            bundle_path=None,
            bundle_identifier=None,
            version_json=_read_version_json(path),
        )

    # 3. Pointed at a bundle.
    metadata_dirs = _find_metadata_dirs(path)
    if metadata_dirs:
        primary = metadata_dirs[0]
        return LocatedCatalog(
            actions_path=primary / ACTIONS_FILE_NAME,
            metadata_dir=primary,
            bundle_path=path,
            bundle_identifier=_read_bundle_identifier(path),
            version_json=_read_version_json(primary),
            extra_metadata_dirs=tuple(metadata_dirs[1:]),
        )

    if path.suffix in {".app", ".appex"}:
        raise LocateError(
            f"{path.name} has no {METADATA_DIR_NAME} directory.\n"
            "That usually means the app ships no App Intents, or the build ran "
            "without the App Intents metadata processor.\n"
            f"Looked in: {', '.join(str(r) for r in _BUNDLE_RELATIVE_DIRS)}"
        )

    raise LocateError(
        f"{path} is not an app bundle, a {METADATA_DIR_NAME} directory, "
        f"or an {ACTIONS_FILE_NAME} file."
    )
