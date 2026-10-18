"""Repository and unit-of-work ports (P14-05).

Python ``Protocol`` interfaces. There is ONE production implementation
(``app.persistence.mongodb``); ``app.persistence.memory`` holds fakes for fast
unit tests, exercised by the same contract suite.

Rules every implementation follows:
* identities are application-generated UUIDs, never database object ids;
* a method that appends history has no update or delete counterpart;
* conditional writes (status guards, revisions) raise ``StaleRevisionError`` /
  ``ImmutableRecordError`` instead of silently doing nothing;
* repositories never commit - the unit of work does.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Protocol, TypeVar
from uuid import UUID

from app.domain.schedule.models import Event, UniversalSchedule, ValidationIssue

from .amendments import (
    AdoptionDecisionRecord,
    AmendmentMappingSetRecord,
    AmendmentPairRecord,
    AmendmentSimulationRecord,
    MappingDecisionRecord,
)
from .authoring import (
    ExtractionRunRecord,
    ProtocolRecord,
    ProtocolVersionRecord,
    ReviewDecisionRecord,
    ScheduleDefinitionRecord,
    ScheduleVersionHeader,
    TrialRecord,
)
from .patients import (
    ActivityEntryRecord,
    AssignmentRecord,
    ConfinementEpisodeRecord,
    EvaluationRecord,
    ImpactProposalRecord,
    ParityRunRecord,
    OccurrenceRecord,
    PatientAnchorRecord,
    PatientConditionRecord,
    PatientEventRecord,
    PatientRecord,
    PatientScheduleRecord,
    PatientStateRecord,
    UnscheduledVisitRecord,
)
from .records import AuditRecord, CommandReceiptRecord, OutboxRecord

T = TypeVar("T")


class AuditRepository(Protocol):
    def append(self, record: AuditRecord) -> None:
        """Insert-only. Raises ``DuplicateRecordError`` on a repeated id."""
        ...

    def list_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID, *, limit: int = 500,
    ) -> list[AuditRecord]:
        """Oldest first."""
        ...

    def list_for_organization(
        self, organization_id: UUID, *, action: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[AuditRecord]:
        """Newest first."""
        ...


class AuthoringRepository(Protocol):
    """Trial, protocol, protocol version, schedule definition, extraction run."""

    def add_trial(self, record: TrialRecord) -> None: ...
    def get_trial(self, organization_id: UUID, trial_id: UUID) -> TrialRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def find_trial_by_external_id(self, organization_id: UUID, external_trial_id: str) -> TrialRecord | None: ...
    def find_trial_by_protocol_id(self, organization_id: UUID, protocol_id: str) -> TrialRecord | None: ...
    def set_trial_read_mode(self, organization_id: UUID, trial_id: UUID, mode: str) -> None: ...
    def set_trial_external_id(
        self, organization_id: UUID, trial_id: UUID, external_trial_id: str) -> None: ...

    def add_protocol(self, record: ProtocolRecord) -> None: ...
    def get_protocol(self, protocol_id: UUID) -> ProtocolRecord: ...
    def find_protocol_for_trial(self, trial_id: UUID) -> ProtocolRecord | None: ...
    def set_current_protocol_version(self, protocol_id: UUID, protocol_version_id: UUID) -> None: ...

    def add_protocol_version(self, record: ProtocolVersionRecord) -> None: ...
    def get_protocol_version(self, protocol_version_id: UUID) -> ProtocolVersionRecord: ...
    def find_protocol_version_by_hash(self, protocol_id: UUID, document_hash: str) -> ProtocolVersionRecord | None: ...
    def list_protocol_version_labels(self, protocol_id: UUID) -> list[str]: ...

    def add_definition(self, record: ScheduleDefinitionRecord) -> None: ...
    def set_definition_external_id(self, definition_id: UUID, external_id: str) -> None: ...
    def get_definition(self, definition_id: UUID) -> ScheduleDefinitionRecord: ...
    def find_definition(
        self, protocol_version_id: UUID, name: str, schedule_type: str,
    ) -> ScheduleDefinitionRecord | None: ...

    def add_run(self, record: ExtractionRunRecord) -> None: ...
    def get_run(self, run_id: UUID) -> ExtractionRunRecord: ...
    def find_run_by_idempotency(self, organization_id: UUID, key: str) -> ExtractionRunRecord | None: ...
    def save_run(self, record: ExtractionRunRecord) -> None:
        """Replace the run (its status/trace/errors are the only mutable parts)."""
        ...


class ScheduleVersionRepository(Protocol):
    """One schedule version: header, content, issues, decisions.

    Content writes (``replace_event``, ``replace_validation_issues``) raise
    ``ImmutableRecordError`` once the version is APPROVED or SUPERSEDED.
    """

    def persist_draft(
        self, schedule: UniversalSchedule, *, schedule_definition_id: UUID,
        extraction_run_id: UUID | None = None,
    ) -> None: ...
    def get(self, schedule_version_id: UUID) -> UniversalSchedule: ...
    def get_header(self, schedule_version_id: UUID) -> ScheduleVersionHeader: ...
    def find_by_extraction_run(self, run_id: UUID) -> ScheduleVersionHeader | None: ...
    def max_version_number(self, schedule_definition_id: UUID) -> int: ...
    def list_headers(
        self, *, organization_id: UUID, status: str | None = None,
        trial_id: UUID | None = None, schedule_definition_id: UUID | None = None,
    ) -> list[ScheduleVersionHeader]: ...

    def replace_event(self, schedule_version_id: UUID, event: Event) -> None: ...
    def add_event(self, schedule_version_id: UUID, event: Event) -> None:
        """A reviewer-entered visit on a draft. ``DuplicateRecordError`` on a code clash."""
        ...
    def replace_support(self, schedule_version_id: UUID, schedule: UniversalSchedule) -> None:
        """Replace a draft's anchors / metadata / evidence / claims from ``schedule``.

        Events and validation issues are untouched (they have their own writes).
        Raises ``ImmutableRecordError`` once the version is frozen.
        """
        ...
    def replace_validation_issues(
        self, schedule_version_id: UUID, issues: list[ValidationIssue]) -> None: ...
    def list_issues(self, schedule_version_id: UUID) -> list[ValidationIssue]: ...
    def set_issue_status(
        self, schedule_version_id: UUID, issue_id: UUID, status: str, *,
        resolved_by: UUID | None = None, note: str | None = None) -> None: ...

    def transition(
        self, schedule_version_id: UUID, *, to_status: str, expected: set[str] | None = None,
        rejection_reason: str | None = None,
    ) -> ScheduleVersionHeader:
        """Guarded status change. Raises ``ImmutableRecordError`` when the
        current status is frozen or not in ``expected``."""
        ...
    def mark_approved(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, approved_at: datetime,
        effective_from: date | None, content_hash: str | None = None,
        content_hash_version: str | None = None, header_integrity_hash: str | None = None,
        expected_revision: int | None = None,
    ) -> ScheduleVersionHeader:
        """IN_REVIEW -> APPROVED in one guarded write. The content hash and the header
        integrity hash are stamped in that SAME write; ``expected_revision`` (when given)
        makes the write fail with ``StaleRevisionError`` if the version changed after
        the hashed content was read, so a hash can never describe other content."""
        ...

    def set_lineage(
        self, schedule_version_id: UUID, based_on_schedule_version_id: UUID,
    ) -> ScheduleVersionHeader:
        """Declare which approved version this one amends. Allowed once, before approval:
        the same value again is a no-op; a different value or a frozen version raises
        ``ImmutableRecordError``."""
        ...

    def record_material_edit(
        self, schedule_version_id: UUID, *, editor_id: UUID, at: datetime,
    ) -> ScheduleVersionHeader:
        """Record who materially changed a draft (maker-checker provenance), in
        the same transaction as the edit. Raises ``ImmutableRecordError`` on a
        frozen version."""
        ...

    def add_decision(self, record: ReviewDecisionRecord) -> None: ...
    def list_decisions(
        self, schedule_version_id: UUID, *, reviewer_id: UUID | None = None,
        decisions: set[str] | None = None,
    ) -> list[ReviewDecisionRecord]: ...


class PatientRepository(Protocol):
    def add(self, record: PatientRecord) -> None:
        """Unique per (trial, patient code). Raises ``DuplicateRecordError``."""
        ...
    def guard_inputs(self, organization_id: UUID, patient_id: UUID, expected_input_revision: int) -> None:
        """Write-skew guard: succeeds only while no clinical input of the patient changed since
        ``expected_input_revision`` was read, and joins this transaction to the patient's input
        sequence so a concurrent correction conflicts with it. Raises ``StaleRevisionError``."""
        ...
    def get(self, organization_id: UUID, patient_id: UUID) -> PatientRecord:
        """Raises ``RecordNotFoundError`` (also for another organization's patient)."""
        ...
    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[PatientRecord]: ...
    def list_for_organization(
        self, organization_id: UUID, *, trial_id: UUID | None = None,
    ) -> list[PatientRecord]: ...
    def find_by_external_id(
        self, organization_id: UUID, trial_id: UUID, external_patient_id: str,
    ) -> PatientRecord | None: ...
    def list_page_for_trial(
        self, organization_id: UUID, trial_id: UUID, *, after_code: str | None, limit: int,
    ) -> list[PatientRecord]:
        """One stable page, ordered by (patient_code, id), strictly after ``after_code``.
        Callers ask for ``limit + 1`` to know whether a next page exists."""
        ...
    def find_owner_by_external_id(
        self, trial_id: UUID, external_patient_id: str,
    ) -> PatientRecord | None:
        """The persisted canonical patient of ONE canonical trial by its operational
        id, whichever organization owns it. Scoped by the trial id (globally
        unique), never a free search; callers still verify the relationship."""
        ...
    def count_by_schedule_version(
        self, organization_id: UUID, trial_id: UUID) -> dict[UUID, int]:
        """Patients per pinned schedule version, for one trial."""
        ...
    def pin_schedule_version(
        self, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
    ) -> PatientRecord: ...
    def save(self, record: PatientRecord) -> PatientRecord:
        """Replace the patient, guarded by its revision. ``StaleRevisionError``
        when someone else changed it first; returns the stored record."""
        ...


class PatientFactsRepository(Protocol):
    """Append-only facts about one patient. No update or delete exists, except
    that a correction marks the fact it replaces as superseded."""

    def add_anchor(self, record: PatientAnchorRecord) -> None: ...
    def get_anchor(self, anchor_id: UUID) -> PatientAnchorRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def confirm_anchor(self, anchor_id: UUID, metadata: dict) -> PatientAnchorRecord:
        """PENDING_CONFIRMATION -> CONFIRMED, once. ``StaleRevisionError`` otherwise."""
        ...
    def list_anchors(
        self, patient_id: UUID, *, statuses: set[str] | None = None) -> list[PatientAnchorRecord]:
        """Oldest recorded first."""
        ...
    def append_state(self, record: PatientStateRecord) -> None: ...
    def list_states(self, patient_id: UUID) -> list[PatientStateRecord]:
        """By effective time, then creation time."""
        ...
    def add_condition(self, record: PatientConditionRecord) -> None: ...
    def list_current_conditions(self, patient_id: UUID) -> list[PatientConditionRecord]:
        """Not superseded, oldest recorded first."""
        ...
    def supersede_condition(self, condition_id: UUID, superseded_by_id: UUID) -> None: ...
    def add_activity_entry(self, record: ActivityEntryRecord) -> None: ...
    def list_current_activity_entries(self, patient_id: UUID) -> list[ActivityEntryRecord]: ...
    def supersede_activity_entries(
        self, patient_id: UUID, logical_key: str, superseded_by_id: UUID,
    ) -> list[ActivityEntryRecord]:
        """Mark every current entry of ``logical_key`` (except the new one) as
        superseded; return what was superseded, oldest first."""
        ...
    def add_unscheduled_visit(self, record: UnscheduledVisitRecord) -> None: ...
    def list_unscheduled_visits(self, patient_id: UUID) -> list[UnscheduledVisitRecord]: ...
    def add_occurrence(self, record: OccurrenceRecord) -> None: ...
    def list_occurrences(
        self, patient_id: UUID, *, schedule_version_id: UUID | None = None,
    ) -> list[OccurrenceRecord]:
        """Oldest recorded first."""
        ...


class EvaluationRepository(Protocol):
    def find_by_idempotency(self, patient_id: UUID, key: str) -> EvaluationRecord | None: ...
    def get_evaluation(self, evaluation_id: UUID) -> EvaluationRecord: ...
    def get_pointer(self, patient_id: UUID, schedule_version_id: UUID) -> PatientScheduleRecord | None: ...
    def count_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> int: ...
    def save_evaluation(
        self, evaluation: EvaluationRecord, events: list[PatientEventRecord],
        confinements: list[ConfinementEpisodeRecord], *, expected_pointer_revision: int | None,
    ) -> PatientScheduleRecord:
        """Insert the evaluation and its projections and advance the pointer in
        one step. ``expected_pointer_revision`` None means "no pointer yet".
        Raises ``StaleRevisionError`` when the pointer moved underneath."""
        ...
    def list_events(self, evaluation_id: UUID) -> list[PatientEventRecord]: ...
    def list_confinements(self, evaluation_id: UUID) -> list[ConfinementEpisodeRecord]: ...
    def supersede_active_pointers(self, patient_id: UUID) -> int:
        """Mark every ACTIVE patient schedule SUPERSEDED (a version change)."""
        ...
    def list_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> list[EvaluationRecord]:
        """Oldest first - the full, never-rewritten history."""
        ...


class ImpactRepository(Protocol):
    def add(self, record: ImpactProposalRecord) -> None: ...
    def get(self, proposal_id: UUID) -> ImpactProposalRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def save(self, record: ImpactProposalRecord) -> None: ...
    def add_assignment(self, record: AssignmentRecord) -> None: ...
    def list_assignments(self, patient_id: UUID) -> list[AssignmentRecord]: ...
    def list_for_patient(
        self, patient_id: UUID, *, status: str | None = None,
    ) -> list[ImpactProposalRecord]: ...


class ParityRepository(Protocol):
    def add(self, record: ParityRunRecord) -> None: ...
    def get(self, organization_id: UUID, run_id: UUID) -> ParityRunRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def save(self, record: ParityRunRecord) -> None: ...
    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[ParityRunRecord]:
        """Oldest first."""
        ...


class PatientCommandRepository(Protocol):
    """The append-only ledger of typed patient commands (P5-03).

    A command is written once; nothing here updates or deletes one. Lifecycle
    facts are further appended rows (a REVERSE command, a proposal state)."""

    def add(self, record: "PatientCommandRecord") -> None:
        """Unique per (patient, sequence) and per (organization, patient, idempotency
        key). Raises ``DuplicateRecordError``."""
        ...
    def get(self, organization_id: UUID, command_id: UUID) -> "PatientCommandRecord":
        """Raises ``RecordNotFoundError``."""
        ...
    def list_for_patient(
        self, organization_id: UUID, patient_id: UUID,
    ) -> "list[PatientCommandRecord]":
        """Oldest first (by sequence)."""
        ...
    def find_by_idempotency(
        self, organization_id: UUID, patient_id: UUID, idempotency_key: str,
    ) -> "PatientCommandRecord | None": ...


class CommandRepository(Protocol):
    def find_receipt(
        self, organization_id: UUID, patient_id: UUID, idempotency_key: str,
    ) -> CommandReceiptRecord | None: ...
    def add_receipt(self, record: CommandReceiptRecord) -> None:
        """Unique per (organization, patient, key). ``DuplicateRecordError``."""
        ...


class OutboxRepository(Protocol):
    def add(self, record: OutboxRecord) -> None:
        """Unique per ``dedupe_key``. ``DuplicateRecordError``."""
        ...
    def list_pending(self, *, limit: int = 100) -> list[OutboxRecord]: ...


class AmendmentRepository(Protocol):
    """Pairs, mapping sets, review decisions, simulations and adoption decisions (P6).

    Every read is scoped by organization. Pairs, mapping decisions and simulations'
    material are append-only; a mapping set and a decision change only through a
    guarded revision (``StaleRevisionError`` on a lost race)."""

    def add_pair(self, record: AmendmentPairRecord) -> None:
        """Unique per (organization, from, to). ``DuplicateRecordError``."""
        ...
    def get_pair(self, organization_id: UUID, pair_id: UUID) -> AmendmentPairRecord:
        """Raises ``RecordNotFoundError`` (also for another organization's pair)."""
        ...
    def find_pair(
        self, organization_id: UUID, from_schedule_version_id: UUID, to_schedule_version_id: UUID,
    ) -> AmendmentPairRecord | None: ...
    def list_pairs_for_trial(
        self, organization_id: UUID, trial_id: UUID) -> list[AmendmentPairRecord]: ...

    def add_mapping_set(self, record: AmendmentMappingSetRecord) -> None:
        """Unique per pair. ``DuplicateRecordError``."""
        ...
    def get_mapping_set(self, organization_id: UUID, pair_id: UUID) -> AmendmentMappingSetRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def save_mapping_set(
        self, record: AmendmentMappingSetRecord, *, expected_revision: int,
    ) -> AmendmentMappingSetRecord:
        """Replace the rows, guarded by revision. ``StaleRevisionError`` otherwise."""
        ...
    def add_mapping_decision(self, record: MappingDecisionRecord) -> None: ...
    def list_mapping_decisions(
        self, organization_id: UUID, pair_id: UUID) -> list[MappingDecisionRecord]:
        """Oldest first."""
        ...

    def add_simulation(self, record: AmendmentSimulationRecord) -> None: ...
    def get_simulation(
        self, organization_id: UUID, simulation_id: UUID) -> AmendmentSimulationRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def set_simulation_status(
        self, organization_id: UUID, simulation_id: UUID, to_status: str, *,
        expected: set[str], consumed_by_decision_id: UUID | None = None,
    ) -> AmendmentSimulationRecord:
        """Guarded status change (ACTIVE -> CONSUMED / EXPIRED / SUPERSEDED).
        ``StaleRevisionError`` when the current status is not in ``expected``."""
        ...
    def list_simulations_for_patient(
        self, organization_id: UUID, patient_id: UUID) -> list[AmendmentSimulationRecord]: ...

    def add_decision(self, record: AdoptionDecisionRecord) -> None:
        """Unique per (organization, patient, idempotency key) and per OPEN patient slot.
        ``DuplicateRecordError``."""
        ...
    def get_decision(self, organization_id: UUID, decision_id: UUID) -> AdoptionDecisionRecord:
        """Raises ``RecordNotFoundError``."""
        ...
    def save_decision(
        self, record: AdoptionDecisionRecord, *, expected_revision: int,
    ) -> AdoptionDecisionRecord:
        """Replace, guarded by revision (bumped on write). ``StaleRevisionError``."""
        ...
    def find_decision_by_idempotency(
        self, organization_id: UUID, patient_id: UUID, idempotency_key: str,
    ) -> AdoptionDecisionRecord | None: ...
    def find_open_decision(
        self, organization_id: UUID, patient_id: UUID) -> AdoptionDecisionRecord | None: ...
    def list_decisions_for_patient(
        self, organization_id: UUID, patient_id: UUID) -> list[AdoptionDecisionRecord]:
        """Oldest first."""
        ...
    def list_open_decisions(
        self, organization_id: UUID, *, trial_id: UUID | None = None, limit: int = 100,
    ) -> list[AdoptionDecisionRecord]: ...
    def list_pending_execution(
        self, *, limit: int = 100, now: datetime | None = None,
    ) -> list[AdoptionDecisionRecord]:
        """TRUSTED SYSTEM USE ONLY (the deferred-execution worker): open decisions of every
        organization that are waiting, or executing under a lease that has EXPIRED (a held
        lease is never returned). Least recently checked first (never-checked first, then
        oldest created), bounded by ``limit`` in the query itself. Never reachable from a
        request."""
        ...


class UnitOfWork(Protocol):
    """One atomic business operation.

    ``run`` executes ``fn`` inside a transaction and commits when it returns.
    ``fn`` MUST be deterministic and free of external side effects: a transient
    transaction error re-runs it from the start.
    """

    audit: AuditRepository
    authoring: AuthoringRepository
    schedules: ScheduleVersionRepository
    patients: PatientRepository
    facts: PatientFactsRepository
    evaluations: EvaluationRepository
    commands: CommandRepository
    ledger: PatientCommandRepository
    impacts: ImpactRepository
    parity: ParityRepository
    outbox: OutboxRepository
    amendments: AmendmentRepository

    def run(self, fn: "Callable[[UnitOfWork], T]") -> T: ...

    def read(self, fn: "Callable[[UnitOfWork], T]") -> T:
        """Run read-only work outside a transaction."""
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
