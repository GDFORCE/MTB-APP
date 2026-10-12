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
)
from app.application.records import AuditRecord
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


class InMemoryUnitOfWork:
    """Atomic like the real store: an exception restores the pre-run state."""

    def __init__(self) -> None:
        self._store = _Store()
        self.audit = InMemoryAuditRepository(self._store)
        self.authoring = InMemoryAuthoringRepository(self._store)
        self.schedules = InMemoryScheduleVersionRepository(self._store)

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
