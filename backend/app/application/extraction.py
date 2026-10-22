"""Extraction runs and schedule import on the repository ports.

A behaviour-preserving port of ``app.services.extraction_service.ExtractionService``,
``app.services.native_import`` and ``app.services.canonical_bridge``. The SQL
versions ran inside the caller's session; here an import is ONE unit of work, so
a failure part-way leaves no trial, protocol version, run or draft behind.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable, TypeVar
from uuid import UUID, uuid4

from app.domain.schedule.models import ScheduleStatus, ValidationIssue
from app.domain.schedule.temporal_basis import upgrade_schedule_payload
from app.extraction.contracts import ExtractionResult

from .authoring import (
    ExtractionRunRecord, ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord,
    TrialRecord,
)
from .errors import DuplicateRecordError, StaleRevisionError
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

T = TypeVar("T")

NATIVE_PROVIDER = "mtb.native_extraction"
NATIVE_PROMPT_VERSION = "uctsm-extractor.v1"
CANONICAL_PROVIDER = "mtb.protocol_extraction"
CANONICAL_PROMPT_VERSION = "mtb.canonical-plan.v2"
_RETRIES = 3


MAX_TRACE_BYTES = 4 * 1024 * 1024


class ProvenanceError(ValueError):
    """A run without provider/model/prompt/schema provenance may not execute."""


@dataclass(frozen=True)
class ImportedSchedule:
    """One canonical draft produced from one detected schedule option."""

    external_schedule_definition_id: str
    schedule_definition_id: UUID
    schedule_version_id: UUID
    version_number: int
    status: str
    name: str
    created: bool


@dataclass(frozen=True)
class _Completion:
    version_ids: list[UUID]
    error: str | None = None


class ExtractionClaimUnavailableError(ValueError):
    """Another worker owns this attempt, or the run has already finished."""


class ExtractionClaimLostError(ValueError):
    """An expired/replaced worker cannot finish, fail or renew the current attempt."""


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """Content address for an extracted plan (identical to the SQL bridge)."""
    payload = json.dumps(plan or {}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the service
class ExtractionService:
    """Transactional boundary around a separately hosted extraction worker."""

    def __init__(self, uow: UnitOfWork, *, clock=utc_now):
        self._uow = uow
        self._clock = clock

    def _require_claim(self, run, token):
        if (run.attempt_number == 0 and run.status != "RUNNING"
                and run.lease_token is None and token is None):
            return  # trusted synchronous import; no worker ever claimed it
        if (not token or run.lease_token != token or run.status != "RUNNING"
                or run.lease_expires_at is None or run.lease_expires_at <= self._clock()):
            raise ExtractionClaimLostError("extraction worker no longer owns this attempt")

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # -- queueing ------------------------------------------------------------
    def queue(
        self, *, organization_id: UUID, protocol_version_id: UUID, idempotency_key: str | None,
        provider: str | None, model_name: str | None, model_version: str | None,
        prompt_version: str, configuration: dict[str, object] | None = None,
    ) -> ExtractionRunRecord:
        return self._run(lambda tx: self.queue_in(
            tx, organization_id=organization_id,
            protocol_version=tx.authoring.get_protocol_version(protocol_version_id),
            idempotency_key=idempotency_key, provider=provider, model_name=model_name,
            model_version=model_version, prompt_version=prompt_version,
            configuration=configuration))

    @staticmethod
    def queue_in(
        tx: UnitOfWork, *, organization_id: UUID, protocol_version: ProtocolVersionRecord,
        idempotency_key: str | None, provider: str | None, model_name: str | None,
        model_version: str | None, prompt_version: str,
        configuration: dict[str, object] | None = None,
    ) -> ExtractionRunRecord:
        if idempotency_key:
            existing = tx.authoring.find_run_by_idempotency(organization_id, idempotency_key)
            if existing:
                same_input = (
                    existing.protocol_version_id == protocol_version.id
                    and existing.document_hash == protocol_version.document_hash
                    and existing.prompt_version == prompt_version
                    and existing.model_name == model_name)
                if not same_input:
                    raise ValueError("idempotency key was already used for different extraction inputs")
                return existing
        run = ExtractionRunRecord(
            organization_id=organization_id, protocol_version_id=protocol_version.id,
            idempotency_key=idempotency_key, status="QUEUED", provider=provider,
            model_name=model_name, model_version=model_version, prompt_version=prompt_version,
            schema_version="uctsm.v1", document_hash=protocol_version.document_hash,
            configuration=configuration or {}, trace={})
        tx.authoring.add_run(run)
        tx.audit.append(AuditRecord(
            organization_id=organization_id, action="SCHEDULE_EXTRACTION_QUEUED",
            entity_type="EXTRACTION_RUN", entity_id=run.id, before=None,
            after={"protocol_version_id": str(protocol_version.id),
                   "document_hash": protocol_version.document_hash}))
        return run

    # -- completion ------------------------------------------------------------
    @staticmethod
    def _persist_one(tx: UnitOfWork, run: ExtractionRunRecord, result: ExtractionResult) -> UUID:
        """Persist one already-extracted schedule as a new draft version."""
        schedule = result.schedule
        assert schedule is not None
        schedule = schedule.model_copy(deep=True)
        schedule.validation_issues = [
            issue.model_copy(update={"id": uuid4()}, deep=True) for issue in result.issues]
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
        protocol_version = tx.authoring.get_protocol_version(run.protocol_version_id)
        definition = tx.authoring.find_definition(
            run.protocol_version_id, schedule.schedule_metadata.name,
            schedule.schedule_metadata.schedule_type)
        if definition is None:
            definition = ScheduleDefinitionRecord(
                organization_id=protocol_version.organization_id,
                trial_id=protocol_version.trial_id, protocol_id=protocol_version.protocol_id,
                protocol_version_id=run.protocol_version_id,
                name=schedule.schedule_metadata.name,
                description=schedule.schedule_metadata.description,
                schedule_type=schedule.schedule_metadata.schedule_type)
            tx.authoring.add_definition(definition)
        schedule.schedule_metadata.version_number = tx.schedules.max_version_number(definition.id) + 1
        schedule.schedule_metadata.protocol_version_id = run.protocol_version_id
        tx.schedules.persist_draft(
            schedule, schedule_definition_id=definition.id, extraction_run_id=run.id)
        return schedule.schedule_version_id

    def complete_in(
        self, tx: UnitOfWork, run_id: UUID,
        results: list[tuple[dict[str, Any] | None, ExtractionResult]],
        *, claim_token: str | None = None,
    ) -> _Completion:
        """Persist every schedule one run produced, then mark the run once.

        A run that produced no usable schedule is marked FAILED with every
        option's issues attached; that outcome is returned (not raised) so the
        FAILED state can be committed before the caller reports it.
        """
        run = tx.authoring.get_run(run_id)
        self._require_claim(run, claim_token)
        if run.status not in {"QUEUED", "RUNNING", "PARTIAL"}:
            raise ValueError(f"cannot complete extraction in {run.status} state")
        entries: list[dict[str, Any]] = [
            {"option": option, "nodes": result.extraction_trace,
             "accountability": result.accountability} for option, result in results]
        version_ids: list[UUID] = []
        empty: list[dict[str, Any]] = []
        for index, (option, result) in enumerate(results):
            if result.schedule is None:
                continue
            if not result.schedule.events and result.accountability is not None:
                # Never an empty-success draft from the document reader (AT-20).
                empty.append({"option": option, "issues": [
                    item.model_dump(mode="json") for item in result.issues]})
                continue
            version_id = self._persist_one(tx, run, result)
            version_ids.append(version_id)
            entries[index]["schedule_version_id"] = str(version_id)
        size = len(json.dumps(entries, default=str))
        if size > MAX_TRACE_BYTES:
            raise ValueError("extraction trace is too large to keep with the run; store raw provider payloads in the "
                             "artifact store and keep only a reference")
        run = run.model_copy(update={
            "trace": {"options": [dict(entry) for entry in entries]}, "completed_at": self._clock(),
            "lease_token": None, "lease_expires_at": None})
        if not version_ids:
            run = run.model_copy(update={
                "status": "FAILED",
                "error_details": {"issues": [
                    item.model_dump(mode="json")
                    for _option, result in results for item in result.issues],
                    "empty_schedules": len(empty)}})
            tx.authoring.save_run(run)
            return _Completion([], "extraction did not produce any schema-valid schedule with visits")
        failed_options = [
            {"option": option, "issues": [item.model_dump(mode="json") for item in result.issues]}
            for option, result in results if result.schedule is None] + empty
        run = run.model_copy(update={
            "status": "PARTIAL" if failed_options else "COMPLETED",
            "error_details": {"failed_options": failed_options} if failed_options else None})
        tx.authoring.save_run(run)
        tx.audit.append(AuditRecord(
            organization_id=run.organization_id, action="SCHEDULE_EXTRACTION_COMPLETED",
            entity_type="EXTRACTION_RUN", entity_id=run.id, before=None,
            after={"schedule_version_ids": [str(item) for item in version_ids],
                   "issues": sum(len(result.issues) for _option, result in results)}))
        return _Completion(version_ids)

    def complete_many(
        self, run_id: UUID, results: list[tuple[dict[str, Any] | None, ExtractionResult]],
        *, claim_token: str | None = None,
    ) -> list[UUID]:
        completion = self._run(lambda tx: self.complete_in(tx, run_id, results, claim_token=claim_token))
        if completion.error:
            raise ValueError(completion.error)
        return completion.version_ids

    def complete(self, run_id: UUID, result: ExtractionResult, *, claim_token: str | None = None) -> UUID:
        return self.complete_many(run_id, [(None, result)], claim_token=claim_token)[0]

    def accountability_for_version(self, schedule_version_id: UUID) -> dict[str, Any] | None:
        def work(tx: UnitOfWork) -> dict[str, Any] | None:
            header = tx.schedules.get_header(schedule_version_id)
            if header.extraction_run_id is None:
                return None
            from .schedule_review import _accountability

            return _accountability(tx.authoring.get_run(header.extraction_run_id), schedule_version_id)

        try:
            return self._uow.read(work)
        except KeyError:
            return None

    # -- reviewer decisions about extracted statements -----------------------------
    def record_statement_decisions(
        self, schedule_version_id: UUID, *, statement_ids: list[str], actor_id: UUID,
        reason: str, action: str, question_id: str | None = None,
        resolved_issue_codes: list[str] | None = None,
    ) -> list[str]:
        """Release the statements a question held, by recorded reviewer decision."""
        def work(tx: UnitOfWork) -> list[str]:
            try:
                header = tx.schedules.get_header(schedule_version_id)
            except KeyError:
                return []
            if header.extraction_run_id is None or not statement_ids:
                return []
            run = tx.authoring.get_run(header.extraction_run_id)
            trace = dict(run.trace or {})
            options = [dict(item) if isinstance(item, dict) else item
                       for item in trace.get("options") or []]
            matches = [index for index, item in enumerate(options)
                       if isinstance(item, dict) and item.get("accountability")]
            target = next((index for index in matches
                           if options[index].get("schedule_version_id") == str(schedule_version_id)),
                          None)
            if target is None:
                if len(matches) != 1:
                    return []
                target = matches[0]
            ledger = dict(options[target]["accountability"])
            wanted = {str(item) for item in statement_ids}
            resolved_codes = set(resolved_issue_codes or [])
            decision_id = str(uuid4())
            now = utc_now().isoformat()
            uncompiled = {"PENDING_INTERPRETATION", "UNSUPPORTED", "INVALID"}
            released: list[str] = []
            before: dict[str, Any] = {}
            statements = []
            for original in ledger.get("statements") or []:
                statement = dict(original) if isinstance(original, dict) else original
                statements.append(statement)
                if not isinstance(statement, dict) or str(statement.get("statement_id")) not in wanted:
                    continue
                if statement.get("disposition") != "PENDING_INTERPRETATION":
                    continue
                remaining = [code for code in statement.get("blocking_issue_codes") or []
                             if code not in resolved_codes]
                statement["blocking_issue_codes"] = remaining
                if remaining or not statement.get("rule_refs"):
                    continue
                before[str(statement["statement_id"])] = {
                    "disposition": statement.get("disposition"), "reason": statement.get("reason")}
                statement["disposition_history"] = [*(statement.get("disposition_history") or []), {
                    "disposition": statement.get("disposition"), "reason": statement.get("reason"),
                    "decided_by": statement.get("decided_by") or "MODEL",
                    "superseded_at": now, "superseded_by_decision": decision_id}]
                statement["disposition"] = "COMPILED"
                statement["decided_by"] = "REVIEWER"
                statement["reason"] = f"Released by reviewer decision ({action}): {reason}"
                statement["reviewer_decision"] = {
                    "id": decision_id, "action": action, "question_id": question_id,
                    "reviewer_id": str(actor_id), "decided_at": now, "rationale": reason}
                released.append(str(statement["statement_id"]))
            anchors = dict(ledger.get("anchors") or {})
            if question_id and anchors.get("unmatched"):
                anchors["unmatched"] = [
                    {**item, "resolution": {"decision_id": decision_id, "action": action,
                                            "decided_at": now}}
                    if isinstance(item, dict) and item.get("question_id") == question_id else item
                    for item in anchors["unmatched"]]
                ledger["anchors"] = anchors
            ledger["statements"] = statements
            counts: dict[str, int] = {}
            for statement in statements:
                if isinstance(statement, dict):
                    counts[statement.get("disposition")] = counts.get(
                        statement.get("disposition"), 0) + 1
            summary = dict(ledger.get("summary") or {})
            still_uncompiled = sum(count for name, count in counts.items() if name in uncompiled)
            summary.update({
                "dispositions": {**{name: 0 for name in (summary.get("dispositions") or {})}, **counts},
                "uncompiled_statements": still_uncompiled,
                "complete": summary.get("coverage_status") == "COMPLETE" and not still_uncompiled,
            })
            ledger["summary"] = summary
            ledger["reviewer_decisions"] = [*(ledger.get("reviewer_decisions") or []), {
                "id": decision_id, "action": action, "question_id": question_id,
                "reviewer_id": str(actor_id), "decided_at": now, "rationale": reason,
                "resolved_issue_codes": sorted(resolved_codes),
                "released_statement_ids": released}]
            ledger["revision"] = int(ledger.get("revision") or 0) + 1
            options[target] = {**options[target], "accountability": ledger}
            trace["options"] = options
            tx.authoring.save_run(run.model_copy(update={"trace": trace}))
            tx.audit.append(AuditRecord(
                organization_id=run.organization_id, actor_id=actor_id,
                action="EXTRACTION_STATEMENT_DISPOSITION_DECIDED", entity_type="SCHEDULE_VERSION",
                entity_id=header.id, before=before or None,
                after={"decision_id": decision_id, "action": action, "question_id": question_id,
                       "released_statement_ids": released, "ledger_revision": ledger["revision"]},
                reason=reason))
            return released

        return self._run(work)

    # -- run lifecycle ----------------------------------------------------------------
    @staticmethod
    def require_provenance(run: ExtractionRunRecord) -> None:
        """A run may only execute when we can say WHICH provider, model, prompt and schema produced its drafts."""
        missing = [name for name in ("provider", "model_name", "prompt_version", "schema_version")
                   if not str(getattr(run, name) or "").strip()]
        if missing:
            raise ProvenanceError("extraction run lacks provenance: " + ", ".join(missing))

    def start(self, run_id: UUID, *, lease_seconds: int = 120) -> ExtractionRunRecord:
        """Atomically claim a queued run or reclaim an explicitly expired attempt."""
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("extraction lease must be between 30 and 3600 seconds")
        def work(tx: UnitOfWork) -> ExtractionRunRecord:
            run = tx.authoring.get_run(run_id)
            now = self._clock()
            expired = run.status == "RUNNING" and run.lease_expires_at is not None and run.lease_expires_at <= now
            if run.status != "QUEUED" and not expired:
                raise ExtractionClaimUnavailableError(f"cannot execute extraction in {run.status} state")
            self.require_provenance(run)
            run = run.model_copy(update={"status": "RUNNING", "started_at": now,
                "attempt_number": run.attempt_number + 1, "lease_token": uuid4().hex,
                "lease_expires_at": now + timedelta(seconds=lease_seconds)})
            tx.authoring.save_run(run)
            return run

        return self._run(work)

    def renew(self, run_id: UUID, claim_token: str, *, lease_seconds: int = 120):
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("extraction lease must be between 30 and 3600 seconds")
        def work(tx):
            run = tx.authoring.get_run(run_id)
            self._require_claim(run, claim_token)
            renewed = run.model_copy(update={"lease_expires_at": self._clock() + timedelta(seconds=lease_seconds)})
            tx.authoring.save_run(renewed)
            return renewed
        return self._run(work)

    def fail(self, run_id: UUID, error: Exception, *, claim_token: str | None = None) -> None:
        def work(tx: UnitOfWork) -> None:
            try:
                run = tx.authoring.get_run(run_id)
            except KeyError:
                return
            self._require_claim(run, claim_token)
            if run.status not in {"COMPLETED", "PARTIAL"}:
                tx.authoring.save_run(run.model_copy(update={
                    "status": "FAILED", "completed_at": self._clock(),
                    "lease_token": None, "lease_expires_at": None,
                    "error_details": {"type": type(error).__name__, "message": str(error)}}))

        self._run(work)


# ----------------------------------------------------------------------- imports
def _trial(
    tx: UnitOfWork, *, organization_id: UUID, external_trial_id: str,
    study_title: str | None, protocol_id: str | None, actor_id: UUID, provider: str,
) -> TrialRecord:
    row = tx.authoring.find_trial_by_external_id(organization_id, external_trial_id)
    if row is not None:
        return row
    row = TrialRecord(
        organization_id=organization_id, external_trial_id=external_trial_id,
        study_title=study_title, protocol_id=protocol_id)
    tx.authoring.add_trial(row)
    tx.audit.append(AuditRecord(
        organization_id=organization_id, actor_id=actor_id, action="TRIAL_CREATED",
        entity_type="TRIAL", entity_id=row.id, before=None,
        after={"external_trial_id": external_trial_id, "source": provider}))
    return row


def _protocol(tx: UnitOfWork, trial: TrialRecord, protocol_number: str) -> ProtocolRecord:
    row = tx.authoring.find_protocol_for_trial(trial.id)
    if row is not None:
        return row
    row = ProtocolRecord(
        organization_id=trial.organization_id, trial_id=trial.id, protocol_number=protocol_number)
    tx.authoring.add_protocol(row)
    return row


def _protocol_version(
    tx: UnitOfWork, protocol: ProtocolRecord, *, document_hash: str, document_name: str,
    document_uri: str, version_label: str, actor_id: UUID, provider: str,
) -> ProtocolVersionRecord:
    row = tx.authoring.find_protocol_version_by_hash(protocol.id, document_hash)
    if row is not None:
        return row
    taken = set(tx.authoring.list_protocol_version_labels(protocol.id))
    label = version_label or f"Extracted {len(taken) + 1}"
    if label in taken:
        base_label, suffix = label, 2
        while label in taken:
            label = f"{base_label} ({suffix})"
            suffix += 1
    row = ProtocolVersionRecord(
        organization_id=protocol.organization_id, trial_id=protocol.trial_id,
        protocol_id=protocol.id, version_label=label, document_name=document_name,
        document_uri=document_uri, document_hash=document_hash, uploaded_by=actor_id,
        extraction_status="COMPLETED", metadata={"source": provider, "version_intent": "EXTRACTION_DRAFT"})
    tx.authoring.add_protocol_version(row)
    # An amendment must never silently replace the version already in use for
    # enrolment; that switch is an explicit approval step, not an import effect.
    if protocol.current_version_id is None:
        tx.authoring.set_current_protocol_version(protocol.id, row.id)
    return row


def _imported(tx: UnitOfWork, header, external_id: str, name: str, *, created: bool) -> ImportedSchedule:
    definition = tx.authoring.get_definition(header.schedule_definition_id)
    return ImportedSchedule(
        external_schedule_definition_id=external_id,
        schedule_definition_id=header.schedule_definition_id, schedule_version_id=header.id,
        version_number=header.version_number, status=header.status,
        name=definition.name if definition else name, created=created)


def import_native_schedules(
    uow: UnitOfWork, *, organization_id: UUID, actor_id: UUID, external_trial_id: str,
    protocol_number: str, study_title: str | None, document_uri: str, document_name: str,
    document_hash: str, version_label: str = "", definitions: Iterable[dict[str, Any]],
) -> tuple[TrialRecord, list[ImportedSchedule]]:
    """Import every natively extracted schedule option as a canonical draft.

    One unit of work: a failing option leaves nothing behind. A definition with
    no ``native_schedule`` is skipped - an empty schedule would look approvable
    while promising a patient nothing.
    """
    items = list(definitions)
    service = ExtractionService(uow)

    def work(tx: UnitOfWork) -> tuple[TrialRecord, list[ImportedSchedule]]:
        trial = _trial(
            tx, organization_id=organization_id, external_trial_id=external_trial_id,
            study_title=study_title, protocol_id=protocol_number, actor_id=actor_id,
            provider=NATIVE_PROVIDER)
        protocol = _protocol(tx, trial, protocol_number or external_trial_id)
        protocol_version = _protocol_version(
            tx, protocol, document_hash=document_hash, document_name=document_name,
            document_uri=document_uri, version_label=version_label, actor_id=actor_id,
            provider=NATIVE_PROVIDER)
        imported: list[ImportedSchedule] = []
        for definition in items:
            native_schedule = definition.get("native_schedule")
            external_id = str(definition.get("id") or "")
            if not isinstance(native_schedule, dict) or not native_schedule.get("events") \
                    or not external_id:
                continue
            run = service.queue_in(
                tx, organization_id=organization_id, protocol_version=protocol_version,
                idempotency_key=f"native-import:{external_id}", provider=NATIVE_PROVIDER,
                model_name=str(definition.get("model_name") or "") or None, model_version=None,
                prompt_version=NATIVE_PROMPT_VERSION,
                configuration={"external_schedule_definition_id": external_id})
            already = tx.schedules.find_by_extraction_run(run.id)
            if already is not None:
                # A retried Add Trial reuses the draft the reviewer may be working on.
                imported.append(_imported(tx, already, external_id, "Primary", created=False))
                continue
            # P1-01: a draft stored before ``temporal_basis`` existed is upgraded.
            from app.domain.schedule.models import UniversalSchedule

            schedule = UniversalSchedule.model_validate(upgrade_schedule_payload(native_schedule))
            issues = [ValidationIssue.model_validate(item)
                      for item in definition.get("native_issues") or []]
            accountability = definition.get("native_accountability")
            result = ExtractionResult(
                schedule=schedule, issues=issues, extraction_trace=[],
                accountability=accountability if isinstance(accountability, dict) else None)
            completion = service.complete_in(tx, run.id, [(None, result)])
            if completion.error:
                raise ValueError(completion.error)
            header = tx.schedules.get_header(completion.version_ids[0])
            tx.authoring.set_definition_external_id(header.schedule_definition_id, external_id)
            imported.append(_imported(tx, header, external_id, "Primary", created=True))
        return trial, imported

    return uow.run(work)


def import_schedule_definitions(
    uow: UnitOfWork, *, organization_id: UUID, actor_id: UUID, external_trial_id: str,
    protocol_number: str, study_title: str | None = None, definitions: Iterable[dict[str, Any]],
) -> tuple[TrialRecord, list[ImportedSchedule]]:
    """Import every operational schedule definition as a canonical draft version."""
    from app.extraction.canonical_adapter import extraction_result_from_plan

    items = list(definitions)
    service = ExtractionService(uow)

    def work(tx: UnitOfWork) -> tuple[TrialRecord, list[ImportedSchedule]]:
        trial = _trial(
            tx, organization_id=organization_id, external_trial_id=external_trial_id,
            study_title=study_title, protocol_id=protocol_number, actor_id=actor_id,
            provider=CANONICAL_PROVIDER)
        protocol = _protocol(tx, trial, protocol_number or external_trial_id)
        imported: list[ImportedSchedule] = []
        for definition in items:
            plan = definition.get("canonical_plan")
            external_id = str(definition.get("id") or "")
            if not isinstance(plan, dict) or not plan.get("events") or not external_id:
                continue
            name = (str(definition.get("option_label") or "").strip()
                    or str((plan.get("title") or "")).strip() or "Primary")
            fingerprint = plan_fingerprint(plan)
            protocol_version = _protocol_version(
                tx, protocol, document_hash=fingerprint,
                document_name=str(definition.get("file_name") or "protocol.pdf"),
                document_uri=f"private://protocol-extractions/{external_id}",
                version_label=str(plan.get("protocol_version") or "").strip(),
                actor_id=actor_id, provider=CANONICAL_PROVIDER)
            run = service.queue_in(
                tx, organization_id=organization_id, protocol_version=protocol_version,
                idempotency_key=f"canonical-import:{external_id}", provider=CANONICAL_PROVIDER,
                model_name=str(definition.get("model_name") or "") or None, model_version=None,
                prompt_version=CANONICAL_PROMPT_VERSION,
                configuration={"external_schedule_definition_id": external_id})
            already = tx.schedules.find_by_extraction_run(run.id)
            if already is not None:
                imported.append(_imported(tx, already, external_id, name, created=False))
                continue
            result = extraction_result_from_plan(
                plan, name=name,
                description=str(definition.get("option_description") or "").strip() or None,
                evidence_facts=definition.get("evidence_facts") or [],
                source={"external_schedule_definition_id": external_id,
                        "external_trial_id": external_trial_id, "document_hash": fingerprint})
            completion = service.complete_in(tx, run.id, [(None, result)])
            if completion.error:
                raise ValueError(completion.error)
            header = tx.schedules.get_header(completion.version_ids[0])
            tx.authoring.set_definition_external_id(header.schedule_definition_id, external_id)
            imported.append(_imported(tx, header, external_id, name, created=True))
        return trial, imported

    return uow.run(work)


@dataclass(frozen=True)
class ScheduleVersionRow:
    """One row of the trial-level protocol/schedule version table (doc 9)."""

    schedule_definition_id: UUID
    schedule_version_id: UUID
    name: str
    schedule_type: str
    version_number: int
    status: str
    protocol_version_label: str
    effective_from: Any
    approved_at: Any
    is_current_protocol_version: bool
    patients: int


def list_schedule_versions(
    uow: UnitOfWork, *, organization_id: UUID, external_trial_id: str,
) -> list[ScheduleVersionRow]:
    """Every canonical version this trial has, with its patient count; a
    superseded version is listed, never hidden."""
    def work(tx: UnitOfWork) -> list[ScheduleVersionRow]:
        trial = tx.authoring.find_trial_by_external_id(organization_id, external_trial_id)
        if trial is None:
            return []
        protocol = tx.authoring.find_protocol_for_trial(trial.id)
        counts = tx.patients.count_by_schedule_version(organization_id, trial.id)
        rows = []
        for header in tx.schedules.list_headers(organization_id=organization_id, trial_id=trial.id):
            definition = tx.authoring.get_definition(header.schedule_definition_id)
            protocol_version = tx.authoring.get_protocol_version(header.protocol_version_id)
            rows.append(ScheduleVersionRow(
                schedule_definition_id=definition.id, schedule_version_id=header.id,
                name=definition.name, schedule_type=definition.schedule_type,
                version_number=header.version_number, status=header.status,
                protocol_version_label=protocol_version.version_label,
                effective_from=header.effective_from, approved_at=header.approved_at,
                is_current_protocol_version=bool(
                    protocol and protocol.current_version_id == protocol_version.id),
                patients=int(counts.get(header.id, 0))))
        rows.sort(key=lambda row: (row.name, -row.version_number))
        return rows

    return uow.read(work)
