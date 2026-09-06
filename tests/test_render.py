"""Catalog -> judge tool schema."""

from __future__ import annotations

import json

from intentbench.catalog.render import (
    canonical_tools_json,
    humanize_type_name,
    render_catalog,
    sanitize_tool_name,
)
from intentbench.models import NO_MATCH, Catalog, DescriptionSource, RenderedCatalog


def test_abstain_tool_is_always_appended(synthetic_rendered: RenderedCatalog) -> None:
    """Abstention is how the escape hatch stays expressible under forced choice."""
    assert synthetic_rendered.tools[-1].name == NO_MATCH
    assert NO_MATCH not in synthetic_rendered.tool_name_to_identifier


def test_illegal_identifiers_are_sanitized(synthetic_rendered: RenderedCatalog) -> None:
    original = "Weird Identifier / With Punctuation!"
    tool_name = synthetic_rendered.identifier_to_tool_name[original]
    assert tool_name == "Weird_Identifier___With_Punctuation"
    # The map is bidirectional — the original identifier is never lost.
    assert synthetic_rendered.tool_name_to_identifier[tool_name] == original


def test_sanitize_resolves_collisions() -> None:
    taken: set[str] = set()
    first = sanitize_tool_name("A/B", taken)
    taken.add(first)
    second = sanitize_tool_name("A B", taken)
    assert first != second
    assert first == "A_B"
    assert second == "A_B_2"


def test_sanitize_respects_length_limit() -> None:
    name = sanitize_tool_name("X" * 200, set())
    assert len(name) <= 64


def test_description_fallback_chain(synthetic: Catalog) -> None:
    rendered = render_catalog(synthetic)
    sources = rendered.description_sources
    assert sources["AddItemIntent"] is DescriptionSource.DESCRIPTION
    assert sources["TitleOnlyIntent"] is DescriptionSource.TITLE
    assert sources["NoProseAtAllIntent"] is DescriptionSource.TYPE_NAME
    # Intents with no prose at all are named in the report — they are exactly
    # the ones that lose selection races.
    assert rendered.weak_descriptions == ["NoProseAtAllIntent"]


def test_humanize_type_name() -> None:
    assert humanize_type_name("AddItemToShoppingListIntent") == "Add item to shopping list"
    assert humanize_type_name("ArchiveOldListsAppIntent") == "Archive old lists"


def test_optional_parameters_stay_out_of_required(synthetic: Catalog) -> None:
    rendered = render_catalog(synthetic)
    tool = next(t for t in rendered.tools if t.name == "AddItemIntent")
    required = tool.input_schema.get("required", [])
    assert "item" in required
    assert "list" not in required, "optional parameters must not be required"
    # A parameter with a default is satisfiable without the model supplying it.
    assert "quantity" not in required


def test_enums_render_as_enum_arrays(weather: Catalog) -> None:
    rendered = render_catalog(weather)
    tool = next(t for t in rendered.tools if t.name == "OpenWeatherSpecificConditionIntent")
    condition = tool.input_schema["properties"]["specificCondition"]
    assert set(condition["enum"]) >= {"humidity", "wind", "uvi"}
    assert "UV Index" in condition["description"], "case titles help the model choose"


def test_entity_parameters_name_their_type(weather: Catalog) -> None:
    rendered = render_catalog(weather)
    tool = next(t for t in rendered.tools if t.name == "OpenMoonIntent")
    location = tool.input_schema["properties"]["location"]
    assert location["type"] == "string"
    assert "Location" in location["description"]


def test_unknown_types_render_as_strings_with_a_note(synthetic: Catalog) -> None:
    rendered = render_catalog(synthetic)
    tool = next(t for t in rendered.tools if t.name == "EveryTypeIntent")
    unknown = tool.input_schema["properties"]["somethingNew"]
    assert unknown["type"] == "string"
    assert "primitive<9999>" in unknown["description"]


def test_arrays_render_as_arrays(synthetic: Catalog) -> None:
    rendered = render_catalog(synthetic)
    tool = next(t for t in rendered.tools if t.name == "EveryTypeIntent")
    assert tool.input_schema["properties"]["manyStrings"]["type"] == "array"
    assert tool.input_schema["properties"]["manyStrings"]["items"]["type"] == "string"


def test_defaults_are_carried_into_the_schema(synthetic: Catalog) -> None:
    rendered = render_catalog(synthetic)
    tool = next(t for t in rendered.tools if t.name == "AddItemIntent")
    assert tool.input_schema["properties"]["quantity"]["default"] == 1


def test_rendering_a_subset(synthetic: Catalog) -> None:
    subset = [i for i in synthetic.intents if i.identifier == "AddItemIntent"]
    rendered = render_catalog(synthetic, subset)
    assert [t.name for t in rendered.tools] == ["AddItemIntent", NO_MATCH]


# --- cache key stability --------------------------------------------------


def test_canonical_json_is_order_independent(synthetic: Catalog) -> None:
    """Key stability must not depend on dict ordering."""
    rendered = render_catalog(synthetic)
    shuffled = list(reversed(rendered.tools))
    assert canonical_tools_json(rendered.tools) == canonical_tools_json(shuffled)


def test_canonical_json_changes_when_a_description_changes(synthetic: Catalog) -> None:
    """Editing an intent description must invalidate the cache."""
    before = canonical_tools_json(render_catalog(synthetic).tools)

    intent = synthetic.by_identifier("AddItemIntent")
    assert intent is not None
    intent.description = "Adds an item. Now with a better description."

    after = canonical_tools_json(render_catalog(synthetic).tools)
    assert before != after


def test_canonical_json_is_compact_and_sorted(synthetic: Catalog) -> None:
    payload = canonical_tools_json(render_catalog(synthetic).tools)
    decoded = json.loads(payload)
    # Compact separators: re-encoding the decoded value must reproduce it byte
    # for byte, which is only true if keys are sorted and whitespace is absent.
    assert json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == payload
    assert [tool["name"] for tool in decoded] == sorted(t["name"] for t in decoded)
