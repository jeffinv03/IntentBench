"""Turn a :class:`Catalog` into the tool list handed to the judge model.

This is the heart of the proxy. Each app intent becomes one tool, exactly as an
MCP server would expose it, and the judge is asked to pick one. If two intents
read the same to a model given only their titles and descriptions, that is a
catalog-quality defect — and it is model-independent, which is why the proxy
carries signal even though it is not Siri.
"""

from __future__ import annotations

import json
import re
from typing import Any

from intentbench.models import (
    NO_MATCH,
    Catalog,
    DescriptionSource,
    IntentDefinition,
    ParameterDefinition,
    ParameterType,
    RenderedCatalog,
    ToolSchema,
)

#: The Anthropic and OpenAI tool-name grammars agree on this shape.
_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_NAME = 64

#: The escape hatch. Always appended, so abstention is a first-class answer
#: rather than an unparseable non-answer.
ABSTAIN_TOOL = ToolSchema(
    name=NO_MATCH,
    description=(
        "Use this when the request does not correspond to any of the other available actions."
    ),
    input_schema={"type": "object", "properties": {}},
)

_JSON_TYPES: dict[ParameterType, str] = {
    ParameterType.STRING: "string",
    ParameterType.INTEGER: "integer",
    ParameterType.NUMBER: "number",
    ParameterType.BOOLEAN: "boolean",
    ParameterType.DATE: "string",
    ParameterType.DURATION: "string",
    ParameterType.ENUM: "string",
    ParameterType.ENTITY: "string",
    ParameterType.FILE: "string",
    ParameterType.UNKNOWN: "string",
}


def sanitize_tool_name(identifier: str, taken: set[str]) -> str:
    """Coerce an intent identifier into a legal, unique tool name.

    Collisions are resolved with a numeric suffix rather than by dropping an
    intent — every intent must be selectable, or the eval is measuring the wrong
    catalog.
    """
    name = _TOOL_NAME_RE.sub("_", identifier).strip("_") or "intent"
    name = name[:_MAX_TOOL_NAME]
    if name not in taken:
        return name
    stem = name
    for suffix in range(2, 1000):
        tail = f"_{suffix}"
        candidate = stem[: _MAX_TOOL_NAME - len(tail)] + tail
        if candidate not in taken:
            return candidate
    raise ValueError(f"Could not find a unique tool name for {identifier!r}")


def humanize_type_name(type_name: str) -> str:
    """``AddItemToShoppingListIntent`` -> ``Add item to shopping list``.

    The last-resort description for an intent that has neither a description nor
    a title. It is a poor substitute for prose, which is the point — these
    intents get flagged in the report.
    """
    stem = re.sub(r"(AppIntent|Intent)$", "", type_name)
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", stem)
    words = words.replace("_", " ").strip()
    if not words:
        return type_name
    return words[0].upper() + words[1:].lower()


def _join_sentences(parts: list[str]) -> str:
    """Join description fragments into readable prose.

    App-authored parameter descriptions rarely end in punctuation ("Location to
    show moon details for"), and we append our own type hints after them. Without
    this the model sees two sentences run together.
    """
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    out: list[str] = []
    for index, part in enumerate(cleaned):
        if index < len(cleaned) - 1 and part[-1] not in ".!?;:":
            part += "."
        out.append(part)
    return " ".join(out)


def _describe_intent(intent: IntentDefinition) -> tuple[str, DescriptionSource]:
    if intent.description:
        return intent.description, DescriptionSource.DESCRIPTION
    if intent.title:
        return intent.title, DescriptionSource.TITLE
    return humanize_type_name(intent.type_name), DescriptionSource.TYPE_NAME


def _parameter_description(parameter: ParameterDefinition, catalog: Catalog) -> str:
    """Build the parameter's description, adding type context the model needs.

    An entity parameter is a reference to one of the app's own objects ("the
    note about Mira"). The model can't resolve it, but naming the entity type
    tells it what kind of string belongs there.
    """
    parts: list[str] = []
    if parameter.description:
        parts.append(parameter.description)
    elif parameter.title:
        parts.append(parameter.title)

    if parameter.type is ParameterType.ENTITY and parameter.entity_type:
        entity = catalog.entities.get(parameter.entity_type)
        display = (entity.display_name if entity else None) or parameter.entity_type
        parts.append(f"Refers to a {display} in the app; give the name the user said.")
    elif parameter.type is ParameterType.DATE:
        parts.append("A date or time, as the user expressed it.")
    elif parameter.type is ParameterType.DURATION:
        parts.append("A duration, as the user expressed it.")
    elif parameter.type is ParameterType.FILE:
        parts.append("A file the user referred to.")
    elif parameter.type is ParameterType.UNKNOWN:
        # Unknown types are rendered as strings with the raw type named, so the
        # model still has something to go on and the run does not break.
        parts.append(f"Value of app-specific type {parameter.raw_type}.")

    if parameter.is_array:
        parts.append("One or more values.")

    return _join_sentences(parts)


def _parameter_schema(parameter: ParameterDefinition, catalog: Catalog) -> dict[str, Any]:
    scalar: dict[str, Any] = {"type": _JSON_TYPES[parameter.type]}

    if parameter.type is ParameterType.ENUM and parameter.enum_id:
        enum_def = catalog.enums.get(parameter.enum_id)
        if enum_def and enum_def.cases:
            scalar["enum"] = [case.value for case in enum_def.cases]
            labels = ", ".join(
                f"{case.value} ({case.title})" if case.title else case.value
                for case in enum_def.cases
            )
            scalar["description"] = f"One of: {labels}."

    schema: dict[str, Any] = {"type": "array", "items": scalar} if parameter.is_array else scalar

    description = _parameter_description(parameter, catalog)
    enum_hint = scalar.get("description") if parameter.is_array else schema.get("description")
    merged = _join_sentences(
        [description, enum_hint if enum_hint != description else None]  # type: ignore[list-item]
    )
    if merged:
        schema["description"] = merged

    if parameter.default_value is not None:
        schema["default"] = parameter.default_value

    return schema


def render_intent(intent: IntentDefinition, catalog: Catalog, tool_name: str) -> ToolSchema:
    """Render one intent as one tool."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in intent.parameters:
        properties[parameter.name] = _parameter_schema(parameter, catalog)
        if not parameter.is_optional and parameter.default_value is None:
            required.append(parameter.name)

    input_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required

    description, _ = _describe_intent(intent)
    return ToolSchema(name=tool_name, description=description, input_schema=input_schema)


def render_catalog(
    catalog: Catalog, intents: list[IntentDefinition] | None = None
) -> RenderedCatalog:
    """Render a catalog (or a filtered subset of it) into judge tool schemas."""
    selected = catalog.intents if intents is None else intents

    tools: list[ToolSchema] = []
    tool_to_identifier: dict[str, str] = {}
    identifier_to_tool: dict[str, str] = {}
    description_sources: dict[str, DescriptionSource] = {}
    taken: set[str] = {NO_MATCH}

    for intent in selected:
        tool_name = sanitize_tool_name(intent.identifier, taken)
        taken.add(tool_name)
        tools.append(render_intent(intent, catalog, tool_name))
        tool_to_identifier[tool_name] = intent.identifier
        identifier_to_tool[intent.identifier] = tool_name
        description_sources[intent.identifier] = _describe_intent(intent)[1]

    tools.append(ABSTAIN_TOOL)

    return RenderedCatalog(
        tools=tools,
        tool_name_to_identifier=tool_to_identifier,
        identifier_to_tool_name=identifier_to_tool,
        description_sources=description_sources,
    )


def canonical_tools_json(tools: list[ToolSchema]) -> str:
    """Deterministic serialization of the tool list, for the cache key.

    Sorted keys and no whitespace, so key stability does not depend on dict
    ordering. Changing an intent description must invalidate the cache; changing
    nothing must not.
    """
    payload = sorted((tool.to_api_dict() for tool in tools), key=lambda t: str(t["name"]))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
