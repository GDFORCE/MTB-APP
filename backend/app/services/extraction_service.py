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
        run.trace = {"options": [
            {"option": option, "nodes": result.extraction_trace} for option, result in results
        ]}
        run.completed_at = utc_now()

        version_ids: list[UUID] = []
        for _option, result in results:
            if result.schedule is None:
                continue
            version_ids.append(self._persist_one(run, result))

        if not version_ids:
            run.status = "FAILED"
            run.error_details = {"issues": [
                item.model_dump(mode="json")
                for _option, result in results for item in result.issues
            ]}
            raise ValueError("extraction did not produce any schema-valid schedule")

        failed_options = [
            {"option": option, "issues": [item.model_dump(mode="json") for item in result.issues]}
            for option, result in results if result.schedule is None
        ]
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
