"""Turn `extract.actionsdata` into a normalized :class:`Catalog`.

The file is plain UTF-8 JSON (see ``DECISIONS.md`` ADR-0002 for how that was
established and what the structure looks like). The top-level keys we care
about are:

``actions``
    ``{intent identifier: node}`` — the intents themselves.
``enums``
    A *list* of enum definitions, each with ``identifier`` and ``cases``.
``entities``
    ``{type name: node}`` — the app's data objects (a note, a list, a book).
``generator``
    ``{"name": "xcode-tools", "version": "..."}`` — the extractor version.

Human-readable strings are wrapped: ``{"alternatives": [], "key": "Open List"}``.
Despite the name, ``key`` holds the literal display string, not a lookup key.

Parsing is deliberately forgiving. One malformed node produces a
:class:`ParseWarning` and is skipped; it never aborts the whole file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intentbench.catalog.locate import LocatedCatalog, locate_catalog
from intentbench.models import (
    Catalog,
    EntityDefinition,
    EnumCase,
    EnumDefinition,
    IntentDefinition,
    ParameterDefinition,
    ParameterType,
    ParseWarning,
)


class ParseError(Exception):
    """The file could not be read at all (not valid JSON, unreadable, empty)."""


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------
#
# `valueType` is a single-key tagged union. The key names the wrapper kind and
# the payload carries the details:
#
#     {"primitive":       {"wrapper": {"typeIdentifier": 0}}}
#     {"entity":          {"wrapper": {"typeName": "AnyListEntity"}}}
#     {"linkEnumeration": {"wrapper": {"identifier": "SleepTimerOption"}}}
#     {"array":           {"wrapper": {"memberValueType": {...}}}}
#     {"measurement":     {"wrapper": {"unitType": 7}}}
#     {"intents":         {"wrapper": {"typeIdentifier": 12}}}
#
# The integers are undocumented. The tables below were derived empirically by
# correlating type identifiers against parameter names and default values across
# every App Intents catalog shipped on macOS 15 (see ADR-0003). Anything not in
# the table maps to UNKNOWN and keeps its raw type — Apple will add types, and
# an unrecognized one must never break a run.

_PRIMITIVE_TYPES: dict[int, ParameterType] = {
    0: ParameterType.STRING,  # String
    1: ParameterType.BOOLEAN,  # Bool
    2: ParameterType.INTEGER,  # Int
    7: ParameterType.NUMBER,  # Double / CGFloat (observed on width/height)
    8: ParameterType.DATE,  # Date
    11: ParameterType.STRING,  # URL — a string to the model
    12: ParameterType.STRING,  # Rich/attributed text (observed on text, body, message)
}

#: `intents`-wrapped identifiers refer to AppIntents framework types.
_INTENTS_TYPES: dict[int, ParameterType] = {
    12: ParameterType.FILE,  # IntentFile — file, folder, image, audioFile
}

_TSM_DEFAULT_VALUE = "LNValueTypeSpecificMetadataKeyDefaultValue"


def _localized(node: Any) -> str | None:
    """Unwrap ``{"alternatives": [...], "key": "Open List"}`` to ``"Open List"``."""
    if isinstance(node, str):
        return node or None
    if isinstance(node, dict):
        key = node.get("key")
        if isinstance(key, str) and key:
            return key
    return None


def _unwrap_tagged_value(node: Any) -> Any:
    """Unwrap a tagged scalar such as ``{"int": {"wrapper": 1}}``.

    Used for default values, which are encoded as a one-key union of
    ``string`` / ``int`` / ``double`` / ``bool`` around a ``wrapper`` payload.
    """
    if not isinstance(node, dict) or len(node) != 1:
        return None
    tag, payload = next(iter(node.items()))
    value = payload["wrapper"] if isinstance(payload, dict) and "wrapper" in payload else payload
    if tag == "bool":
        return bool(value)
    if tag == "staticDeferredLocalizedString":
        return _localized(value)
    if tag in {"string", "int", "double", "float"}:
        return value
    return value


def _pairs(sequence: Any) -> dict[str, Any]:
    """`typeSpecificMetadata` is a flat ``[key, value, key, value, ...]`` list."""
    out: dict[str, Any] = {}
    if not isinstance(sequence, list):
        return out
    for index in range(0, len(sequence) - 1, 2):
        key = sequence[index]
        if isinstance(key, str):
            out[key] = sequence[index + 1]
    return out


def _resolve_value_type(
    value_type: Any,
) -> tuple[ParameterType, str, str | None, str | None, bool]:
    """Return ``(type, raw_type, enum_id, entity_type, is_array)``."""
    if not isinstance(value_type, dict) or not value_type:
        return ParameterType.UNKNOWN, "unspecified", None, None, False

    kind, payload = next(iter(value_type.items()))
    wrapper = payload.get("wrapper") if isinstance(payload, dict) else None
    wrapper = wrapper if isinstance(wrapper, dict) else {}

    if kind == "array":
        member = wrapper.get("memberValueType")
        inner_type, inner_raw, enum_id, entity_type, _ = _resolve_value_type(member)
        return inner_type, f"array<{inner_raw}>", enum_id, entity_type, True

    if kind == "entity":
        type_name = wrapper.get("typeName")
        name = str(type_name) if type_name else None
        return ParameterType.ENTITY, f"entity<{name or '?'}>", None, name, False

    if kind == "linkEnumeration":
        identifier = wrapper.get("identifier")
        name = str(identifier) if identifier else None
        return ParameterType.ENUM, f"enum<{name or '?'}>", name, None, False

    if kind == "measurement":
        # The only measurement observed in the wild is a duration (unitType 7,
        # default unit "min"). Other unit types map to a plain number.
        unit_type = wrapper.get("unitType")
        param_type = ParameterType.DURATION if unit_type == 7 else ParameterType.NUMBER
        return param_type, f"measurement<{unit_type}>", None, None, False

    type_identifier = wrapper.get("typeIdentifier")
    raw = f"{kind}<{type_identifier}>"
    if kind == "primitive" and isinstance(type_identifier, int):
        return _PRIMITIVE_TYPES.get(type_identifier, ParameterType.UNKNOWN), raw, None, None, False
    if kind == "intents" and isinstance(type_identifier, int):
        return _INTENTS_TYPES.get(type_identifier, ParameterType.UNKNOWN), raw, None, None, False

    return ParameterType.UNKNOWN, raw, None, None, False


def _parse_parameter(node: dict[str, Any]) -> ParameterDefinition:
    param_type, raw_type, enum_id, entity_type, is_array = _resolve_value_type(
        node.get("valueType")
    )
    metadata = _pairs(node.get("typeSpecificMetadata"))
    default = (
        _unwrap_tagged_value(metadata[_TSM_DEFAULT_VALUE])
        if _TSM_DEFAULT_VALUE in metadata
        else None
    )
    # Bools are stored as int 0/1; normalize so a rendered schema shows true/false.
    if param_type is ParameterType.BOOLEAN and isinstance(default, int):
        default = bool(default)

    return ParameterDefinition(
        name=str(node["name"]),
        title=_localized(node.get("title")),
        description=_localized(node.get("parameterDescription")),
        type=param_type,
        raw_type=raw_type,
        is_optional=bool(node.get("isOptional", False)),
        default_value=default,
        enum_id=enum_id,
        entity_type=entity_type,
        is_array=is_array,
    )


def _parse_schema_id(node: dict[str, Any]) -> str | None:
    """Schema-conformant intents (`@AppIntent(schema:)`) declare a domain + name.

    A schema-conformant intent implements a shape Apple defined (for example
    ``wordProcessor.CreateWordProcessorDocumentIntent``), so the system already
    knows what it means. Custom intents return ``None``.
    """
    schemas = node.get("assistantDefinedSchemas")
    if not isinstance(schemas, list) or not schemas:
        return None
    first = schemas[0]
    if not isinstance(first, dict):
        return None
    domain = first.get("domain")
    name = first.get("name")
    if domain and name:
        return f"{domain}.{name}"
    return str(name) if name else None


def _parse_intent(identifier: str, node: dict[str, Any]) -> IntentDefinition:
    visibility = node.get("visibilityMetadata")
    visibility = visibility if isinstance(visibility, dict) else {}

    deprecation = node.get("deprecationMetadata")
    deprecation = deprecation if isinstance(deprecation, dict) else None

    description_metadata = node.get("descriptionMetadata")
    description = None
    if isinstance(description_metadata, dict):
        description = _localized(description_metadata.get("descriptionText"))

    parameters: list[ParameterDefinition] = []
    for raw_param in node.get("parameters") or []:
        if isinstance(raw_param, dict) and raw_param.get("name"):
            parameters.append(_parse_parameter(raw_param))

    fully_qualified = node.get("fullyQualifiedTypeName")
    type_name = str(fully_qualified).rsplit(".", 1)[-1] if fully_qualified else identifier

    return IntentDefinition(
        identifier=identifier,
        type_name=type_name,
        title=_localized(node.get("title")),
        description=description,
        schema_id=_parse_schema_id(node),
        parameters=parameters,
        is_discoverable=bool(visibility.get("isDiscoverable", True)),
        is_deprecated=deprecation is not None,
        deprecation_message=(_localized(deprecation.get("messageText")) if deprecation else None),
        replaced_by=(
            str(deprecation["replacedByIntentIdentifier"])
            if deprecation and deprecation.get("replacedByIntentIdentifier")
            else None
        ),
        opens_app_when_run=bool(node.get("openAppWhenRun", False)),
        raw=node,
    )


def _parse_enums(raw: Any, warnings: list[ParseWarning]) -> dict[str, EnumDefinition]:
    out: dict[str, EnumDefinition] = {}
    if not isinstance(raw, list):
        return out
    for node in raw:
        if not isinstance(node, dict):
            continue
        identifier = node.get("identifier")
        if not identifier:
            continue
        try:
            cases = [
                EnumCase(
                    value=str(case["identifier"]),
                    title=_localized((case.get("displayRepresentation") or {}).get("title")),
                )
                for case in node.get("cases") or []
                if isinstance(case, dict) and case.get("identifier")
            ]
            out[str(identifier)] = EnumDefinition(
                identifier=str(identifier),
                display_name=_localized(node.get("displayTypeName")),
                is_system=bool(node.get("isSystem", False)),
                cases=cases,
            )
        except Exception as exc:
            warnings.append(ParseWarning(node=str(identifier), reason=str(exc), kind="enum"))
    return out


def _parse_entities(raw: Any, warnings: list[ParseWarning]) -> dict[str, EntityDefinition]:
    out: dict[str, EntityDefinition] = {}
    if not isinstance(raw, dict):
        return out
    for type_name, node in raw.items():
        if not isinstance(node, dict):
            continue
        try:
            out[str(type_name)] = EntityDefinition(
                type_name=str(type_name),
                display_name=_localized(node.get("displayTypeName")),
                fully_qualified_type_name=(
                    str(node["fullyQualifiedTypeName"])
                    if node.get("fullyQualifiedTypeName")
                    else None
                ),
            )
        except Exception as exc:
            warnings.append(ParseWarning(node=str(type_name), reason=str(exc), kind="entity"))
    return out


def parse_catalog_bytes(
    data: bytes,
    *,
    source_path: Path,
    bundle_identifier: str | None = None,
) -> Catalog:
    """Parse raw ``extract.actionsdata`` bytes into a :class:`Catalog`."""
    if not data.strip():
        raise ParseError(f"{source_path} is empty.")

    try:
        document = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ParseError(
            f"{source_path} is not UTF-8 text. intentbench expects the JSON "
            f"extract.actionsdata format (see DECISIONS.md ADR-0002). Detail: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"{source_path} is not valid JSON at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}\nIf this file came from a newer Xcode, please open a "
            "catalog-parse-failure issue with the file attached."
        ) from exc

    if not isinstance(document, dict):
        raise ParseError(
            f"{source_path}: expected a JSON object at the top level, "
            f"got {type(document).__name__}."
        )

    warnings: list[ParseWarning] = []
    intents: list[IntentDefinition] = []

    actions = document.get("actions")
    if actions is None:
        warnings.append(
            ParseWarning(
                node="<root>",
                reason="no 'actions' key — this file declares no app intents",
                kind="catalog",
            )
        )
        actions = {}
    if not isinstance(actions, dict):
        raise ParseError(
            f"{source_path}: 'actions' should be an object keyed by intent "
            f"identifier, got {type(actions).__name__}."
        )

    for identifier, node in actions.items():
        if not isinstance(node, dict):
            warnings.append(ParseWarning(node=str(identifier), reason="node is not an object"))
            continue
        try:
            intents.append(_parse_intent(str(identifier), node))
        except Exception as exc:
            warnings.append(
                ParseWarning(node=str(identifier), reason=f"{type(exc).__name__}: {exc}")
            )

    intents.sort(key=lambda i: i.identifier)

    generator = document.get("generator")
    extractor_version = None
    if isinstance(generator, dict) and generator.get("version"):
        extractor_version = str(generator["version"])

    return Catalog(
        source_path=source_path,
        bundle_identifier=bundle_identifier,
        intents=intents,
        enums=_parse_enums(document.get("enums"), warnings),
        entities=_parse_entities(document.get("entities"), warnings),
        parse_warnings=warnings,
        extractor_version=extractor_version,
        format_version=(str(document["version"]) if document.get("version") is not None else None),
    )


def load_catalog(path: Path) -> Catalog:
    """Load a catalog from a ``.app`` bundle, a metadata directory, or a data file."""
    located = locate_catalog(Path(path))
    return load_located_catalog(located)


def load_located_catalog(located: LocatedCatalog) -> Catalog:
    """Load a catalog from an already-resolved location."""
    try:
        data = located.actions_path.read_bytes()
    except OSError as exc:
        raise ParseError(f"Could not read {located.actions_path}: {exc}") from exc

    catalog = parse_catalog_bytes(
        data,
        source_path=located.actions_path,
        bundle_identifier=located.bundle_identifier,
    )
    if catalog.extractor_version is None and located.version_json:
        catalog.extractor_version = located.version_json.get("toolsVersion")
    return catalog
