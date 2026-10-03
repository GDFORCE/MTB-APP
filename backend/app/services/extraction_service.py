from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import ScheduleStatus
from app.extraction.contracts import ExtractionResult


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionService:
    """Transactional boundary around a separately hosted extraction worker."""

    def __init__(self, session: Session):
        self.session = session

    def queue(
        self,
        *,
        organization_id: UUID,
        protocol_version: db.ProtocolVersion,
        idempotency_key: str | None,
        provider: str | None,
        model_name: str | None,
        model_version: str | None,
        prompt_version: str,
        configuration: dict[str, object] | None = None,
    ) -> db.ExtractionRun:
        if idempotency_key:
            existing = self.session.scalar(select(db.ExtractionRun).where(
                db.ExtractionRun.organization_id == organization_id,
                db.ExtractionRun.idempotency_key == idempotency_key,
            ))
            if existing:
                same_input = (
                    existing.protocol_version_id == protocol_version.id
                    and existing.document_hash == protocol_version.document_hash
                    and existing.prompt_version == prompt_version
                    and existing.model_name == model_name
                )
                if not same_input:
                    raise ValueError("idempotency key was already used for different extraction inputs")
                return existing
        run = db.ExtractionRun(
            organization_id=organization_id, protocol_version_id=protocol_version.id,
            idempotency_key=idempotency_key, status="QUEUED", provider=provider,
            model_name=model_name, model_version=model_version,
            prompt_version=prompt_version, schema_version="uctsm.v1",
            document_hash=protocol_version.document_hash,
            configuration=configuration or {}, trace={},
        )
        self.session.add(run)
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, action="SCHEDULE_EXTRACTION_QUEUED",
            entity_type="EXTRACTION_RUN", entity_id=run.id, before=None,
            after={"protocol_version_id": str(protocol_version.id), "document_hash": protocol_version.document_hash},
        ))
        return run

    def _persist_one(self, run: db.ExtractionRun, result: ExtractionResult) -> UUID:
        """Persist one already-extracted schedule as a new draft version.

        Shared by every caller of `complete_many` regardless of how many
        schedules one run produced - the run itself is marked QUEUED->
        COMPLETED exactly once, by the caller, after every schedule in the
        batch has been persisted.
        """
        schedule = result.schedule
        assert schedule is not None
        schedule = schedule.model_copy(deep=True)
        schedule.validation_issues = [
            issue.model_copy(update={"id": uuid4()}, deep=True) for issue in result.issues
        ]
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
        definition = self.session.scalar(select(db.ScheduleDefinition).where(
            db.ScheduleDefinition.protocol_version_id == run.protocol_version_id,
            db.ScheduleDefinition.name == schedule.schedule_metadata.name,
            db.ScheduleDefinition.schedule_type == schedule.schedule_metadata.schedule_type,
        ))
        if definition is None:
            definition = db.ScheduleDefinition(
                protocol_version_id=run.protocol_version_id,
                name=schedule.schedule_metadata.name,
                description=schedule.schedule_metadata.description,
                schedule_type=schedule.schedule_metadata.schedule_type,
            )
            self.session.add(definition)
            self.session.flush()
        max_version = self.session.scalar(select(func.max(db.ScheduleVersion.version_number)).where(
            db.ScheduleVersion.schedule_definition_id == definition.id,
        )) or 0
        schedule.schedule_metadata.version_number = max_version + 1
        schedule.schedule_metadata.protocol_version_id = run.protocol_version_id
        ScheduleRepository(self.session).persist_draft(
            schedule, schedule_definition_id=definition.id, extraction_run_id=run.id,
        )
        return schedule.schedule_version_id

    def complete_many(
        self, run_id: UUID, results: list[tuple[dict[str, Any] | None, ExtractionResult]],
    ) -> list[UUID]:
        """Persist every schedule one run produced - one per independent
        Schedule of Assessments the document printed (see
        runner.extract_schedules) - as its own draft version,
        then mark the run COMPLETED once.

        A run that produced no usable schedule at all (every option failed)
        is marked FAILED with every option's issues attached, same as a
        single-schedule run that failed outright.
        """
        run = self.session.get(db.ExtractionRun, run_id, with_for_update=True)
        if run is None:
            raise KeyError("extraction run not found")
        if run.status not in {"QUEUED", "RUNNING", "PARTIAL"}:
            raise ValueError(f"cannot complete extraction in {run.status} state")
        # The accountability ledgers (source coverage, statement dispositions)
        # are stored on the run itself - JSON on an existing row - so the
        # review screen can read them for the draft the run produced
        # (ScheduleVersion.extraction_run_id -> this run).
        entries = [
            {"option": option, "nodes": result.extraction_trace,
             "accountability": result.accountability} for option, result in results
        ]
        run.trace = {"options": entries}
        run.completed_at = utc_now()

        version_ids: list[UUID] = []
        empty: list[dict[str, Any]] = []
        for index, (option, result) in enumerate(results):
            if result.schedule is None:
                continue
            if not result.schedule.events and result.accountability is not None:
                # Never an empty-success draft from the document reader: "no
                # visits" is indistinguishable from "the protocol has no
                # visits" (AT-20). Legacy translation paths keep their contract.
                empty.append({"option": option, "issues": [
                    item.model_dump(mode="json") for item in result.issues]})
                continue
            version_id = self._persist_one(run, result)
            version_ids.append(version_id)
            entries[index]["schedule_version_id"] = str(version_id)
        # Reassigned (not mutated in place) so the JSON column is re-serialised
        # with the version links even after an intermediate flush.
        run.trace = {"options": [dict(entry) for entry in entries]}

        if not version_ids:
            run.status = "FAILED"
            run.error_details = {"issues": [
                item.model_dump(mode="json")
                for _option, result in results for item in result.issues
            ], "empty_schedules": len(empty)}
            raise ValueError("extraction did not produce any schema-valid schedule with visits")

        failed_options = [
            {"option": option, "issues": [item.model_dump(mode="json") for item in result.issues]}
            for option, result in results if result.schedule is None
        ] + empty
        run.status = "PARTIAL" if failed_options else "COMPLETED"
        run.error_details = {"failed_options": failed_options} if failed_options else None
        self.session.add(db.AuditEvent(
            organization_id=run.organization_id, action="SCHEDULE_EXTRACTION_COMPLETED",
            entity_type="EXTRACTION_RUN", entity_id=run.id,
            before=None, after={
                "schedule_version_ids": [str(item) for item in version_ids],
                "issues": sum(len(result.issues) for _option, result in results),
            },
        ))
        return version_ids

    def complete(self, run_id: UUID, result: ExtractionResult) -> UUID:
        return self.complete_many(run_id, [(None, result)])[0]

    def accountability_for_version(self, schedule_version_id: UUID) -> dict[str, Any] | None:
        """The coverage / statement ledgers stored with a draft version, for
        the review screen. None when the version was not produced by a run
        that recorded them."""
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None or version.extraction_run_id is None:
            return None
        run = self.session.get(db.ExtractionRun, version.extraction_run_id)
        options = (run.trace or {}).get("options") if run is not None else None
        matches = [entry for entry in options or [] if isinstance(entry, dict) and entry.get("accountability")]
        for entry in matches:
            if entry.get("schedule_version_id") == str(schedule_version_id):
                return entry["accountability"]
        return matches[0]["accountability"] if len(matches) == 1 else None

    def record_statement_decisions(
        self,
        schedule_version_id: UUID,
        *,
        statement_ids: list[str],
        actor_id: UUID,
        reason: str,
        action: str,
        question_id: str | None = None,
        resolved_issue_codes: list[str] | None = None,
    ) -> list[str]:
        """Record a reviewer decision that releases statements a question held.

        A statement left PENDING_INTERPRETATION only because of the issue(s) a
        reviewer has now answered becomes COMPILED *by reviewer decision*. The
        model's own disposition and reason are kept in ``disposition_history``,
        the decision is appended to the ledger's ``reviewer_decisions`` (the
        ledger ``revision`` counts them), the summary is recomputed, and an
        audit event records before/after. A statement with any other blocking
        code, or with no compiled rule to point at, is NOT released.
        Returns the ids actually released.
        """
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None or version.extraction_run_id is None or not statement_ids:
            return []
        run = self.session.get(db.ExtractionRun, version.extraction_run_id)
        if run is None:
            return []
        trace = dict(run.trace or {})
        options = [dict(item) if isinstance(item, dict) else item for item in trace.get("options") or []]
        matches = [index for index, item in enumerate(options)
                   if isinstance(item, dict) and item.get("accountability")]
        target = next((index for index in matches
                       if options[index].get("schedule_version_id") == str(schedule_version_id)), None)
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
                "superseded_at": now, "superseded_by_decision": decision_id,
            }]
            statement["disposition"] = "COMPILED"
            statement["decided_by"] = "REVIEWER"
            statement["reason"] = f"Released by reviewer decision ({action}): {reason}"
            statement["reviewer_decision"] = {
                "id": decision_id, "action": action, "question_id": question_id,
                "reviewer_id": str(actor_id), "decided_at": now, "rationale": reason,
            }
            released.append(str(statement["statement_id"]))
        anchors = dict(ledger.get("anchors") or {})
        if question_id and anchors.get("unmatched"):
            anchors["unmatched"] = [
                {**item, "resolution": {"decision_id": decision_id, "action": action, "decided_at": now}}
                if isinstance(item, dict) and item.get("question_id") == question_id else item
                for item in anchors["unmatched"]]
            ledger["anchors"] = anchors
        ledger["statements"] = statements
        counts: dict[str, int] = {}
        for statement in statements:
            if isinstance(statement, dict):
                counts[statement.get("disposition")] = counts.get(statement.get("disposition"), 0) + 1
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
            "resolved_issue_codes": sorted(resolved_codes), "released_statement_ids": released,
        }]
        ledger["revision"] = int(ledger.get("revision") or 0) + 1
        options[target] = {**options[target], "accountability": ledger}
        trace["options"] = options
        run.trace = trace  # reassigned so the JSON column is written
        self.session.add(db.AuditEvent(
            organization_id=run.organization_id, actor_id=actor_id,
            action="EXTRACTION_STATEMENT_DISPOSITION_DECIDED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=before or None,
            after={"decision_id": decision_id, "action": action, "question_id": question_id,
                   "released_statement_ids": released, "ledger_revision": ledger["revision"]},
            reason=reason,
        ))
        self.session.flush()
        return released

    def start(self, run_id: UUID) -> db.ExtractionRun:
        """QUEUED -> RUNNING. Split out from `execute` so a caller that needs
        to build the extraction pipeline itself (runner.execute_run, to fan
        out over several schedule options) can do so after the run is marked
        RUNNING but before any model call happens, instead of this class
        owning the whole pipeline construction.
        """
        run = self.session.get(db.ExtractionRun, run_id, with_for_update=True)
        if run is None:
            raise KeyError("extraction run not found")
        if run.status != "QUEUED":
            raise ValueError(f"cannot execute extraction in {run.status} state")
        run.status = "RUNNING"
        run.started_at = utc_now()
        self.session.flush()
        return run

    def fail(self, run_id: UUID, error: Exception) -> None:
        run = self.session.get(db.ExtractionRun, run_id, with_for_update=True)
        if run is not None and run.status != "COMPLETED":
            run.status = "FAILED"
            run.completed_at = utc_now()
            run.error_details = {"type": type(error).__name__, "message": str(error)}
