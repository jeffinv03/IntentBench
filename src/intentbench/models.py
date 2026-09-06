"""Every pydantic model in intentbench, in one place.

The models fall into four groups:

* **Catalog** — what we read out of an app bundle (`Catalog`, `IntentDefinition`, ...).
* **Corpus** — what the user writes (`CorpusFile`, `CorpusCase`, ...), defined in
  `intentbench.corpus.schema` but re-exported here for convenience.
* **Judge** — what a model returns when asked to route a phrase (`JudgeResult`).
* **Run** — the scored output of a whole run (`RunReport`), which doubles as the
  baseline format for `intentbench diff`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class ParameterType(str, Enum):
    """Normalized parameter types.

    App Intents encodes types as integers whose meaning depends on the wrapper
    kind. We map the ones we have observed and fall back to ``UNKNOWN`` for the
    rest — an unknown type is never a reason to drop a parameter.
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DURATION = "duration"
    ENUM = "enum"
    ENTITY = "entity"
    FILE = "file"
    UNKNOWN = "unknown"


class ParameterDefinition(BaseModel):
    """One parameter of one intent."""

    name: str
    title: str | None = None
    description: str | None = None
    type: ParameterType = ParameterType.UNKNOWN
    raw_type: str = ""
    is_optional: bool = False
    default_value: Any | None = None
    enum_id: str | None = None
    entity_type: str | None = None
    is_array: bool = False


class IntentDefinition(BaseModel):
    """One app intent — the App Intents analogue of an MCP tool."""

    identifier: str
    type_name: str
    title: str | None = None
    description: str | None = None
    schema_id: str | None = None
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    is_discoverable: bool = True
    is_deprecated: bool = False
    deprecation_message: str | None = None
    replaced_by: str | None = None
    opens_app_when_run: bool = False
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)


class EnumCase(BaseModel):
    value: str
    title: str | None = None


class EnumDefinition(BaseModel):
    identifier: str
    display_name: str | None = None
    is_system: bool = False
    cases: list[EnumCase] = Field(default_factory=list)


class EntityDefinition(BaseModel):
    type_name: str
    display_name: str | None = None
    fully_qualified_type_name: str | None = None


class ParseWarning(BaseModel):
    """A node we could not fully understand.

    Warnings never abort a parse. A catalog with 14 of 15 intents plus one loud
    warning is far more useful than a stack trace.
    """

    node: str
    reason: str
    kind: str = "intent"


class Catalog(BaseModel):
    """The full intent catalog extracted from a bundle."""

    source_path: Path
    bundle_identifier: str | None = None
    intents: list[IntentDefinition] = Field(default_factory=list)
    enums: dict[str, EnumDefinition] = Field(default_factory=dict)
    entities: dict[str, EntityDefinition] = Field(default_factory=dict)
    parse_warnings: list[ParseWarning] = Field(default_factory=list)
    extractor_version: str | None = None
    format_version: str | None = None

    def by_identifier(self, identifier: str) -> IntentDefinition | None:
        for intent in self.intents:
            if intent.identifier == identifier:
                return intent
        return None

    @property
    def identifiers(self) -> list[str]:
        return [i.identifier for i in self.intents]


class FilterStats(BaseModel):
    """What `--include-all` would have added back.

    Reported explicitly: silent filtering causes "why is my intent missing"
    bug reports.
    """

    hidden: int = 0
    deprecated: int = 0

    @property
    def total(self) -> int:
        return self.hidden + self.deprecated


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

#: Sentinel tool name used to capture abstention.
NO_MATCH = "no_matching_intent"


class ToolSchema(BaseModel):
    """A single tool as handed to the judge model."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class DescriptionSource(str, Enum):
    """Where an intent's tool description came from.

    Intents falling back to ``TYPE_NAME`` have no human-authored prose at all,
    and are exactly the ones that tend to lose selection races. The report says so.
    """

    DESCRIPTION = "description"
    TITLE = "title"
    TYPE_NAME = "type_name"


class RenderedCatalog(BaseModel):
    """Tool schemas plus the bookkeeping needed to map back to intents."""

    tools: list[ToolSchema]
    tool_name_to_identifier: dict[str, str]
    identifier_to_tool_name: dict[str, str]
    description_sources: dict[str, DescriptionSource] = Field(default_factory=dict)

    @property
    def weak_descriptions(self) -> list[str]:
        """Identifiers whose description had to be synthesized from the type name."""
        return sorted(
            ident
            for ident, src in self.description_sources.items()
            if src is DescriptionSource.TYPE_NAME
        )


class JudgeResult(BaseModel):
    """One routing decision."""

    selected_intent: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    from_cache: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class CaseOutcome(str, Enum):
    MATCH = "match"
    WRONG_INTENT = "wrong_intent"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    PARAM_MISMATCH = "param_mismatch"
    ERROR = "error"


#: Outcomes that count as a pass.
PASSING = frozenset({CaseOutcome.MATCH})


class ParameterComparison(BaseModel):
    name: str
    expected: Any | None = None
    actual: Any | None = None
    matched: bool = False
    literal_date_comparison: bool = False


class CaseResult(BaseModel):
    """One corpus case, scored."""

    phrase: str
    locale: str = "en-US"
    tags: list[str] = Field(default_factory=list)
    note: str | None = None

    expected_intent: str | None = None
    expected_parameters: dict[str, Any] = Field(default_factory=dict)

    selected_intent: str | None = None
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    extra_parameters: dict[str, Any] = Field(default_factory=dict)

    outcome: CaseOutcome = CaseOutcome.ERROR
    parameter_comparisons: list[ParameterComparison] = Field(default_factory=list)

    repeats: int = 1
    agreement: int = 1
    unstable: bool = False
    observed_selections: list[str | None] = Field(default_factory=list)

    from_cache: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome in PASSING

    @property
    def intent_correct(self) -> bool:
        """True when the right intent (or the right abstention) was chosen."""
        return self.outcome in (CaseOutcome.MATCH, CaseOutcome.PARAM_MISMATCH)


class IntentCoverage(BaseModel):
    identifier: str
    targeted: int = 0
    passed: int = 0

    @property
    def covered(self) -> bool:
        return self.targeted > 0


class RunMetrics(BaseModel):
    total: int = 0
    errors: int = 0
    matched: int = 0

    intent_accuracy: float | None = None

    parameter_scored: int = 0
    parameter_matched: int = 0
    parameter_accuracy: float | None = None

    abstention_expected: int = 0
    false_positives: int = 0
    false_positive_rate: float | None = None
    false_negatives: int = 0

    unstable: int = 0

    outcome_counts: dict[str, int] = Field(default_factory=dict)
    coverage: list[IntentCoverage] = Field(default_factory=list)

    @property
    def uncovered_intents(self) -> list[str]:
        return [c.identifier for c in self.coverage if not c.covered]


class UsageSummary(BaseModel):
    api_calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


class RunMetadata(BaseModel):
    intentbench_version: str
    judge: str
    model_id: str
    prompt_version: int
    catalog_hash: str
    catalog_source: str
    corpus_path: str | None = None
    app_label: str | None = None
    timestamp: datetime
    repeat: int = 1
    tool_count: int = 0


class RunReport(BaseModel):
    """The full run. Written by `--json`, read back by `intentbench diff`.

    One schema for results and baselines — not two.
    """

    model_config = ConfigDict(ser_json_timedelta="iso8601")

    schema_version: int = 1
    metadata: RunMetadata
    metrics: RunMetrics
    cases: list[CaseResult] = Field(default_factory=list)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    parse_warnings: list[ParseWarning] = Field(default_factory=list)
    weak_descriptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class DiffEntry(BaseModel):
    phrase: str
    before: str | None = None
    after: str | None = None
    before_outcome: CaseOutcome | None = None
    after_outcome: CaseOutcome | None = None


class DiffReport(BaseModel):
    regressions: list[DiffEntry] = Field(default_factory=list)
    fixes: list[DiffEntry] = Field(default_factory=list)
    unchanged: int = 0
    added: list[DiffEntry] = Field(default_factory=list)
    removed: list[DiffEntry] = Field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)
