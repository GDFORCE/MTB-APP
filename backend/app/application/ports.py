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
    ConfinementEpisodeRecord,
    EvaluationRecord,
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
    def set_trial_read_mode(self, organization_id: UUID, trial_id: UUID, mode: str) -> None: ...

    def add_protocol(self, record: ProtocolRecord) -> None: ...
    def get_protocol(self, protocol_id: UUID) -> ProtocolRecord: ...
    def find_protocol_for_trial(self, trial_id: UUID) -> ProtocolRecord | None: ...
    def set_current_protocol_version(self, protocol_id: UUID, protocol_version_id: UUID) -> None: ...

    def add_protocol_version(self, record: ProtocolVersionRecord) -> None: ...
    def get_protocol_version(self, protocol_version_id: UUID) -> ProtocolVersionRecord: ...
    def find_protocol_version_by_hash(self, protocol_id: UUID, document_hash: str) -> ProtocolVersionRecord | None: ...
    def list_protocol_version_labels(self, protocol_id: UUID) -> list[str]: ...

    def add_definition(self, record: ScheduleDefinitionRecord) -> None: ...
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
        effective_from: date | None,
    ) -> ScheduleVersionHeader:
        """IN_REVIEW -> APPROVED in one guarded write."""
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
    def get(self, organization_id: UUID, patient_id: UUID) -> PatientRecord:
        """Raises ``RecordNotFoundError`` (also for another organization's patient)."""
        ...
    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[PatientRecord]: ...
    def pin_schedule_version(
        self, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
    ) -> PatientRecord: ...


class PatientFactsRepository(Protocol):
    """Append-only facts about one patient. No update or delete exists, except
    that a correction marks the fact it replaces as superseded."""

    def add_anchor(self, record: PatientAnchorRecord) -> None: ...
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
    def list_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> list[EvaluationRecord]:
        """Oldest first - the full, never-rewritten history."""
        ...


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
    outbox: OutboxRepository

    def run(self, fn: "Callable[[UnitOfWork], T]") -> T: ...

    def read(self, fn: "Callable[[UnitOfWork], T]") -> T:
        """Run read-only work outside a transaction."""
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
