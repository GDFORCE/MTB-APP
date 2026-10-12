from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from typing import Any, Callable, TypeVar
from uuid import UUID

from app.application import schedule_documents as parts
from app.application.authoring import (
    FROZEN_STATUSES,
    ExtractionRunRecord,
    ProtocolRecord,
    ProtocolVersionRecord,
    ReviewDecisionRecord,
    ScheduleDefinitionRecord,
    ScheduleVersionHeader,
    TrialRecord,
)
from app.application.errors import (
    DuplicateRecordError,
    ImmutableRecordError,
    RecordNotFoundError,
    StaleRevisionError,
)
from app.application.patients import (
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
from app.application.records import AuditRecord, CommandReceiptRecord, OutboxRecord
from app.domain.schedule.models import Event, UniversalSchedule, ValidationIssue

T = TypeVar("T")


class _Store:
    def __init__(self) -> None:
        self.audit: dict[UUID, AuditRecord] = {}
        self.trials: dict[UUID, TrialRecord] = {}
        self.protocols: dict[UUID, ProtocolRecord] = {}
        self.protocol_versions: dict[UUID, ProtocolVersionRecord] = {}
        self.definitions: dict[UUID, ScheduleDefinitionRecord] = {}
        self.runs: dict[UUID, ExtractionRunRecord] = {}
        # schedule version aggregate, kept as the shared stored shape
        self.versions: dict[UUID, dict[str, Any]] = {}
        self.decisions: list[ReviewDecisionRecord] = []
        # patient plane
        self.patients: dict[UUID, PatientRecord] = {}
        self.anchors: list[PatientAnchorRecord] = []
        self.states: list[PatientStateRecord] = []
        self.conditions: dict[UUID, PatientConditionRecord] = {}
        self.activity_entries: dict[UUID, ActivityEntryRecord] = {}
        self.unscheduled: list[UnscheduledVisitRecord] = []
        self.occurrences: list[OccurrenceRecord] = []
        self.evaluations: dict[UUID, EvaluationRecord] = {}
        self.events: dict[UUID, list[PatientEventRecord]] = {}
        self.confinements: dict[UUID, list[ConfinementEpisodeRecord]] = {}
        self.pointers: dict[tuple[UUID, UUID], PatientScheduleRecord] = {}
        self.receipts: list[CommandReceiptRecord] = []
        self.proposals: dict[UUID, ImpactProposalRecord] = {}
        self.parity_runs: dict[UUID, ParityRunRecord] = {}
        self.assignments: list[AssignmentRecord] = []
        self.outbox: list[OutboxRecord] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryAuditRepository:
    def __init__(self, store: _Store):
        self._store = store

    def append(self, record: AuditRecord) -> None:
        if record.id in self._store.audit:
            raise DuplicateRecordError(f"audit event {record.id} already exists", key=str(record.id))
        self._store.audit[record.id] = record.model_copy(deep=True)

    def list_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID, *, limit: int = 500,
    ) -> list[AuditRecord]:
        rows = [item for item in self._store.audit.values()
                if (item.organization_id, item.entity_type, item.entity_id)
                == (organization_id, entity_type, entity_id)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows[:limit]]

    def list_for_organization(
        self, organization_id: UUID, *, action: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[AuditRecord]:
        rows = [item for item in self._store.audit.values()
                if item.organization_id == organization_id
                and (action is None or item.action == action)
                and (since is None or item.created_at >= since)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return [item.model_copy(deep=True) for item in rows[:limit]]


class InMemoryAuthoringRepository:
    def __init__(self, store: _Store):
        self._s = store

    @staticmethod
    def _copy(value):
        return value.model_copy(deep=True)

    def _unique(self, table: dict, record, what: str) -> None:
        if record.id in table:
            raise DuplicateRecordError(f"{what} already exists", key=str(record.id))

    # trials
    def add_trial(self, record: TrialRecord) -> None:
        self._unique(self._s.trials, record, "trial")
        if record.external_trial_id is not None and any(
                item.organization_id == record.organization_id
                and item.external_trial_id == record.external_trial_id
                for item in self._s.trials.values()):
            raise DuplicateRecordError("trial already exists", key=record.external_trial_id)
        self._s.trials[record.id] = self._copy(record)

    def get_trial(self, organization_id: UUID, trial_id: UUID) -> TrialRecord:
        row = self._s.trials.get(trial_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("trial not found")
        return self._copy(row)

    def find_trial_by_external_id(self, organization_id: UUID, external_trial_id: str) -> TrialRecord | None:
        for row in self._s.trials.values():
            if row.organization_id == organization_id and row.external_trial_id == external_trial_id:
                return self._copy(row)
        return None

    def set_trial_read_mode(self, organization_id: UUID, trial_id: UUID, mode: str) -> None:
        row = self._s.trials.get(trial_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("trial not found")
        row.schedule_read_mode = mode
        row.updated_at = _now()

    def find_trial_by_protocol_id(self, organization_id: UUID, protocol_id: str):
        for item in self._s.trials.values():
            if item.organization_id == organization_id and item.protocol_id == protocol_id:
                return item.model_copy(deep=True)
        return None

    def set_trial_external_id(self, organization_id: UUID, trial_id: UUID, external_trial_id: str) -> None:
        row = self._s.trials.get(trial_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("trial not found")
        if any(item.id != trial_id and item.organization_id == organization_id
               and item.external_trial_id == external_trial_id for item in self._s.trials.values()):
            raise DuplicateRecordError(
                "another trial already uses this external trial id", key=external_trial_id)
        row.external_trial_id = external_trial_id
        row.updated_at = _now()

    # protocols
    def add_protocol(self, record: ProtocolRecord) -> None:
        self._unique(self._s.protocols, record, "protocol")
        if any(item.trial_id == record.trial_id and item.protocol_number == record.protocol_number
               for item in self._s.protocols.values()):
            raise DuplicateRecordError("protocol already exists", key=record.protocol_number)
        self._s.protocols[record.id] = self._copy(record)

    def get_protocol(self, protocol_id: UUID) -> ProtocolRecord:
        row = self._s.protocols.get(protocol_id)
        if row is None:
            raise RecordNotFoundError("protocol not found")
        return self._copy(row)

    def find_protocol_for_trial(self, trial_id: UUID) -> ProtocolRecord | None:
        for row in self._s.protocols.values():
            if row.trial_id == trial_id:
                return self._copy(row)
        return None

    def set_current_protocol_version(self, protocol_id: UUID, protocol_version_id: UUID) -> None:
        row = self._s.protocols.get(protocol_id)
        if row is None:
            raise RecordNotFoundError("protocol not found")
        row.current_version_id = protocol_version_id
        row.updated_at = _now()

    # protocol versions
    def add_protocol_version(self, record: ProtocolVersionRecord) -> None:
        self._unique(self._s.protocol_versions, record, "protocol version")
        if any(item.protocol_id == record.protocol_id and item.version_label == record.version_label
               for item in self._s.protocol_versions.values()):
            raise DuplicateRecordError("protocol version label already exists", key=record.version_label)
        self._s.protocol_versions[record.id] = self._copy(record)

    def get_protocol_version(self, protocol_version_id: UUID) -> ProtocolVersionRecord:
        row = self._s.protocol_versions.get(protocol_version_id)
        if row is None:
            raise RecordNotFoundError("protocol version not found")
        return self._copy(row)

    def find_protocol_version_by_hash(
        self, protocol_id: UUID, document_hash: str,
    ) -> ProtocolVersionRecord | None:
        for row in self._s.protocol_versions.values():
            if row.protocol_id == protocol_id and row.document_hash == document_hash:
                return self._copy(row)
        return None

    def list_protocol_version_labels(self, protocol_id: UUID) -> list[str]:
        return [row.version_label for row in self._s.protocol_versions.values()
                if row.protocol_id == protocol_id]

    # definitions
    def add_definition(self, record: ScheduleDefinitionRecord) -> None:
        self._unique(self._s.definitions, record, "schedule definition")
        if any((item.protocol_version_id, item.name, item.schedule_type)
               == (record.protocol_version_id, record.name, record.schedule_type)
               for item in self._s.definitions.values()):
            raise DuplicateRecordError("schedule definition already exists", key=record.name)
        self._s.definitions[record.id] = self._copy(record)

    def set_definition_external_id(self, definition_id: UUID, external_id: str) -> None:
        row = self._s.definitions.get(definition_id)
        if row is None:
            raise RecordNotFoundError("schedule definition not found")
        row.external_schedule_definition_id = external_id
        row.updated_at = _now()

    def get_definition(self, definition_id: UUID) -> ScheduleDefinitionRecord:
        row = self._s.definitions.get(definition_id)
        if row is None:
            raise RecordNotFoundError("schedule definition not found")
        return self._copy(row)

    def find_definition(
        self, protocol_version_id: UUID, name: str, schedule_type: str,
    ) -> ScheduleDefinitionRecord | None:
        for row in self._s.definitions.values():
            if (row.protocol_version_id, row.name, row.schedule_type) == (
                    protocol_version_id, name, schedule_type):
                return self._copy(row)
        return None

    # runs
    def add_run(self, record: ExtractionRunRecord) -> None:
        self._unique(self._s.runs, record, "extraction run")
        if record.idempotency_key is not None and any(
                item.organization_id == record.organization_id
                and item.idempotency_key == record.idempotency_key
                for item in self._s.runs.values()):
            raise DuplicateRecordError("extraction run already exists", key=record.idempotency_key)
        self._s.runs[record.id] = self._copy(record)

    def get_run(self, run_id: UUID) -> ExtractionRunRecord:
        row = self._s.runs.get(run_id)
        if row is None:
            raise RecordNotFoundError("extraction run not found")
        return self._copy(row)

    def find_run_by_idempotency(self, organization_id: UUID, key: str) -> ExtractionRunRecord | None:
        for row in self._s.runs.values():
            if row.organization_id == organization_id and row.idempotency_key == key:
                return self._copy(row)
        return None

    def save_run(self, record: ExtractionRunRecord) -> None:
        if record.id not in self._s.runs:
            raise RecordNotFoundError("extraction run not found")
        stored = self._copy(record)
        stored.updated_at = _now()
        self._s.runs[record.id] = stored


class InMemoryScheduleVersionRepository:
    def __init__(self, store: _Store):
        self._s = store

    def _version(self, schedule_version_id: UUID) -> dict[str, Any]:
        row = self._s.versions.get(schedule_version_id)
        if row is None:
            raise RecordNotFoundError("schedule version not found")
        return row

    def _editable(self, schedule_version_id: UUID) -> dict[str, Any]:
        row = self._version(schedule_version_id)
        if row["header"].status in FROZEN_STATUSES:
            raise ImmutableRecordError("approved schedule versions cannot be edited")
        return row

    def get_header(self, schedule_version_id: UUID) -> ScheduleVersionHeader:
        return self._version(schedule_version_id)["header"].model_copy(deep=True)

    def get(self, schedule_version_id: UUID) -> UniversalSchedule:
        row = self._version(schedule_version_id)
        header: ScheduleVersionHeader = row["header"]
        definition = self._s.definitions.get(header.schedule_definition_id)
        if definition is None:
            raise RecordNotFoundError("schedule definition not found")
        return parts.assemble(
            header=header, definition=definition, content=copy.deepcopy(row["content"]),
            events=copy.deepcopy(list(row["events"].values())),
            evidence=copy.deepcopy(row["evidence"]), claims=copy.deepcopy(row["claims"]),
            issues=copy.deepcopy(row["issues"]))

    def find_by_extraction_run(self, run_id: UUID) -> ScheduleVersionHeader | None:
        for row in self._s.versions.values():
            if row["header"].extraction_run_id == run_id:
                return row["header"].model_copy(deep=True)
        return None

    def max_version_number(self, schedule_definition_id: UUID) -> int:
        numbers = [row["header"].version_number for row in self._s.versions.values()
                   if row["header"].schedule_definition_id == schedule_definition_id]
        return max(numbers, default=0)

    def list_headers(
        self, *, organization_id: UUID, status: str | None = None,
        trial_id: UUID | None = None, schedule_definition_id: UUID | None = None,
    ) -> list[ScheduleVersionHeader]:
        rows = [row["header"] for row in self._s.versions.values()
                if row["header"].organization_id == organization_id
                and (status is None or row["header"].status == status)
                and (trial_id is None or row["header"].trial_id == trial_id)
                and (schedule_definition_id is None
                     or row["header"].schedule_definition_id == schedule_definition_id)]
        rows.sort(key=lambda item: (item.updated_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]

    def persist_draft(
        self, schedule: UniversalSchedule, *, schedule_definition_id: UUID,
        extraction_run_id: UUID | None = None,
    ) -> None:
        definition = self._s.definitions.get(schedule_definition_id)
        if definition is None:
            raise RecordNotFoundError("schedule definition not found")
        if schedule.schedule_version_id in self._s.versions:
            raise ValueError("schedule version already exists")
        if any(row["header"].schedule_definition_id == schedule_definition_id
               and row["header"].version_number == schedule.schedule_metadata.version_number
               for row in self._s.versions.values()):
            raise DuplicateRecordError("version number already exists", key=str(schedule_definition_id))
        built = parts.build_parts(
            schedule, definition=definition, extraction_run_id=extraction_run_id)
        codes = [item["code"] for item in built["events"]]
        if len(codes) != len(set(codes)):
            raise DuplicateRecordError("duplicate event code in schedule version")
        for kind in ("events", "evidence", "claims", "issues"):
            ids = [item["id"] for item in built[kind]]
            if len(ids) != len(set(ids)):
                raise DuplicateRecordError(f"duplicate {kind} id in schedule version")
        self._s.versions[schedule.schedule_version_id] = copy.deepcopy({
            "header": built["header"], "content": built["content"],
            "events": {item["id"]: item for item in built["events"]},
            "evidence": built["evidence"], "claims": built["claims"],
            "issues": [dict(item) for item in built["issues"]],
        })

    def replace_event(self, schedule_version_id: UUID, event: Event) -> None:
        row = self._editable(schedule_version_id)
        stored = row["events"].get(event.id)
        if stored is None:
            raise RecordNotFoundError("event definition not found")
        codes = {item["code"] for item in row["events"].values()} | {event.code}
        if any(item["code"] == event.code and key != event.id for key, item in row["events"].items()):
            raise DuplicateRecordError(
                f"event code {event.code!r} already exists in this schedule version", key=event.code)
        document = parts._event_document(event, valid_codes=codes)
        old_code = stored["code"]
        row["events"][event.id] = copy.deepcopy(document)
        if old_code != event.code:
            for key, other in row["events"].items():
                if key != event.id:
                    parts.rename_dependency_source(other["event"], old_code, event.code)

    def replace_validation_issues(
        self, schedule_version_id: UUID, issues: list[ValidationIssue],
    ) -> None:
        row = self._editable(schedule_version_id)
        row["issues"] = [
            item for item in row["issues"]
            if not (item["validator_version"] == parts.VALIDATOR_VERSION and item["status"] == "OPEN")]
        existing = {item["id"] for item in row["issues"]}
        for issue in issues:
            if issue.id in existing:
                raise DuplicateRecordError(f"duplicate validation issue id {issue.id}")
            row["issues"].append(copy.deepcopy(parts.issue_document(issue)))

    def list_issues(self, schedule_version_id: UUID) -> list[ValidationIssue]:
        row = self._version(schedule_version_id)
        return [ValidationIssue.model_validate(item["issue"]) for item in row["issues"]]

    def set_issue_status(
        self, schedule_version_id: UUID, issue_id: UUID, status: str, *,
        resolved_by: UUID | None = None, note: str | None = None,
    ) -> None:
        row = self._editable(schedule_version_id)
        for item in row["issues"]:
            if item["id"] == issue_id:
                item["status"] = status
                item["issue"]["status"] = status
                item["resolved_by"] = resolved_by
                item["resolution_note"] = note
                return
        raise RecordNotFoundError("validation issue not found")

    def _guarded(self, schedule_version_id: UUID, allowed, update: dict[str, Any], refusal: str):
        row = self._version(schedule_version_id)
        header: ScheduleVersionHeader = row["header"]
        if not allowed(header.status):
            raise ImmutableRecordError(f"{refusal} (current status {header.status})")
        row["header"] = header.model_copy(update={
            **update, "updated_at": _now(), "revision": header.revision + 1})
        return row["header"].model_copy(deep=True)

    def transition(
        self, schedule_version_id: UUID, *, to_status: str, expected: set[str] | None = None,
        rejection_reason: str | None = None,
    ) -> ScheduleVersionHeader:
        allowed = (lambda status: status in expected) if expected is not None \
            else (lambda status: status not in FROZEN_STATUSES)
        update: dict[str, Any] = {"status": to_status}
        if to_status == "REJECTED":
            update["rejection_reason"] = rejection_reason
        return self._guarded(
            schedule_version_id, allowed, update,
            f"cannot move the schedule version to {to_status}")

    def mark_approved(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, approved_at: datetime,
        effective_from: date | None,
    ) -> ScheduleVersionHeader:
        return self._guarded(
            schedule_version_id, lambda status: status == "IN_REVIEW",
            {"status": "APPROVED", "approved_by": reviewer_id, "approved_at": approved_at,
             "effective_from": effective_from},
            "only an in-review schedule can be approved")

    def add_decision(self, record: ReviewDecisionRecord) -> None:
        self._version(record.schedule_version_id)
        if any(item.id == record.id for item in self._s.decisions):
            raise DuplicateRecordError(f"review decision {record.id} already exists")
        self._s.decisions.append(record.model_copy(deep=True))

    def list_decisions(
        self, schedule_version_id: UUID, *, reviewer_id: UUID | None = None,
        decisions: set[str] | None = None,
    ) -> list[ReviewDecisionRecord]:
        rows = [item for item in self._s.decisions
                if item.schedule_version_id == schedule_version_id
                and (reviewer_id is None or item.reviewer_id == reviewer_id)
                and (decisions is None or item.decision in decisions)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]


class InMemoryPatientRepository:
    def __init__(self, store: _Store):
        self._s = store

    def add(self, record: PatientRecord) -> None:
        if record.id in self._s.patients or any(
                item.trial_id == record.trial_id and item.patient_code == record.patient_code
                for item in self._s.patients.values()):
            raise DuplicateRecordError("patient already exists", key=record.patient_code)
        self._s.patients[record.id] = record.model_copy(deep=True)

    def get(self, organization_id: UUID, patient_id: UUID) -> PatientRecord:
        row = self._s.patients.get(patient_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("patient not found")
        return row.model_copy(deep=True)

    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[PatientRecord]:
        rows = [item for item in self._s.patients.values()
                if item.organization_id == organization_id and item.trial_id == trial_id]
        rows.sort(key=lambda item: item.patient_code)
        return [item.model_copy(deep=True) for item in rows]

    def find_by_external_id(self, organization_id: UUID, trial_id: UUID, external_patient_id: str):
        for item in self._s.patients.values():
            if (item.organization_id == organization_id and item.trial_id == trial_id
                    and item.external_patient_id == external_patient_id):
                return item.model_copy(deep=True)
        return None

    def list_for_organization(self, organization_id: UUID, *, trial_id: UUID | None = None):
        rows = [item for item in self._s.patients.values()
                if item.organization_id == organization_id
                and (trial_id is None or item.trial_id == trial_id)]
        rows.sort(key=lambda item: (item.patient_code, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]

    def count_by_schedule_version(self, organization_id: UUID, trial_id: UUID) -> dict[UUID, int]:
        counts: dict[UUID, int] = {}
        for row in self._s.patients.values():
            if (row.organization_id == organization_id and row.trial_id == trial_id
                    and row.current_schedule_version_id is not None):
                counts[row.current_schedule_version_id] = counts.get(row.current_schedule_version_id, 0) + 1
        return counts

    def save(self, record: PatientRecord) -> PatientRecord:
        row = self._s.patients.get(record.id)
        if row is None or row.organization_id != record.organization_id or row.revision != record.revision:
            raise StaleRevisionError("the patient changed since it was read")
        stored = record.model_copy(update={"revision": record.revision + 1, "updated_at": _now()}, deep=True)
        self._s.patients[record.id] = stored
        return stored.model_copy(deep=True)

    def pin_schedule_version(
        self, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
    ) -> PatientRecord:
        row = self._s.patients.get(patient_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("patient not found")
        row.current_schedule_version_id = schedule_version_id
        row.updated_at = _now()
        row.revision += 1
        return row.model_copy(deep=True)


class InMemoryPatientFactsRepository:
    def __init__(self, store: _Store):
        self._s = store

    @staticmethod
    def _copies(rows):
        return [item.model_copy(deep=True) for item in rows]

    def add_anchor(self, record: PatientAnchorRecord) -> None:
        self._s.anchors.append(record.model_copy(deep=True))

    def get_anchor(self, anchor_id: UUID) -> PatientAnchorRecord:
        for item in self._s.anchors:
            if item.id == anchor_id:
                return item.model_copy(deep=True)
        raise RecordNotFoundError("patient anchor not found")

    def confirm_anchor(self, anchor_id: UUID, metadata: dict) -> PatientAnchorRecord:
        for item in self._s.anchors:
            if item.id == anchor_id:
                if item.status != "PENDING_CONFIRMATION":
                    raise StaleRevisionError("anchor candidate is no longer pending confirmation")
                item.status = "CONFIRMED"
                item.metadata = dict(metadata)
                return item.model_copy(deep=True)
        raise StaleRevisionError("anchor candidate is no longer pending confirmation")

    def list_anchors(self, patient_id: UUID, *, statuses: set[str] | None = None):
        rows = [item for item in self._s.anchors if item.patient_id == patient_id
                and (statuses is None or item.status in statuses)]
        rows.sort(key=lambda item: (item.recorded_at, str(item.id)))
        return self._copies(rows)

    def append_state(self, record: PatientStateRecord) -> None:
        self._s.states.append(record.model_copy(deep=True))

    def list_states(self, patient_id: UUID):
        rows = [item for item in self._s.states if item.patient_id == patient_id]
        rows.sort(key=lambda item: (item.effective_at, item.created_at, str(item.id)))
        return self._copies(rows)

    def add_condition(self, record: PatientConditionRecord) -> None:
        self._s.conditions[record.id] = record.model_copy(deep=True)

    def list_current_conditions(self, patient_id: UUID):
        rows = [item for item in self._s.conditions.values()
                if item.patient_id == patient_id and item.superseded_by_id is None]
        rows.sort(key=lambda item: (item.recorded_at, str(item.id)))
        return self._copies(rows)

    def supersede_condition(self, condition_id: UUID, superseded_by_id: UUID) -> None:
        row = self._s.conditions.get(condition_id)
        if row is None or row.superseded_by_id is not None:
            raise RecordNotFoundError("current condition record not found")
        row.superseded_by_id = superseded_by_id

    def add_activity_entry(self, record: ActivityEntryRecord) -> None:
        self._s.activity_entries[record.id] = record.model_copy(deep=True)

    def list_current_activity_entries(self, patient_id: UUID):
        rows = [item for item in self._s.activity_entries.values()
                if item.patient_id == patient_id and item.superseded_by_id is None]
        rows.sort(key=lambda item: (item.recorded_at, str(item.id)))
        return self._copies(rows)

    def supersede_activity_entries(self, patient_id: UUID, logical_key: str, superseded_by_id: UUID):
        previous = [item for item in self._s.activity_entries.values()
                    if item.patient_id == patient_id and item.logical_key == logical_key
                    and item.superseded_by_id is None and item.id != superseded_by_id]
        previous.sort(key=lambda item: (item.recorded_at, str(item.id)))
        copies = self._copies(previous)
        for item in previous:
            item.superseded_by_id = superseded_by_id
        return copies

    def add_unscheduled_visit(self, record: UnscheduledVisitRecord) -> None:
        self._s.unscheduled.append(record.model_copy(deep=True))

    def list_unscheduled_visits(self, patient_id: UUID):
        rows = [item for item in self._s.unscheduled if item.patient_id == patient_id]
        rows.sort(key=lambda item: (item.occurred_on, str(item.id)))
        return self._copies(rows)

    def add_occurrence(self, record: OccurrenceRecord) -> None:
        self._s.occurrences.append(record.model_copy(deep=True))

    def list_occurrences(self, patient_id: UUID, *, schedule_version_id: UUID | None = None):
        rows = [item for item in self._s.occurrences if item.patient_id == patient_id
                and (schedule_version_id is None or item.schedule_version_id == schedule_version_id)]
        rows.sort(key=lambda item: (item.recorded_at, str(item.id)))
        return self._copies(rows)


class InMemoryEvaluationRepository:
    def __init__(self, store: _Store):
        self._s = store

    def find_by_idempotency(self, patient_id: UUID, key: str) -> EvaluationRecord | None:
        for row in self._s.evaluations.values():
            if row.patient_id == patient_id and row.idempotency_key == key:
                return row.model_copy(deep=True)
        return None

    def get_evaluation(self, evaluation_id: UUID) -> EvaluationRecord:
        row = self._s.evaluations.get(evaluation_id)
        if row is None:
            raise RecordNotFoundError("evaluation not found")
        return row.model_copy(deep=True)

    def get_pointer(self, patient_id: UUID, schedule_version_id: UUID):
        row = self._s.pointers.get((patient_id, schedule_version_id))
        return row.model_copy(deep=True) if row else None

    def count_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> int:
        return sum(1 for row in self._s.evaluations.values()
                   if row.patient_id == patient_id and row.schedule_version_id == schedule_version_id)

    def save_evaluation(
        self, evaluation: EvaluationRecord, events: list[PatientEventRecord],
        confinements: list[ConfinementEpisodeRecord], *, expected_pointer_revision: int | None,
    ) -> PatientScheduleRecord:
        if evaluation.id in self._s.evaluations:
            raise DuplicateRecordError("evaluation already exists", key=str(evaluation.id))
        for row in self._s.evaluations.values():
            if row.patient_id == evaluation.patient_id and row.idempotency_key is not None \
                    and row.idempotency_key == evaluation.idempotency_key:
                raise DuplicateRecordError("idempotency key already used", key=row.idempotency_key)
            if (row.patient_id, row.schedule_version_id, row.sequence) == (
                    evaluation.patient_id, evaluation.schedule_version_id, evaluation.sequence):
                raise DuplicateRecordError("evaluation sequence already used", key=str(row.sequence))
        seen = set()
        for item in events:
            key = (item.event_code, item.occurrence_index)
            if key in seen:
                raise DuplicateRecordError("duplicate projection in one evaluation", key=str(key))
            seen.add(key)
        pointer_key = (evaluation.patient_id, evaluation.schedule_version_id)
        pointer = self._s.pointers.get(pointer_key)
        if expected_pointer_revision is None:
            if pointer is not None:
                raise StaleRevisionError("the patient schedule was created by a concurrent evaluation")
        elif pointer is None or pointer.revision != expected_pointer_revision:
            raise StaleRevisionError("the patient schedule changed since it was read")
        self._s.evaluations[evaluation.id] = evaluation.model_copy(deep=True)
        self._s.events[evaluation.id] = [item.model_copy(deep=True) for item in events]
        self._s.confinements[evaluation.id] = [item.model_copy(deep=True) for item in confinements]
        if pointer is None:
            pointer = PatientScheduleRecord(
                organization_id=evaluation.organization_id, patient_id=evaluation.patient_id,
                schedule_version_id=evaluation.schedule_version_id)
        pointer.current_evaluation_id = evaluation.id
        pointer.generation_run_id = evaluation.id
        pointer.status = "ACTIVE"
        pointer.updated_at = pointer.generated_at = _now()
        pointer.revision += 1
        self._s.pointers[pointer_key] = pointer
        return pointer.model_copy(deep=True)

    @staticmethod
    def _order(item: PatientEventRecord):
        return (item.nominal_start_date is not None, item.nominal_start_date or date.min,
                item.occurrence_index, item.event_code, str(item.id))

    def list_events(self, evaluation_id: UUID) -> list[PatientEventRecord]:
        rows = sorted(self._s.events.get(evaluation_id, []), key=self._order)
        return [item.model_copy(deep=True) for item in rows]

    def list_confinements(self, evaluation_id: UUID) -> list[ConfinementEpisodeRecord]:
        rows = sorted(self._s.confinements.get(evaluation_id, []),
                      key=lambda item: (item.episode_code, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]

    def supersede_active_pointers(self, patient_id: UUID) -> int:
        changed = 0
        for (owner, _version), pointer in self._s.pointers.items():
            if owner == patient_id and pointer.status == "ACTIVE":
                pointer.status = "SUPERSEDED"
                changed += 1
        return changed

    def list_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> list[EvaluationRecord]:
        rows = [item for item in self._s.evaluations.values()
                if item.patient_id == patient_id and item.schedule_version_id == schedule_version_id]
        rows.sort(key=lambda item: (item.sequence, item.evaluated_at))
        return [item.model_copy(deep=True) for item in rows]


class InMemoryImpactRepository:
    def __init__(self, store: _Store):
        self._s = store

    def add(self, record: ImpactProposalRecord) -> None:
        if record.id in self._s.proposals:
            raise DuplicateRecordError("impact proposal already exists", key=str(record.id))
        self._s.proposals[record.id] = record.model_copy(deep=True)

    def get(self, proposal_id: UUID) -> ImpactProposalRecord:
        row = self._s.proposals.get(proposal_id)
        if row is None:
            raise RecordNotFoundError("impact proposal not found")
        return row.model_copy(deep=True)

    def save(self, record: ImpactProposalRecord) -> None:
        if record.id not in self._s.proposals:
            raise RecordNotFoundError("impact proposal not found")
        self._s.proposals[record.id] = record.model_copy(deep=True)

    def add_assignment(self, record: AssignmentRecord) -> None:
        self._s.assignments.append(record.model_copy(deep=True))

    def list_for_patient(self, patient_id: UUID, *, status: str | None = None):
        rows = [item for item in self._s.proposals.values()
                if item.patient_id == patient_id and (status is None or item.status == status)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]

    def list_assignments(self, patient_id: UUID) -> list[AssignmentRecord]:
        rows = [item for item in self._s.assignments if item.patient_id == patient_id]
        rows.sort(key=lambda item: (item.assigned_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]


class InMemoryParityRepository:
    def __init__(self, store: _Store):
        self._s = store

    def add(self, record: ParityRunRecord) -> None:
        if record.id in self._s.parity_runs:
            raise DuplicateRecordError("parity run already exists", key=str(record.id))
        self._s.parity_runs[record.id] = record.model_copy(deep=True)

    def get(self, organization_id: UUID, run_id: UUID) -> ParityRunRecord:
        row = self._s.parity_runs.get(run_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("parity run not found")
        return row.model_copy(deep=True)

    def save(self, record: ParityRunRecord) -> None:
        if record.id not in self._s.parity_runs:
            raise RecordNotFoundError("parity run not found")
        self._s.parity_runs[record.id] = record.model_copy(deep=True)

    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[ParityRunRecord]:
        rows = [item for item in self._s.parity_runs.values()
                if item.organization_id == organization_id and item.trial_id == trial_id]
        rows.sort(key=lambda item: (item.ran_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows]


class InMemoryCommandRepository:
    def __init__(self, store: _Store):
        self._s = store

    def find_receipt(self, organization_id: UUID, patient_id: UUID, idempotency_key: str):
        for row in self._s.receipts:
            if (row.organization_id, row.patient_id, row.idempotency_key) == (
                    organization_id, patient_id, idempotency_key):
                return row.model_copy(deep=True)
        return None

    def add_receipt(self, record: CommandReceiptRecord) -> None:
        if self.find_receipt(record.organization_id, record.patient_id, record.idempotency_key):
            raise DuplicateRecordError("command receipt already exists", key=record.idempotency_key)
        self._s.receipts.append(record.model_copy(deep=True))


class InMemoryOutboxRepository:
    def __init__(self, store: _Store):
        self._s = store

    def add(self, record: OutboxRecord) -> None:
        if any(row.dedupe_key == record.dedupe_key for row in self._s.outbox):
            raise DuplicateRecordError("outbox record already exists", key=record.dedupe_key)
        self._s.outbox.append(record.model_copy(deep=True))

    def list_pending(self, *, limit: int = 100):
        rows = sorted((row for row in self._s.outbox if row.status == "PENDING"),
                      key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows[:limit]]


class InMemoryUnitOfWork:
    """Atomic like the real store: an exception restores the pre-run state."""

    def __init__(self) -> None:
        self._store = _Store()
        self.audit = InMemoryAuditRepository(self._store)
        self.authoring = InMemoryAuthoringRepository(self._store)
        self.schedules = InMemoryScheduleVersionRepository(self._store)
        self.patients = InMemoryPatientRepository(self._store)
        self.facts = InMemoryPatientFactsRepository(self._store)
        self.evaluations = InMemoryEvaluationRepository(self._store)
        self.commands = InMemoryCommandRepository(self._store)
        self.impacts = InMemoryImpactRepository(self._store)
        self.parity = InMemoryParityRepository(self._store)
        self.outbox = InMemoryOutboxRepository(self._store)

    def run(self, fn: Callable[["InMemoryUnitOfWork"], T]) -> T:
        snapshot = copy.deepcopy(self._store.__dict__)
        try:
            return fn(self)
        except BaseException:
            self._store.__dict__.clear()
            self._store.__dict__.update(snapshot)
            raise

    def read(self, fn: Callable[["InMemoryUnitOfWork"], T]) -> T:
        return fn(self)
