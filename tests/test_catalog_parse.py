"""Parser tests: one per committed fixture, plus one per malformed-input class."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentbench.catalog import ParseError, load_catalog
from intentbench.catalog.locate import LocateError, locate_catalog
from intentbench.catalog.parse import parse_catalog_bytes
from intentbench.models import Catalog, ParameterType
from tests.conftest import REAL_CATALOGS, catalog_path

# --- one test per committed real fixture ---------------------------------


@pytest.mark.parametrize(("slug", "expected_count"), sorted(REAL_CATALOGS.items()))
def test_real_fixture_parses(slug: str, expected_count: int) -> None:
    parsed = load_catalog(catalog_path(slug))
    assert len(parsed.intents) == expected_count
    assert parsed.parse_warnings == []
    for intent in parsed.intents:
        assert intent.identifier
        assert intent.type_name
        assert intent.raw, "raw must always be retained for --dump-raw"


def test_real_fixtures_have_provenance_sidecars() -> None:
    """Every fixture records where it came from — required by CONTRIBUTING."""
    for slug in REAL_CATALOGS:
        sidecar = catalog_path(slug).with_suffix(".meta.json")
        assert sidecar.is_file(), f"{slug} has no .meta.json"
        meta = json.loads(sidecar.read_text())
        assert meta["source_app"]
        assert meta["metadata_tools_version"]
        assert "redacted" in meta


def test_reminders_smallest_shape() -> None:
    parsed = load_catalog(catalog_path("reminders"))
    intent = parsed.intents[0]
    assert intent.identifier == "TTROpenSmartListAppIntent"
    assert intent.title == "Open List"
    assert intent.description == "Opens a list in Reminders."
    assert intent.opens_app_when_run is True
    (parameter,) = intent.parameters
    assert parameter.name == "target"
    assert parameter.type is ParameterType.ENTITY
    assert parameter.entity_type == "AnyListEntity"
    assert "AnyListEntity" in parsed.entities


def test_weather_enums_resolve() -> None:
    parsed = load_catalog(catalog_path("weather"))
    intent = parsed.by_identifier("OpenWeatherSpecificConditionIntent")
    assert intent is not None
    condition = next(p for p in intent.parameters if p.name == "specificCondition")
    assert condition.type is ParameterType.ENUM
    assert condition.enum_id is not None
    enum = parsed.enums[condition.enum_id]
    assert {case.value for case in enum.cases} >= {"humidity", "wind", "uvi"}
    assert any(case.title == "UV Index" for case in enum.cases)


def test_voicememos_marks_hidden_intents() -> None:
    parsed = load_catalog(catalog_path("voicememos"))
    hidden = [i for i in parsed.intents if not i.is_discoverable]
    assert len(hidden) == 4


def test_freeform_marks_deprecated_intents() -> None:
    """Deprecation is representable in the metadata — verified during the spike."""
    parsed = load_catalog(catalog_path("freeform"))
    deprecated = [i for i in parsed.intents if i.is_deprecated]
    assert len(deprecated) == 4
    replaced = [i for i in deprecated if i.replaced_by]
    assert replaced, "at least one deprecated intent names its replacement"


def test_freeform_marks_schema_conformant_intents() -> None:
    parsed = load_catalog(catalog_path("freeform"))
    schema_ids = {i.schema_id for i in parsed.intents if i.schema_id}
    assert schema_ids
    assert all("." in sid for sid in schema_ids)


def test_podcasts_declares_no_intents() -> None:
    """A valid catalog with an empty `actions` map parses cleanly, with no warning.

    An app that ships the metadata directory but declares no intents is normal,
    not suspect. Only a *missing* `actions` key warrants a warning.
    """
    parsed = load_catalog(catalog_path("podcasts"))
    assert parsed.intents == []
    assert parsed.parse_warnings == []


# --- the synthetic fixture, which covers what the real ones don't ---------


def test_synthetic_every_parameter_type(synthetic: Catalog) -> None:
    intent = synthetic.by_identifier("EveryTypeIntent")
    assert intent is not None
    types = {p.name: p.type for p in intent.parameters}
    assert types["aString"] is ParameterType.STRING
    assert types["aBool"] is ParameterType.BOOLEAN
    assert types["anInteger"] is ParameterType.INTEGER
    assert types["aNumber"] is ParameterType.NUMBER
    assert types["aDate"] is ParameterType.DATE
    assert types["aURL"] is ParameterType.STRING
    assert types["richText"] is ParameterType.STRING
    assert types["aDuration"] is ParameterType.DURATION
    assert types["aFile"] is ParameterType.FILE
    assert types["anEnum"] is ParameterType.ENUM
    assert types["manyEntities"] is ParameterType.ENTITY


def test_synthetic_unknown_types_survive(synthetic: Catalog) -> None:
    """Unknown types are not errors. Apple will add types; we must not break."""
    intent = synthetic.by_identifier("EveryTypeIntent")
    assert intent is not None
    by_name = {p.name: p for p in intent.parameters}

    for name, raw in [
        ("somethingNew", "primitive<9999>"),
        ("somethingNewer", "thisWrapperDoesNotExistYet<3>"),
        ("noValueTypeAtAll", "unspecified"),
    ]:
        parameter = by_name[name]
        assert parameter.type is ParameterType.UNKNOWN
        assert parameter.raw_type == raw, "raw_type is always retained"


def test_synthetic_arrays(synthetic: Catalog) -> None:
    intent = synthetic.by_identifier("EveryTypeIntent")
    assert intent is not None
    by_name = {p.name: p for p in intent.parameters}
    assert by_name["manyEntities"].is_array is True
    assert by_name["manyEntities"].raw_type == "array<entity<ShoppingListEntity>>"
    assert by_name["manyStrings"].is_array is True
    assert by_name["aString"].is_array is False


def test_synthetic_defaults_and_optionality(synthetic: Catalog) -> None:
    intent = synthetic.by_identifier("AddItemIntent")
    assert intent is not None
    by_name = {p.name: p for p in intent.parameters}
    assert by_name["item"].is_optional is False
    assert by_name["list"].is_optional is True
    assert by_name["quantity"].default_value == 1

    every = synthetic.by_identifier("EveryTypeIntent")
    assert every is not None
    every_by_name = {p.name: p for p in every.parameters}
    assert every_by_name["aString"].default_value == "hello"
    # Bools are stored as int 0/1 and must normalize.
    assert every_by_name["aBool"].default_value is True


def test_synthetic_enum_case_without_title(synthetic: Catalog) -> None:
    enum = synthetic.enums["SortOrder"]
    assert enum.display_name == "Sort Order"
    by_value = {case.value: case.title for case in enum.cases}
    assert by_value["alphabetical"] == "Alphabetical"
    assert by_value["manual"] is None


def test_synthetic_schema_conformance(synthetic: Catalog) -> None:
    assert synthetic.by_identifier("CreateShoppingListIntent").schema_id == "lists.CreateListIntent"
    assert synthetic.by_identifier("AddItemIntent").schema_id is None


def test_one_bad_node_warns_and_the_rest_survive(synthetic: Catalog) -> None:
    """Never fail the whole parse on one bad node."""
    assert len(synthetic.intents) == 9
    assert len(synthetic.parse_warnings) == 1
    warning = synthetic.parse_warnings[0]
    assert warning.node == "MalformedIntent"
    assert "not an object" in warning.reason


def test_description_and_title_fallbacks(synthetic: Catalog) -> None:
    assert synthetic.by_identifier("TitleOnlyIntent").description is None
    assert synthetic.by_identifier("TitleOnlyIntent").title == "Sync Everything"
    assert synthetic.by_identifier("NoProseAtAllIntent").description is None
    assert synthetic.by_identifier("NoProseAtAllIntent").title is None


# --- malformed input classes ---------------------------------------------


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "extract.actionsdata"
    path.write_bytes(b"")
    with pytest.raises(ParseError, match="empty"):
        load_catalog(path)


def test_truncated_json(tmp_path: Path) -> None:
    path = tmp_path / "extract.actionsdata"
    path.write_text('{"actions": {"A": {"identifier": "A"', encoding="utf-8")
    with pytest.raises(ParseError, match="not valid JSON"):
        load_catalog(path)


def test_wrong_format_binary(tmp_path: Path) -> None:
    """A binary plist — the format this file plausibly could have been."""
    path = tmp_path / "extract.actionsdata"
    path.write_bytes(b"bplist00\xd1\x01\x02\x5f\x10\x0f")
    with pytest.raises(ParseError):
        load_catalog(path)


def test_top_level_not_an_object(tmp_catalog) -> None:
    with pytest.raises(ParseError, match="top level"):
        load_catalog(tmp_catalog([1, 2, 3]))


def test_actions_wrong_shape(tmp_catalog) -> None:
    with pytest.raises(ParseError, match=r"'actions' should be an object"):
        load_catalog(tmp_catalog({"actions": ["nope"]}))


def test_catalog_without_actions_key_warns(tmp_catalog) -> None:
    parsed = load_catalog(tmp_catalog({"version": 1}))
    assert parsed.intents == []
    assert parsed.parse_warnings[0].kind == "catalog"


def test_missing_path() -> None:
    with pytest.raises(LocateError, match="No such file"):
        load_catalog(Path("/nonexistent/Nope.app"))


def test_directory_without_metadata(tmp_path: Path) -> None:
    bundle = tmp_path / "Empty.app"
    (bundle / "Contents").mkdir(parents=True)
    with pytest.raises(LocateError, match=r"no Metadata\.appintents directory"):
        load_catalog(bundle)


def test_parse_bytes_rejects_non_utf8() -> None:
    with pytest.raises(ParseError, match="not UTF-8"):
        parse_catalog_bytes(b"\xff\xfe\x00\x01", source_path=Path("x"))


# --- locate ---------------------------------------------------------------


def _make_bundle(root: Path, relative: str) -> Path:
    """Build a fake .app with the metadata directory at *relative*."""
    bundle = root / "Fake.app"
    metadata = bundle / relative
    metadata.mkdir(parents=True)
    (metadata / "extract.actionsdata").write_text(
        json.dumps({"actions": {}, "version": 1}), encoding="utf-8"
    )
    (metadata / "version.json").write_text(
        json.dumps({"version": "3.0", "toolsVersion": "17E6107"}), encoding="utf-8"
    )
    return bundle


def test_locate_macos_layout(tmp_path: Path) -> None:
    """macOS bundles nest the directory under Contents/Resources."""
    bundle = _make_bundle(tmp_path, "Contents/Resources/Metadata.appintents")
    located = locate_catalog(bundle)
    assert located.actions_path.name == "extract.actionsdata"
    assert located.bundle_path == bundle
    assert located.version_json == {"version": "3.0", "toolsVersion": "17E6107"}


def test_locate_ios_flat_layout(tmp_path: Path) -> None:
    """iOS bundles are flat — the directory sits at the bundle root."""
    bundle = _make_bundle(tmp_path, "Metadata.appintents")
    located = locate_catalog(bundle)
    assert located.actions_path.is_file()
    assert located.bundle_path == bundle


def test_locate_accepts_metadata_directory(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, "Contents/Resources/Metadata.appintents")
    located = locate_catalog(bundle / "Contents/Resources/Metadata.appintents")
    assert located.metadata_dir is not None
    assert located.bundle_path is None


def test_locate_accepts_the_file_directly() -> None:
    """Users attach this single file to bug reports; it must work on its own."""
    located = locate_catalog(catalog_path("weather"))
    assert located.actions_path == catalog_path("weather")
    assert located.bundle_path is None


def test_locate_reads_extractor_version_from_sidecar(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, "Contents/Resources/Metadata.appintents")
    parsed = load_catalog(bundle)
    assert parsed.extractor_version == "17E6107"
