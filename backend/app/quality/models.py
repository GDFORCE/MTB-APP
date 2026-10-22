"""Versioned gold/scenario contracts. Engineering fixtures never imply clinical sign-off."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.domain.schedule.models import PatientContext, UniversalSchedule

SCHEMA_VERSION = "uctsm-quality.v1"
REQUIRED_CONSTRUCTS = frozenset({
    "linear", "cycle", "actual_relative", "branch", "multi_track", "recurrence",
    "device", "amendment", "conflict", "unsupported"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Source(Strict):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_status: Literal["VERIFIED", "UNVERIFIED"] = "UNVERIFIED"
    deidentified: bool
    synthetic: bool
    constructs: set[str] = Field(min_length=1)

    @field_serializer("constructs")
    def _ordered_constructs(self, value: set[str]) -> list[str]:
        # A set has no stable order; the manifest hash must not depend on hash randomisation.
        return sorted(value)


class ExpectedField(Strict):
    """A stable rule identity + exact field path; no guessed event equivalence."""
    rule_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    value: Any
    critical: bool = True
    equivalence: Literal["EXACT", "UNORDERED_STRINGS"] = "EXACT"


IDENTITY_STATUSES = ("MATCH", "MISMATCH", "UNCERTAIN", "DUPLICATE", "NEW_VERSION")
#: A document that must NOT be accepted for extraction; predicting any of ACCEPTING for it is a critical false accept.
NOT_ACCEPTABLE = frozenset({"MISMATCH", "UNCERTAIN"})
ACCEPTING = frozenset({"MATCH", "NEW_VERSION", "DUPLICATE"})


class ExpectedIdentity(Strict):
    """The identity decision the document must receive (a mislabelled file must be MISMATCH, never accepted)."""
    status: Literal["MATCH", "MISMATCH", "UNCERTAIN", "DUPLICATE", "NEW_VERSION"]
    identifiers: list[str] = Field(default_factory=list)


class ExpectedEvidence(Strict):
    """Where in the document a rule comes from: page and the (whitespace-normalized) quoted text."""
    rule_id: str = Field(min_length=1)
    page: int = Field(gt=0)
    quote: str = Field(min_length=3)


class ExpectedMapping(Strict):
    """How an amendment relates old and new events; a rename or split must need review, never auto-confirm."""
    source_codes: list[str] = Field(default_factory=list)
    target_codes: list[str] = Field(default_factory=list)
    mapping_type: str = Field(min_length=1)
    requires_review: bool = False


class ExpectedVisit(Strict):
    event_code: str
    occurrence_index: int = Field(default=0, ge=0)
    status: str
    target: date | datetime | None
    earliest: date | datetime | None = None
    latest: date | datetime | None = None


class Scenario(Strict):
    id: str = Field(min_length=1)
    as_of: date
    horizon: date
    schedule: UniversalSchedule
    context: PatientContext
    expected: list[ExpectedVisit] = Field(default_factory=list)
    expected_validation_codes: set[str] = Field(default_factory=set)
    safety_critical: bool = True

    @field_serializer("expected_validation_codes")
    def _ordered_codes(self, value: set[str]) -> list[str]:
        return sorted(value)

    @model_validator(mode="after")
    def coherent(self):
        if self.horizon < self.as_of:
            raise ValueError("scenario horizon precedes its as-of date")
        if self.context.schedule_version_id != self.schedule.schedule_version_id:
            raise ValueError("scenario context belongs to another schedule version")
        keys = [(row.event_code, row.occurrence_index) for row in self.expected]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate expected logical occurrence")
        if not self.expected and not self.expected_validation_codes:
            raise ValueError("a scenario must state an executable or blocking expectation")
        return self


class ExpectedCalendar(Strict):
    """Hand-written expectation for one logical visit of the committed calendar after a step."""
    logical_key: str
    planned: date
    earliest: date | None = None
    latest: date | None = None
    completed: bool | None = None
    actual_visit: date | None = None
    actual_treatment: date | None = None


class Step(Strict):
    """One transactional actual-occurrence command and exactly what it must change."""
    logical_key: str
    visit_date: date
    treatment_date: date | None = None
    #: the clock when the site records it (an actual date may not lie after it)
    recorded_on: date
    #: logical keys whose protocol target must move (and no others)
    expect_moved: list[str] = Field(default_factory=list)
    #: whether the command must create a new immutable evaluation
    expect_reevaluated: bool
    expected: list[ExpectedCalendar] = Field(default_factory=list)


class SequenceScenario(Strict):
    id: str = Field(min_length=1)
    as_of: date
    horizon: date
    schedule: UniversalSchedule
    anchors: dict[str, date] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
    safety_critical: bool = True

    @model_validator(mode="after")
    def coherent(self):
        if self.horizon < self.as_of:
            raise ValueError("sequence horizon precedes its as-of date")
        return self


class SignOff(Strict):
    reviewer_id: UUID
    key_id: str
    reviewed_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(min_length=1)


class GoldRecord(Strict):
    schema_version: Literal["uctsm-quality.v1"] = SCHEMA_VERSION
    id: str
    source_id: str
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: Literal["ENGINEERING", "CLINICAL"]
    fields: list[ExpectedField] = Field(min_length=1)
    scenarios: list[Scenario] = Field(min_length=1)
    sequences: list[SequenceScenario] = Field(default_factory=list)
    identity: ExpectedIdentity | None = None
    evidence: list[ExpectedEvidence] = Field(default_factory=list)
    mappings: list[ExpectedMapping] = Field(default_factory=list)
    signoff: SignOff | None = None

    def content_hash(self) -> str:
        return digest(self.model_dump(mode="json", exclude={"signoff"}))

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [(row.rule_id, row.path) for row in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate expected field key")
        if len({row.id for row in [*self.scenarios, *self.sequences]}) != len(self.scenarios) + len(self.sequences):
            raise ValueError("duplicate scenario id")
        return self


class Corpus(Strict):
    schema_version: Literal["uctsm-quality.v1"] = SCHEMA_VERSION
    dataset_version: str
    sources: list[Source] = Field(default_factory=list)
    records: list[GoldRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        source_ids = [row.id for row in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate source identity")
        if len({row.id for row in self.records}) != len(self.records):
            raise ValueError("duplicate gold identity")
        for row in self.records:
            if row.source_id not in source_ids:
                raise ValueError(f"unknown source for gold record {row.id}")
            source = next(source for source in self.sources if source.id == row.source_id)
            if row.source_manifest_hash != digest(source.model_dump(mode="json")):
                raise ValueError(f"gold source/version/data-control manifest changed: {row.id}")
        return self


class Thresholds(Strict):
    schema_version: Literal["uctsm-quality.v1"] = SCHEMA_VERSION
    min_precision: float = Field(default=1, ge=0, le=1)
    min_recall: float = Field(default=1, ge=0, le=1)
    required_constructs: set[str] = Field(default_factory=lambda: set(REQUIRED_CONSTRUCTS))
    approval: SignOff | None = None

    @field_serializer("required_constructs")
    def _ordered_required(self, value: set[str]) -> list[str]:
        return sorted(value)

    def content_hash(self):
        return digest(self.model_dump(mode="json", exclude={"approval"}))


class TrustedReviewer(Strict):
    reviewer_id: UUID
    public_key: str


class Predictions(Strict):
    schema_version: Literal["uctsm-quality.v1"] = SCHEMA_VERSION
    versions: dict[str, str] = Field(min_length=1)
    fields: dict[str, list[ExpectedField]] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    identity: dict[str, ExpectedIdentity] = Field(default_factory=dict)
    evidence: dict[str, list[ExpectedEvidence]] = Field(default_factory=dict)
    mappings: dict[str, list[ExpectedMapping]] = Field(default_factory=dict)
