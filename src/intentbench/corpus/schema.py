"""The corpus file format.

A corpus is a YAML file of natural-language phrases and what each one should
select. Three design points carry weight:

* ``intent: null`` is a first-class expectation. Over-matching — an app that
  grabs "what's the weather in Tokyo" — is a real failure mode, and abstention
  is scored, not ignored.
* Parameter expectations are **partial**. Only the parameters you list are
  scored, so you are never forced to pin down something the phrase leaves open.
* ``version: 1`` is required, so the format can change later without any file
  being silently misread.

Unknown keys are rejected everywhere. A typo in a corpus is a config problem,
and config problems fail loudly and immediately — before any API spend.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_STRICT = ConfigDict(extra="forbid")


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """One problem found in a corpus, tied back to where it came from."""

    severity: IssueSeverity
    message: str
    case_index: int | None = None
    phrase: str | None = None
    suggestion: str | None = None

    def render(self) -> str:
        where = f"case {self.case_index + 1}" if self.case_index is not None else "corpus"
        text = f"{where}: {self.message}"
        if self.phrase:
            text = f'{where} ("{self.phrase}"): {self.message}'
        if self.suggestion:
            text += f"\n    did you mean: {self.suggestion}?"
        return text


class Expectation(BaseModel):
    """What a phrase should select.

    ``intent`` is ``None`` when nothing in the app should match. Because that is
    also what an omitted key would look like, the distinction between "expects
    abstention" and "forgot to write an expectation" is enforced in
    :class:`CorpusCase`.
    """

    model_config = _STRICT

    intent: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @property
    def expects_abstention(self) -> bool:
        return self.intent is None


class CorpusCase(BaseModel):
    model_config = _STRICT

    phrase: str
    expect: Expectation
    tags: list[str] = Field(default_factory=list)
    note: str | None = None
    locale: str | None = None

    @field_validator("phrase")
    @classmethod
    def _phrase_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("phrase must not be empty")
        return value

    @model_validator(mode="after")
    def _abstention_needs_no_parameters(self) -> CorpusCase:
        if self.expect.expects_abstention and self.expect.parameters:
            raise ValueError(
                "a case expecting no intent (intent: null) cannot also expect parameters"
            )
        return self


class CorpusDefaults(BaseModel):
    model_config = _STRICT

    locale: str = "en-US"


class CorpusFile(BaseModel):
    model_config = _STRICT

    version: int
    defaults: CorpusDefaults = Field(default_factory=CorpusDefaults)
    cases: list[CorpusCase] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(
                f"unsupported corpus version {value}; this build of intentbench "
                "understands version 1"
            )
        return value

    def locale_for(self, case: CorpusCase) -> str:
        return case.locale or self.defaults.locale

    def filtered(self, tags: list[str] | None) -> list[CorpusCase]:
        """Cases carrying any of *tags* — used by ``--filter smoke``."""
        if not tags:
            return list(self.cases)
        wanted = {t.strip() for t in tags if t.strip()}
        return [c for c in self.cases if wanted & set(c.tags)]
