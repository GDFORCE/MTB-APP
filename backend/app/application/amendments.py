"""Records of the amendment plane (P6): pairs, mappings, simulations, decisions.

Plain typed records with application-generated UUIDs; no driver types. Everything
is scoped by organization (and trial / patient where it applies) so a query is scoped
without a join. Nothing here is a schedule version or a patient fact: a pair, a
simulation and a decision are non-clinical PROVENANCE about how a patient moved (or
chose not to move) between two immutable approved versions.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from .records import Record, utc_now

PAIR_SCHEMA = "amendment-pair.v1"
DECISION_SCHEMA = "amendment-decision.v1"


class AmendmentPairRecord(Record):
    """The frozen comparison of two APPROVED versions of one trial.

    Created once per ordered (from, to) pair; the semantic change set is derived from
    the two immutable contents and stored with their hashes so a later reader can prove
    which exact content it describes. Mapping REVIEW state lives beside it, not in it."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    protocol_id: UUID
    from_schedule_version_id: UUID
    to_schedule_version_id: UUID
    from_content_hash: str
    to_content_hash: str
    content_hash_version: str
    from_effective_from: date | None = None
    to_effective_from: date | None = None
    change_set: list[dict[str, Any]] = Field(default_factory=list)
    change_summary: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = PAIR_SCHEMA
    created_by: UUID
    created_at: datetime = Field(default_factory=utc_now)


class AmendmentMappingSetRecord(Record):
    """The CURRENT mapping rows of a pair (bounded by the number of events). Changed
    only through a guarded revision bump; every change also appends a decision."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    pair_id: UUID
    revision: int = 1
    mappings: list[dict[str, Any]] = Field(default_factory=list)
    #: ``mapping_revision`` of the effective rows (the fingerprint simulations bind to)
    mapping_revision: str
    engine_version: str
    updated_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)


class MappingDecisionKind(StrEnum):
    PROPOSED = "PROPOSED"          # the server's own baseline (no human involved)
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    REPLACE = "REPLACE"            # a reviewer's replacement mapping row
    SET_RULES = "SET_RULES"        # split/merge occurrence rules


class MappingDecisionRecord(Record):
    """Append-only, authenticated review decision about one mapping row."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    pair_id: UUID
    mapping_id: UUID | None = None
    kind: MappingDecisionKind
    reviewer_id: UUID | None = None
    reason: str | None = None
    base_revision: int
    resulting_revision: int
    resulting_mapping_revision: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SimulationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class AmendmentSimulationRecord(Record):
    """A frozen read-only simulation of moving ONE patient from V1 to V2.

    It changes no assignment, fact, projection or outbox row. ``bindings`` are the
    material inputs it was computed from; confirmation recomputes them and refuses on
    any difference (``binding_hash`` is their canonical digest)."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    patient_id: UUID
    pair_id: UUID
    from_schedule_version_id: UUID
    to_schedule_version_id: UUID
    adoption_point: dict[str, Any]
    as_of: date
    horizon: date
    bindings: dict[str, Any]
    binding_hash: str
    result: dict[str, Any] = Field(default_factory=dict)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    status: SimulationStatus = SimulationStatus.ACTIVE
    consumed_by_decision_id: UUID | None = None
    created_by: UUID
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class DecisionType(StrEnum):
    ADOPT = "ADOPT"
    REMAIN = "REMAIN"
    RETURN = "RETURN"


class DecisionStatus(StrEnum):
    #: deferred: waiting for its stated event / cycle start / date
    WAITING = "WAITING"
    #: a worker holds the fenced lease and is applying it
    EXECUTING = "EXECUTING"
    #: applied (ADOPT / RETURN committed)
    APPLIED = "APPLIED"
    #: the patient was deliberately left on the earlier version (REMAIN)
    REMAINING = "REMAINING"
    #: due, but the approved impact no longer applies: a person must confirm again
    NEEDS_RECONFIRMATION = "NEEDS_RECONFIRMATION"
    #: due, but a precondition fails (version/mapping/authority); nothing was applied
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


#: statuses that still hold the patient's single open-decision slot
OPEN_STATUSES = frozenset({
    DecisionStatus.WAITING, DecisionStatus.EXECUTING, DecisionStatus.NEEDS_RECONFIRMATION})


class FuturePolicy(StrEnum):
    #: a deferred adoption proceeds when the recomputed impact has the same STRUCTURE
    #: (which visits are added / removed / protected / blocked); dates of visits that
    #: were still in the future when it was approved may differ.
    SAME_STRUCTURE = "SAME_STRUCTURE"
    #: any change to the impact needs a new confirmation
    SAME_IMPACT = "SAME_IMPACT"


class AdoptionDecisionRecord(Record):
    """One authenticated decision about a patient's move: ADOPT, REMAIN or RETURN.

    Exactly one decision per patient may be OPEN at a time (``is_open`` carries a
    store-enforced unique slot), which is what stops an immediate adoption, a deferred
    one and a return from double-assigning."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    patient_id: UUID
    pair_id: UUID
    from_schedule_version_id: UUID
    to_schedule_version_id: UUID
    decision_type: DecisionType
    mode: str
    adoption_point: dict[str, Any] = Field(default_factory=dict)
    status: DecisionStatus
    is_open: bool = False
    simulation_id: UUID | None = None
    bindings: dict[str, Any] = Field(default_factory=dict)
    binding_hash: str = ""
    #: digest of the MATERIAL operation (type, versions, point, policy, reason-free)
    material_hash: str
    future_policy: FuturePolicy = FuturePolicy.SAME_STRUCTURE
    impact_signature: dict[str, str] = Field(default_factory=dict)
    reason: str
    actor_id: UUID
    #: capability names the actor needed and held when this was recorded (audit)
    capabilities: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    effective_date: date | None = None
    trigger_state: dict[str, Any] = Field(default_factory=dict)
    linked_decision_id: UUID | None = None
    result_evaluation_id: UUID | None = None
    result_assignment_id: UUID | None = None
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    carried_fact_count: int = 0
    #: fenced, retryable execution of a deferred decision
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    #: when the deferred-execution worker last looked at this decision (due or not). The
    #: worker visits the least recently checked first, so an old not-yet-due decision can
    #: never keep a due one out of a bounded pass. Real time, not the business clock.
    last_checked_at: datetime | None = None
    revision: int = 1
    schema_version: str = DECISION_SCHEMA
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
