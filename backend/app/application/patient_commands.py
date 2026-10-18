"""Typed, auditable patient schedule commands with a preview that IS the commit (P5-03/P5-04).

One calculation path
--------------------
``_apply`` is the ONLY place a command's effect is computed: it writes the
underlying fact (if the command is a clinical fact), appends the ledger row,
runs the ordinary evaluator through ``PatientScheduleService.evaluate_in`` and
reads back the before/after. A PREVIEW runs ``_apply`` inside a transaction that
is rolled back and keeps only the numbers; a CONFIRMATION runs the very same
function and commits. Preview and commit therefore cannot diverge, and a preview
needs no second implementation of any rule.

Confirmation additionally re-checks, before anything is kept, that
(a) the calculation's *base* - every fact, command and evaluation it was
computed from - is unchanged, and (b) the resulting semantic digest equals the
previewed one. Otherwise the proposal is marked STALE and nothing is written.

Why this is not an optimistic shortcut for the legacy impact service: the legacy
service previews assignment/anchor/condition/process changes by re-deriving the
evaluation from overrides. P5 commands are different in kind (they append ledger
entries and facts), so the commit-time ledger sequence, the unique indexes and
the reversal rules live here; the legacy service refuses to confirm a command
proposal (``kind == PATIENT_COMMAND``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.schedule.command_impact import (
    OccurrenceView, impact, notification_intents, semantic_digest, view_of_record,
)
from app.domain.schedule.commands import (
    COMMAND_SEMANTICS_VERSION, FACT_COMMANDS, TRACK_COMMANDS, CancelOccurrence, CommandPayload,
    CommandType, CreateUnscheduled, LedgerEntry, MarkMissed, OverrideOccurrence, RecordActual,
    RescheduleAppointment, ReverseCommand, check_reversal, validate_track_command,
)
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import OccurrenceFactKind

from .authority import PlaneAuthorityError, require_command_plane_authority
from .command_ledger import append as ledger_append
from .command_ledger import entries_for, material_hash, next_sequence, records_for, to_entry
from .errors import (
    DuplicateRecordError, IdempotencyConflictError, RecordNotFoundError,
    ScheduleVersionChangedError, StaleRevisionError,
)
from .patient_schedule import (
    BOOKING_WITHDRAWN, FACT_KIND_KEY, SUPERSEDES_KEY, PatientScheduleService,
    occurrence_fact_from_record,
)
from .patients import (
    ImpactProposalRecord, OccurrenceRecord, PatientCommandRecord, PatientEventRecord, PatientRecord,
    UnscheduledVisitRecord,
)
from .ports import UnitOfWork
from .records import AuditRecord, OutboxRecord, utc_now

T = TypeVar("T")
_RETRIES = 3
PROPOSAL_KIND = "PATIENT_COMMAND"
_TERMINAL = {"COMPLETED", "MISSED", "CANCELLED"}


class CommandRefusedError(ValueError):
    """A command the schedule cannot accept. ``code`` is machine-readable and
    ``problems`` lists every reason found."""

    def __init__(self, code: str, message: str, problems: list[dict[str, str]] | None = None,
                 status: int = 409):
        super().__init__(message)
        self.code = code
        self.problems = problems or [{"code": code, "message": message}]
        self.status = status


class StaleProposalError(CommandRefusedError):
    def __init__(self, message: str):
        super().__init__("PROPOSAL_STALE", message)


class CommandImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    patient_id: UUID
    schedule_version_id: UUID
    command_type: str
    effective_at: date
    expires_at: datetime
    horizon: date
    changes: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    summary: dict[str, int]
    digest: str
    base_fingerprint: str
    reverses_command_id: str | None = None


class CommandOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    command_id: UUID
    sequence: int
    command_type: str
    evaluation_id: UUID | None
    schedule_version_id: UUID
    changes: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    summary: dict[str, int]
    digest: str
    replayed: bool = False


@dataclass
class _Prepared:
    payload: CommandPayload
    command_type: CommandType
    payload_json: dict[str, Any]
    logical_key: str | None = None
    track_code: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    requested: dict[str, Any] = field(default_factory=dict)
    #: how the underlying fact is written (None for ledger-only commands)
    fact: dict[str, Any] | None = None
    unscheduled: dict[str, Any] | None = None
    withdraw_booking: UUID | None = None      # superseded booking occurrence id
    restore_booking: dict[str, Any] | None = None
    reverses: UUID | None = None


@dataclass
class _Applied:
    record: PatientCommandRecord
    evaluation_id: UUID | None
    rows: list[dict[str, Any]]
    intents: list[dict[str, Any]]
    digest: str


class _RolledBack(Exception):
    """Raised on purpose to discard a preview's writes."""


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "changed": sum(1 for row in rows if row["changed"]),
        "unchanged": sum(1 for row in rows if not row["changed"]),
        "protocol_targets_moved": sum(1 for row in rows if row["change_kind"] == "PROTOCOL_TARGET"),
    }


def _views(records: list[PatientEventRecord]) -> list[OccurrenceView]:
    return [view_of_record(
        item.logical_key, item.event_code, item.occurrence_index, item.nominal_start_date,
        item.earliest_date, item.latest_date, item.status, item.generation_reason)
        for item in records]


class PatientCommandService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self.patient_schedules = PatientScheduleService(uow)

    # ------------------------------------------------------------------ plumbing
    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------------ preview
    def preview(
        self, patient_id: UUID, *, organization_id: UUID, actor_id: UUID, payload: CommandPayload,
        effective_at: date, reason: str, authority: str | None = None, horizon: date | None = None,
        today: date | None = None, expires_in_minutes: int = 30,
    ) -> CommandImpact:
        if not reason or not reason.strip():
            raise CommandRefusedError(
                "REASON_REQUIRED", "a command needs a reason; it becomes part of the audit trail",
                status=422)
        clock = today or date.today()
        horizon = horizon or (clock + timedelta(days=365))
        captured: dict[str, Any] = {}

        def simulate(tx: UnitOfWork) -> None:
            patient = tx.patients.get(organization_id, patient_id)
            version_id = self._version(patient)
            prepared = self._prepare(tx, patient, version_id, payload, effective_at, reason,
                                     authority, clock)
            captured["base"] = self._base_fingerprint(tx, patient, version_id, horizon, prepared)
            captured["applied"] = self._apply(
                tx, patient, version_id, prepared, actor_id=actor_id, effective_at=effective_at,
                reason=reason.strip(), authority=authority, horizon=horizon,
                proposal_id=None, base_revision=captured["base"], idempotency_key=None, today=clock)
            captured["prepared"] = prepared
            captured["version"] = version_id
            raise _RolledBack()           # a preview keeps NO clinical write

        try:
            self._run(simulate)
        except _RolledBack:
            pass
        applied: _Applied = captured["applied"]
        prepared: _Prepared = captured["prepared"]
        version_id: UUID = captured["version"]
        expires = utc_now() + timedelta(minutes=expires_in_minutes)
        command_hash = self._command_hash(patient_id, version_id, prepared, effective_at, horizon)
        impact_payload = {
            "kind": PROPOSAL_KIND, "semantics_version": COMMAND_SEMANTICS_VERSION,
            "command": {
                "payload": prepared.payload_json, "type": prepared.command_type.value,
                "effective_at": effective_at.isoformat(), "authority": authority,
                "reverses_command_id": str(prepared.reverses) if prepared.reverses else None},
            "changes": applied.rows, "notifications": applied.intents,
            "summary": _summary(applied.rows), "digest": applied.digest,
            "base_fingerprint": captured["base"], "command_hash": command_hash,
            "evaluator_version": ScheduleEvaluator.version,
        }
        proposal = ImpactProposalRecord(
            organization_id=organization_id, patient_id=patient_id,
            from_schedule_version_id=version_id, to_schedule_version_id=version_id,
            horizon=horizon, input_hash=captured["base"], impact=impact_payload,
            status="PENDING", reason=reason.strip(), created_by=actor_id, expires_at=expires)

        def store(tx: UnitOfWork) -> ImpactProposalRecord:
            tx.patients.get(organization_id, patient_id)
            tx.impacts.add(proposal)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_COMMAND_PREVIEWED", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"schedule_version_id": str(version_id)},
                after={"command_type": prepared.command_type.value,
                       "summary": impact_payload["summary"], "digest": applied.digest},
                reason=reason.strip()))
            return proposal

        self._run(store)
        return CommandImpact(
            proposal_id=proposal.id, patient_id=patient_id, schedule_version_id=version_id,
            command_type=prepared.command_type.value, effective_at=effective_at,
            expires_at=expires, horizon=horizon, changes=applied.rows,
            notifications=applied.intents, summary=impact_payload["summary"], digest=applied.digest,
            base_fingerprint=captured["base"],
            reverses_command_id=str(prepared.reverses) if prepared.reverses else None)

    # ------------------------------------------------------------------ confirm
    def confirm(
        self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID,
        idempotency_key: str | None = None, reason: str | None = None, today: date | None = None,
    ) -> CommandOutcome:
        clock = today or date.today()
        try:
            outcome = self._run(lambda tx: self._confirm(
                tx, proposal_id, organization_id, actor_id, idempotency_key, reason, clock))
        except _DigestMismatch as mismatch:
            # the transaction that noticed was aborted: nothing clinical was kept
            self.mark_stale(proposal_id, organization_id=organization_id,
                            reason="calculation no longer matches the preview")
            raise StaleProposalError(
                "the calculation changed after this preview was taken; preview again") from mismatch
        if isinstance(outcome, StaleProposalError):
            raise outcome
        return outcome

    def _confirm(
        self, tx: UnitOfWork, proposal_id: UUID, organization_id: UUID, actor_id: UUID,
        idempotency_key: str | None, reason: str | None, clock: date,
    ) -> CommandOutcome | StaleProposalError:
        proposal = tx.impacts.get(proposal_id)
        patient = tx.patients.get(organization_id, proposal.patient_id)
        self._version(patient)          # authority first - before ANY replay or recalculation
        meta = proposal.impact or {}
        if meta.get("kind") != PROPOSAL_KIND:
            raise CommandRefusedError("NOT_A_COMMAND_PROPOSAL",
                                      "this proposal is not a patient command", status=422)
        stored = meta.get("committed")
        command_hash = meta.get("command_hash")
        if idempotency_key:
            existing = tx.ledger.find_by_idempotency(organization_id, patient.id, idempotency_key)
            if existing is not None:
                if existing.payload_hash != command_hash:
                    raise IdempotencyConflictError(
                        "this idempotency key was already used for a different command")
                return self._replayed(tx, existing, proposal)
        if proposal.status == "CONFIRMED" and stored and idempotency_key is None:
            record = tx.ledger.get(organization_id, UUID(stored["command_id"]))
            return self._replayed(tx, record, proposal)
        if proposal.status != "PENDING":
            raise CommandRefusedError(
                "PROPOSAL_NOT_PENDING", f"this preview is {proposal.status.lower()}; preview again")
        expires_at = proposal.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            tx.impacts.save(proposal.model_copy(update={"status": "EXPIRED"}))
            return StaleProposalError("this preview has expired; preview again")
        self._version(patient)          # authority is rechecked at confirmation
        version_id = patient.current_schedule_version_id
        if version_id != proposal.from_schedule_version_id:
            tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))
            return StaleProposalError(
                "the patient's schedule version changed after this preview; preview again")
        command = meta["command"]
        payload = _parse_payload(command["payload"])
        effective_at = date.fromisoformat(command["effective_at"])
        authority = command.get("authority")
        reason_text = (reason or proposal.reason or "").strip()
        prepared = self._prepare(tx, patient, version_id, payload, effective_at, reason_text,
                                 authority, clock)
        base = self._base_fingerprint(tx, patient, version_id, proposal.horizon, prepared)
        if base != meta.get("base_fingerprint"):
            tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_COMMAND_STALE", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"base": meta.get("base_fingerprint")},
                after={"base": base}))
            return StaleProposalError(
                "the patient's facts, commands or schedule changed after this preview; "
                "preview again")
        applied = self._apply(
            tx, patient, version_id, prepared, actor_id=actor_id, effective_at=effective_at,
            reason=reason_text, authority=authority, horizon=proposal.horizon,
            proposal_id=proposal.id, base_revision=base,
            idempotency_key=idempotency_key, today=clock)
        if applied.digest != meta.get("digest"):
            # Refuse BEFORE anything is kept: raising aborts this transaction.
            raise _DigestMismatch(proposal.id, applied.digest)
        committed = {
            "command_id": str(applied.record.id), "sequence": applied.record.sequence,
            "evaluation_id": str(applied.evaluation_id) if applied.evaluation_id else None}
        tx.impacts.save(proposal.model_copy(update={
            "status": "CONFIRMED", "confirmed_by": actor_id, "confirmed_at": utc_now(),
            "impact": {**meta, "committed": committed, "committed_digest": applied.digest,
                       "changes": applied.rows, "notifications": applied.intents,
                       "summary": _summary(applied.rows)}}))
        return CommandOutcome(
            proposal_id=proposal.id, command_id=applied.record.id,
            sequence=applied.record.sequence, command_type=applied.record.command_type,
            evaluation_id=applied.evaluation_id, schedule_version_id=version_id,
            changes=applied.rows, notifications=applied.intents, summary=_summary(applied.rows),
            digest=applied.digest)

    def _replayed(
        self, tx: UnitOfWork, record: PatientCommandRecord, proposal: ImpactProposalRecord,
    ) -> CommandOutcome:
        """The ORIGINAL committed outcome of ``record`` - never the requested
        proposal's own preview. The canonical proposal is the one that committed
        the command; a duplicate proposal is linked to it and left unchanged
        clinically."""
        canonical: ImpactProposalRecord | None = None
        if record.proposal_id is not None:
            try:
                canonical = tx.impacts.get(record.proposal_id)
            except RecordNotFoundError:
                canonical = None
        meta = (canonical.impact if canonical is not None else {}) or {}
        committed = meta.get("committed") or {}
        evaluation_id = committed.get("evaluation_id") or (
            str(record.result_evaluation_id) if record.result_evaluation_id else None)
        if proposal.status == "PENDING" and canonical is not None and canonical.id != proposal.id:
            tx.impacts.save(proposal.model_copy(update={
                "status": "CONFIRMED",
                "impact": {**(proposal.impact or {}), "duplicate_of": str(canonical.id),
                           "committed": {**committed, "command_id": str(record.id)}}}))
        return CommandOutcome(
            proposal_id=canonical.id if canonical is not None else proposal.id,
            command_id=record.id, sequence=record.sequence, command_type=record.command_type,
            evaluation_id=UUID(evaluation_id) if evaluation_id else None,
            schedule_version_id=record.schedule_version_id,
            changes=meta.get("changes", []), notifications=meta.get("notifications", []),
            summary=meta.get("summary", {}),
            digest=meta.get("committed_digest") or meta.get("digest", ""),
            replayed=True)

    def cancel(
        self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str,
    ) -> ImpactProposalRecord:
        def work(tx: UnitOfWork) -> ImpactProposalRecord:
            proposal = tx.impacts.get(proposal_id)
            tx.patients.get(organization_id, proposal.patient_id)
            if proposal.status != "PENDING":
                raise CommandRefusedError(
                    "PROPOSAL_NOT_PENDING", f"impact proposal is {proposal.status.lower()}")
            cancelled = proposal.model_copy(update={"status": "CANCELLED"})
            tx.impacts.save(cancelled)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_COMMAND_CANCELLED", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"status": "PENDING"},
                after={"status": "CANCELLED"}, reason=reason))
            return cancelled

        return self._run(work)

    def mark_stale(self, proposal_id: UUID, *, organization_id: UUID, reason: str) -> None:
        """Record a refused confirmation on the proposal (outside the aborted
        transaction that refused it)."""
        def work(tx: UnitOfWork) -> None:
            proposal = tx.impacts.get(proposal_id)
            tx.patients.get(organization_id, proposal.patient_id)
            if proposal.status == "PENDING":
                tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))

        self._run(work)

    # ------------------------------------------------------------------ reading
    def ledger(self, patient_id: UUID, *, organization_id: UUID,
               profile: str = "STAFF") -> list[dict[str, Any]]:
        """The command history. ``profile`` is the response allowlist applied
        BEFORE serialisation: STAFF carries the clinical detail; RESTRICTED keeps
        only what a de-identified reader needs to see that something changed
        (never the typed payload, reason, authority, actor or proposal)."""
        def work(tx: UnitOfWork) -> list[dict[str, Any]]:
            tx.patients.get(organization_id, patient_id)
            rows = records_for(tx, organization_id, patient_id)
            reversed_by = {item.reverses_command_id: item.id for item in rows
                           if item.reverses_command_id}
            if profile != "STAFF":
                return [{
                    "id": str(item.id), "sequence": item.sequence,
                    "command_type": item.command_type, "track_code": item.track_code,
                    "logical_key": item.logical_key, "effective_at": item.effective_at.isoformat(),
                    "recorded_at": item.recorded_at.isoformat(),
                    "schedule_version_id": str(item.schedule_version_id),
                    "reverses_command_id": (
                        str(item.reverses_command_id) if item.reverses_command_id else None),
                    "reversed_by": str(reversed_by[item.id]) if item.id in reversed_by else None,
                } for item in rows]
            return [{
                "id": str(item.id), "sequence": item.sequence, "command_type": item.command_type,
                "track_code": item.track_code, "logical_key": item.logical_key,
                "effective_at": item.effective_at.isoformat(),
                "recorded_at": item.recorded_at.isoformat(), "actor_id": str(item.actor_id),
                "authority": item.authority, "reason": item.reason, "payload": item.payload,
                "schedule_version_id": str(item.schedule_version_id),
                "reverses_command_id": str(item.reverses_command_id) if item.reverses_command_id else None,
                "reversed_by": str(reversed_by[item.id]) if item.id in reversed_by else None,
                "proposal_id": str(item.proposal_id) if item.proposal_id else None,
            } for item in rows]

        return self._uow.read(work)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _version(patient: PatientRecord) -> UUID:
        try:
            require_command_plane_authority(patient)
        except PlaneAuthorityError as error:
            raise CommandRefusedError(error.code, str(error), status=error.status) from error
        if patient.current_schedule_version_id is None:
            raise CommandRefusedError(
                "NO_ACTIVE_SCHEDULE", "the patient has no active approved schedule", status=409)
        return patient.current_schedule_version_id

    @staticmethod
    def _command_hash(
        patient_id: UUID, version_id: UUID, prepared: _Prepared, effective_at: date, horizon: date,
    ) -> str:
        return material_hash(
            patient_id=patient_id, schedule_version_id=version_id,
            command_type=prepared.command_type.value, payload=prepared.payload_json,
            effective_at=effective_at, horizon=horizon)

    def _base_fingerprint(
        self, tx: UnitOfWork, patient: PatientRecord, version_id: UUID, horizon: date,
        prepared: _Prepared,
    ) -> str:
        """Everything the calculation read: if any of it changes, a preview taken
        before is no longer a preview of THIS commit."""
        pointer = tx.evaluations.get_pointer(patient.id, version_id)
        header = tx.schedules.get_header(version_id)
        records = records_for(tx, patient.organization_id, patient.id)
        occurrences = self.patient_schedules.occurrences_for(tx, patient.id, version_id)
        payload = {
            "patient": str(patient.id), "patient_revision": patient.revision,
            "version": str(version_id), "version_status": header.status,
            "version_revision": header.revision,
            "evaluator": ScheduleEvaluator.version, "semantics": COMMAND_SEMANTICS_VERSION,
            "pointer": [str(pointer.current_evaluation_id), pointer.revision] if pointer else None,
            "ledger": [[str(item.id), item.sequence] for item in records],
            "occurrences": sorted(str(item.id) for item in occurrences),
            "anchors": sorted(f"{item.id}:{item.status}" for item in tx.facts.list_anchors(
                patient.id, statuses={"CONFIRMED", "PROVISIONAL", "PENDING_CONFIRMATION"})),
            "states": sorted(str(item.id) for item in tx.facts.list_states(patient.id)),
            "conditions": sorted(
                f"{item.id}:{item.state}" for item in tx.facts.list_current_conditions(patient.id)),
            "unscheduled": sorted(str(item.id) for item in tx.facts.list_unscheduled_visits(patient.id)),
            "assignments": sorted(str(item.id) for item in tx.impacts.list_assignments(patient.id)),
            "horizon": horizon.isoformat(),
            "command": prepared.payload_json,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    # ------------------------------------------------------------------ prepare
    def _prepare(
        self, tx: UnitOfWork, patient: PatientRecord, version_id: UUID, payload: CommandPayload,
        effective_at: date, reason: str, authority: str | None, clock: date,
    ) -> _Prepared:
        kind = CommandType(payload.type)
        payload_json = payload.model_dump(mode="json")
        prepared = _Prepared(payload=payload, command_type=kind, payload_json=payload_json)
        schedule = tx.schedules.get(version_id)
        records = records_for(tx, patient.organization_id, patient.id)
        entries = [to_entry(item) for item in records if item.schedule_version_id == version_id]
        current = {item.logical_key: item
                   for item in self.patient_schedules.current_events(tx, patient.id, version_id)}

        def occurrence(key: str) -> PatientEventRecord:
            row = current.get(key)
            if row is None:
                raise CommandRefusedError(
                    "OCCURRENCE_UNKNOWN",
                    f"occurrence {key} is not in the patient's current plan", status=404)
            return row

        def refuse(code: str, message: str) -> None:
            raise CommandRefusedError(code, message)

        if kind in TRACK_COMMANDS:
            prepared.track_code = payload.track_code
            problems = validate_track_command(schedule, entries, payload, effective_at)
            if problems:
                raise CommandRefusedError(problems[0]["code"], problems[0]["message"], problems)
            prepared.requested = {k: v for k, v in payload_json.items() if k != "type"}
            return prepared

        if isinstance(payload, ReverseCommand):
            try:
                target_id = UUID(payload.reverses_command_id)
                target = tx.ledger.get(patient.organization_id, target_id)
            except (ValueError, RecordNotFoundError):
                target = None
            if target is None or target.patient_id != patient.id \
                    or target.schedule_version_id != version_id:
                # same answer for a missing, foreign-patient or foreign-version id
                raise CommandRefusedError(
                    "REVERSAL_TARGET_UNKNOWN",
                    "that command is not part of this patient's schedule history", status=404)
            problems = check_reversal(entries, str(target.id), effective_at)
            if problems:
                raise CommandRefusedError(problems[0]["code"], problems[0]["message"], problems)
            prepared.reverses = target.id
            prepared.track_code = target.track_code
            prepared.logical_key = target.logical_key
            prepared.before = {"command_type": target.command_type, "payload": target.payload,
                               "effective_at": target.effective_at.isoformat()}
            if target.command_type == CommandType.RESCHEDULE_APPOINTMENT.value:
                booking_id = (target.fact_refs or {}).get("occurrence_id")
                if booking_id:
                    prepared.withdraw_booking = UUID(str(booking_id))
                    previous = next((item for item in self.patient_schedules.occurrences_for(
                        tx, patient.id, version_id) if str(item.id) == str(booking_id)), None)
                    earlier = (previous.metadata or {}).get(SUPERSEDES_KEY) if previous else None
                    if earlier:
                        before_row = next((item for item in self.patient_schedules.occurrences_for(
                            tx, patient.id, version_id) if str(item.id) == str(earlier)), None)
                        fact = occurrence_fact_from_record(before_row) if before_row else None
                        if fact is not None and fact.value is not None:
                            prepared.restore_booking = {"date": fact.value}
            return prepared

        if isinstance(payload, RecordActual):
            row = occurrence(payload.logical_key)
            self.patient_schedules.check_actual_dates(
                tx, patient, payload.visit_date, payload.treatment_date, clock)
            prepared.logical_key = payload.logical_key
            prepared.before = {"status": row.status, "target": _iso(row.nominal_start_date)}
            prepared.requested = {"visit_date": _iso(payload.visit_date),
                                  "treatment_date": _iso(payload.treatment_date)}
            # Treatment alone is its own fact: it never becomes a visit date and
            # never completes the visit (R6).
            treatment_only = payload.visit_date is None
            prepared.fact = {
                "kind": (OccurrenceFactKind.TREATMENT if treatment_only
                         else OccurrenceFactKind.PERFORMED),
                "value": payload.treatment_date if treatment_only else payload.visit_date,
                "treatment_date": None if treatment_only else payload.treatment_date,
                "supersedes": (UUID(payload.supersedes_occurrence_id)
                               if payload.supersedes_occurrence_id else None)}
            return prepared

        if isinstance(payload, MarkMissed):
            row = occurrence(payload.logical_key)
            if row.status == "COMPLETED":
                refuse("OCCURRENCE_ALREADY_PERFORMED",
                       f"{payload.logical_key} was performed; correct that record instead of "
                       "marking it missed")
            prepared.logical_key = payload.logical_key
            prepared.before = {"status": row.status}
            prepared.fact = {"kind": OccurrenceFactKind.MISSED, "value": None,
                             "treatment_date": None, "supersedes": None}
            return prepared

        if isinstance(payload, RescheduleAppointment):
            row = occurrence(payload.logical_key)
            if row.status in _TERMINAL:
                refuse("OCCURRENCE_CLOSED",
                       f"{payload.logical_key} is {row.status.lower()}; it cannot be rescheduled")
            existing = self._current_booking(tx, patient, version_id, payload.logical_key)
            prepared.logical_key = payload.logical_key
            prepared.before = {"appointment": _iso(
                occurrence_fact_from_record(existing).value) if existing and
                occurrence_fact_from_record(existing) else None}
            prepared.requested = {"new_date": payload.new_date.isoformat()}
            prepared.fact = {"kind": OccurrenceFactKind.BOOKED, "value": payload.new_date,
                             "treatment_date": None,
                             "supersedes": existing.id if existing is not None else None}
            return prepared

        if isinstance(payload, CancelOccurrence):
            row = occurrence(payload.logical_key)
            if row.status in _TERMINAL:
                refuse("OCCURRENCE_CLOSED",
                       f"{payload.logical_key} is {row.status.lower()}; it cannot be cancelled")
            if row.nominal_start_date is not None and row.nominal_start_date < effective_at:
                refuse("OCCURRENCE_NOT_IN_FUTURE",
                       f"{payload.logical_key} was planned for {row.nominal_start_date.isoformat()}, "
                       "before the date this cancellation takes effect; mark it missed instead")
            prepared.logical_key = payload.logical_key
            prepared.before = {"status": row.status, "target": _iso(row.nominal_start_date)}
            return prepared

        if isinstance(payload, OverrideOccurrence):
            row = occurrence(payload.logical_key)
            if row.status in _TERMINAL:
                refuse("OCCURRENCE_CLOSED",
                       f"{payload.logical_key} is {row.status.lower()}; it cannot be overridden")
            if not (authority or payload.authority).strip():
                refuse("AUTHORITY_REQUIRED", "an override names who authorised the exception")
            prepared.logical_key = payload.logical_key
            prepared.before = {"target": _iso(row.nominal_start_date),
                               "earliest": _iso(row.earliest_date), "latest": _iso(row.latest_date)}
            prepared.requested = {k: v for k, v in payload_json.items() if k != "type"}
            return prepared

        if isinstance(payload, CreateUnscheduled):
            record = self.patient_schedules.build_unscheduled_visit(
                tx, patient.id, organization_id=patient.organization_id,
                event_code=payload.event_code, occurred_on=payload.occurred_on, reason=reason,
                visit_mode=payload.visit_mode, actor_id=None)
            prepared.unscheduled = {"record": record}
            prepared.requested = {"event_code": payload.event_code,
                                  "occurred_on": payload.occurred_on.isoformat()}
            return prepared
        raise CommandRefusedError("UNSUPPORTED_COMMAND", f"unsupported command {kind.value}")

    def _current_booking(
        self, tx: UnitOfWork, patient: PatientRecord, version_id: UUID, logical_key: str,
    ) -> OccurrenceRecord | None:
        rows = self.patient_schedules.occurrences_for(tx, patient.id, version_id)
        superseded = {str((row.metadata or {}).get(SUPERSEDES_KEY)) for row in rows
                      if (row.metadata or {}).get(SUPERSEDES_KEY)}
        live = [row for row in rows if row.logical_key == logical_key
                and str(row.id) not in superseded
                and (row.metadata or {}).get(FACT_KIND_KEY) == OccurrenceFactKind.BOOKED.value]
        return live[-1] if live else None

    # ------------------------------------------------------------------ the one calculation
    def _apply(
        self, tx: UnitOfWork, patient: PatientRecord, version_id: UUID, prepared: _Prepared, *,
        actor_id: UUID, effective_at: date, reason: str, authority: str | None, horizon: date,
        proposal_id: UUID | None, base_revision: str | None, idempotency_key: str | None,
        today: date,
    ) -> _Applied:
        org = patient.organization_id
        before_records = self.patient_schedules.current_events(tx, patient.id, version_id)
        records = records_for(tx, org, patient.id)
        fact_refs: dict[str, Any] = {}
        if prepared.fact is not None:
            spec = prepared.fact
            occurrence = self.patient_schedules._record_fact(
                tx, patient.id, organization_id=org, logical_key=prepared.logical_key or "",
                kind=spec["kind"], value=spec["value"], end_value=None, effective_at=effective_at,
                supersedes_occurrence_id=spec["supersedes"], actor_id=actor_id, reason=reason,
                source="PATIENT_COMMAND", treatment_date=spec["treatment_date"])
            fact_refs["occurrence_id"] = str(occurrence.id)
        if prepared.unscheduled is not None:
            record = prepared.unscheduled["record"].model_copy(update={"created_by": actor_id})
            tx.facts.add_unscheduled_visit(record)
            fact_refs["unscheduled_visit_id"] = str(record.id)
            tx.audit.append(AuditRecord(
                organization_id=org, actor_id=actor_id, action="PATIENT_UNSCHEDULED_VISIT_RECORDED",
                entity_type="PATIENT", entity_id=patient.id, before=None,
                after={"event_code": record.event_code, "occurred_on": record.occurred_on.isoformat()},
                reason=reason))
        if prepared.withdraw_booking is not None:
            marker = self._booking_reversal(tx, patient, version_id, prepared, actor_id, reason)
            fact_refs["occurrence_id"] = str(marker.id)
        material = self._command_hash(patient.id, version_id, prepared, effective_at, horizon)
        record = ledger_append(
            tx, organization_id=org, patient_id=patient.id, schedule_version_id=version_id,
            command_type=prepared.command_type, payload=prepared.payload_json,
            effective_at=effective_at, actor_id=actor_id, reason=reason, authority=authority,
            track_code=prepared.track_code, logical_key=prepared.logical_key,
            before=prepared.before, requested=prepared.requested, proposal_id=proposal_id,
            base_revision=base_revision, idempotency_key=idempotency_key, payload_hash=material,
            reverses_command_id=prepared.reverses, fact_refs=fact_refs, records=records)
        tx.audit.append(AuditRecord(
            organization_id=org, actor_id=actor_id, action="PATIENT_COMMAND_RECORDED",
            entity_type="PATIENT", entity_id=patient.id, before=prepared.before or None,
            after={"command_id": str(record.id), "type": record.command_type,
                   "sequence": record.sequence, "effective_at": effective_at.isoformat(),
                   "requested": prepared.requested, "authority": authority,
                   "reverses": str(prepared.reverses) if prepared.reverses else None},
            reason=reason, metadata={"semantics": COMMAND_SEMANTICS_VERSION}))
        evaluation, _events = self.patient_schedules.evaluate_in(
            tx, patient.id, organization_id=org, horizon=horizon, schedule_version_id=version_id,
            idempotency_key=f"command:{record.id}", actor_id=actor_id,
            reason=f"{record.command_type}: {reason}")
        after_records = self.patient_schedules.current_events(tx, patient.id, version_id)
        rows = impact(_views(before_records), _views(after_records),
                      command_label=record.command_type.replace("_", " ").lower())
        intents = notification_intents(rows)
        digest = semantic_digest(rows, intents)
        tx.outbox.add(OutboxRecord(
            organization_id=org, aggregate_type="PATIENT_SCHEDULE", aggregate_id=patient.id,
            event_type="PATIENT_COMMAND_APPLIED", dedupe_key=f"{evaluation.id}:PATIENT_COMMAND",
            payload={"command_id": str(record.id), "type": record.command_type,
                     "evaluation_id": str(evaluation.id), "notifications": intents,
                     "changed": [row["logical_key"] for row in rows if row["changed"]]}))
        return _Applied(record, evaluation.id, rows, intents, digest)

    def _booking_reversal(
        self, tx: UnitOfWork, patient: PatientRecord, version_id: UUID, prepared: _Prepared,
        actor_id: UUID, reason: str,
    ) -> OccurrenceRecord:
        """Supersede the booking a reversed RESCHEDULE created: with the booking
        it replaced when there was one, else with a withdrawal marker."""
        assert prepared.withdraw_booking is not None
        key = prepared.logical_key or ""
        row = next((item for item in self.patient_schedules.current_events(
            tx, patient.id, version_id) if item.logical_key == key), None)
        if row is None:
            raise CommandRefusedError("OCCURRENCE_UNKNOWN", f"occurrence {key} is not in the plan")
        if prepared.restore_booking is not None:
            return self.patient_schedules._record_fact(
                tx, patient.id, organization_id=patient.organization_id, logical_key=key,
                kind=OccurrenceFactKind.BOOKED, value=prepared.restore_booking["date"],
                end_value=None, effective_at=None, supersedes_occurrence_id=prepared.withdraw_booking,
                actor_id=actor_id, reason=reason, source="PATIENT_COMMAND_REVERSAL")
        marker = OccurrenceRecord(
            organization_id=patient.organization_id, patient_id=patient.id,
            schedule_version_id=version_id, event_code=row.event_code,
            occurrence_index=row.occurrence_index, logical_key=key,
            occurrence_type="BOOKING_WITHDRAWN", scheduled_date=row.nominal_start_date,
            status="BOOKING_WITHDRAWN", recorded_by=actor_id,
            metadata={FACT_KIND_KEY: BOOKING_WITHDRAWN,
                      SUPERSEDES_KEY: str(prepared.withdraw_booking),
                      "source": "PATIENT_COMMAND_REVERSAL"})
        tx.facts.add_occurrence(marker)
        tx.audit.append(AuditRecord(
            organization_id=patient.organization_id, actor_id=actor_id,
            action="PATIENT_BOOKING_WITHDRAWN", entity_type="PATIENT_EVENT", entity_id=row.id,
            before={"occurrence_id": str(prepared.withdraw_booking)},
            after={"occurrence_id": str(marker.id), "logical_key": key}, reason=reason))
        return marker


class _DigestMismatch(Exception):
    def __init__(self, proposal_id: UUID, digest: str):
        super().__init__("the calculation no longer matches the preview")
        self.proposal_id = proposal_id
        self.digest = digest


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_payload(data: dict[str, Any]) -> CommandPayload:
    from pydantic import TypeAdapter

    return TypeAdapter(CommandPayload).validate_python(data)
