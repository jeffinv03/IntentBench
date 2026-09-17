"""Shared fixtures. No test in this suite may make a network call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentbench.catalog import load_catalog, render_catalog
from intentbench.corpus import load_corpus
from intentbench.envfile import KNOWN_KEYS
from intentbench.models import Catalog, RenderedCatalog

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGS = FIXTURES / "catalogs"
CORPORA = FIXTURES / "corpora"

#: Every committed real catalog, with the intent count we expect from it.
#: Counts are unfiltered — hidden and deprecated intents included.
REAL_CATALOGS: dict[str, int] = {
    "reminders": 1,
    "weather": 6,
    "voicememos": 14,
    "freeform": 24,
    "podcasts": 0,
}


@pytest.fixture(autouse=True)
def no_local_credentials(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real keys out of the offline suite.

    The CLI reads ``.env`` from the working directory, so each test runs from an
    empty temporary one, with every credential variable unset.
    """
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.chdir(request.getfixturevalue("tmp_path"))
    for name in KNOWN_KEYS:
        monkeypatch.delenv(name, raising=False)


def catalog_path(slug: str) -> Path:
    return CATALOGS / f"{slug}.actionsdata"


@pytest.fixture
def synthetic() -> Catalog:
    return load_catalog(catalog_path("synthetic"))


@pytest.fixture
def synthetic_rendered(synthetic: Catalog) -> RenderedCatalog:
    return render_catalog(synthetic)


@pytest.fixture
def weather() -> Catalog:
    return load_catalog(catalog_path("weather"))


@pytest.fixture
def demo_corpus():
    return load_corpus(CORPORA / "synthetic.yaml")


@pytest.fixture
def tmp_catalog(tmp_path: Path):
    """Write an arbitrary dict out as an actionsdata file."""

    def _write(document: object, name: str = "extract.actionsdata") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    return _write
