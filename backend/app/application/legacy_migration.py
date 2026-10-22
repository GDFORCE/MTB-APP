"""Resumable, idempotent legacy-patient -> UCTSM plane migration with reconciliation (P11-02).

The job only ENROLS patients into the canonical Mongo plane (the existing enrolment bridge) and
compares the result with the legacy schedule through the existing parity service. It never
flips ``UCTSM_AUTHORITATIVE``, a trial read mode or any legacy collection; legacy data is read,
never written. ``dry_run`` (the default) plans without writing anything but the returned report.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Protocol
from uuid import UUID

from .errors import RecordNotFoundError
from .migration_records import MigrationRunRecord, SourceMappingRecord
from .operational_bridge import OperationalBridgeService
from .parity import ParityService
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

SOURCE_SYSTEM = "LEGACY_MONGO"


@dataclass(frozen=True)
class LegacyPatient:
    legacy_id: str
    subject_id: str | None
    baseline_date: date | None
    schedule_definition_id: UUID | None = None
    schedule_version_id: UUID | None = None
    arm_label: str | None = None
    cohort_label: str | None = None
    population_label: str | None = None
    dimension_values: dict[str, list[str]] = field(default_factory=dict)
    anchor_dates: dict[str, date] = field(default_factory=dict)
    anchor_date_sources: dict[str, str] = field(default_factory=dict)
    visits: tuple[dict[str, Any], ...] = ()

    def source_hash(self) -> str:
        """Hash of every source field that drives the migration, so a changed legacy record is re-migrated."""
        body = {"id": self.legacy_id, "subject": self.subject_id,
                "baseline": self.baseline_date.isoformat() if self.baseline_date else None,
                "def": str(self.schedule_definition_id), "ver": str(self.schedule_version_id),
                "arm": self.arm_label, "cohort": self.cohort_label, "population": self.population_label,
                "dims": self.dimension_values,
                "anchors": {key: value.isoformat() for key, value in sorted(self.anchor_dates.items())},
                "sources": dict(sorted(self.anchor_date_sources.items())),
                "visits": [{key: str(value) for key, value in sorted(item.items())} for item in self.visits]}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LegacySource(Protocol):
    def list_patients(self, external_trial_id: str, *, after: str | None, limit: int) -> list[LegacyPatient]:
        """Patients ordered by legacy id, strictly after ``after``. Read-only."""
        ...


@dataclass
class MigrationReport:
    run_key: str
    dry_run: bool
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"run_key": self.run_key, "dry_run": self.dry_run, "status": self.status,
                "counts": dict(self.counts), "items": list(self.items)}


class LegacyMigrationJob:
    def __init__(self, uow: UnitOfWork, source: LegacySource):
        self._uow, self._source = uow, source
        self._bridge = OperationalBridgeService(uow)
        self._parity = ParityService(uow)

    def run(self, *, organization_id: UUID, actor_id: UUID, canonical_trial_id: UUID,
            schedule_definition_id: UUID, external_trial_id: str, run_key: str,
            dry_run: bool = True, batch_size: int = 50, max_batches: int | None = None,
            horizon_days: int = 366, today: date | None = None) -> MigrationReport:
        if not 1 <= batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        today = today or date.today()
        run = self._uow.read(lambda tx: tx.migrations.get_run(organization_id, run_key))
        if run is not None and run.trial_id != canonical_trial_id:
            raise ValueError("this run key belongs to another trial")
        if run is None:
            run = MigrationRunRecord(
                organization_id=organization_id, run_key=run_key, trial_id=canonical_trial_id,
                external_trial_id=external_trial_id, source_system=SOURCE_SYSTEM, started_by=actor_id)
        # An unfinished run resumes at its checkpoint; a finished one starts a fresh pass (so changed
        # source records are noticed) whose unchanged, reconciled patients are cheaply skipped.
        resuming = not dry_run and run.status == "RUNNING"
        if not resuming:
            run.cursor, run.counts, run.status = None, {}, "RUNNING"
        report = MigrationReport(run_key=run_key, dry_run=dry_run, status="RUNNING",
                                 counts=dict(run.counts) if resuming else {})
        cursor = run.cursor if resuming else None
        batches = 0
        while True:
            patients = self._source.list_patients(external_trial_id, after=cursor, limit=batch_size)
            if not patients:
                break
            for patient in patients:
                item = self._handle(patient, organization_id=organization_id, actor_id=actor_id,
                                    canonical_trial_id=canonical_trial_id,
                                    schedule_definition_id=schedule_definition_id, run=run,
                                    dry_run=dry_run, horizon_days=horizon_days, today=today)
                report.items.append(item)
                report.counts[item["result"]] = report.counts.get(item["result"], 0) + 1
                cursor = patient.legacy_id
                if not dry_run:
                    run.cursor, run.counts, run.updated_at = cursor, dict(report.counts), utc_now()
                    run.revision += 1
                    self._uow.run(lambda tx: tx.migrations.save_run(run))      # checkpoint per patient
            batches += 1
            if max_batches is not None and batches >= max_batches:
                report.status = "RUNNING"
                return report
        failed = report.counts.get("FAILED", 0) + report.counts.get("DIFFERENCES", 0)
        report.status = "PARTIAL" if failed else "COMPLETED"
        if not dry_run:
            run.status, run.updated_at = report.status, utc_now()
            run.revision += 1

            def finish(tx):
                tx.migrations.save_run(run)
                tx.audit.append(AuditRecord(
                    organization_id=organization_id, actor_id=actor_id, action="LEGACY_MIGRATION_RUN_FINISHED",
                    entity_type="TRIAL", entity_id=canonical_trial_id, after={
                        "run_key": run_key, "status": report.status, "counts": report.counts}))
            self._uow.run(finish)
        return report

    def _handle(self, patient: LegacyPatient, *, organization_id, actor_id, canonical_trial_id,
                schedule_definition_id, run, dry_run, horizon_days, today) -> dict[str, Any]:
        digest = patient.source_hash()
        existing = self._uow.read(lambda tx: tx.migrations.get_mapping(
            organization_id, SOURCE_SYSTEM, patient.legacy_id))
        base = {"source_id": patient.legacy_id, "source_hash": digest}
        if existing and existing.source_hash == digest and existing.status in {"RECONCILED", "MIGRATED"}:
            return {**base, "result": "SKIPPED_UNCHANGED", "patient_id": str(existing.patient_id)}
        if patient.baseline_date is None:
            # Never default an unstated baseline: it is reported, not guessed.
            return self._fail(base, patient, organization_id, run, dry_run, "BASELINE_DATE_MISSING")
        if dry_run:
            return {**base, "result": "WOULD_MIGRATE" if existing is None else "WOULD_REMIGRATE_CHANGED_SOURCE"}
        try:
            horizon = max(patient.baseline_date, today) + timedelta(days=horizon_days)
            enrollment = self._bridge.enroll_patient(
                organization_id=organization_id, actor_id=actor_id, canonical_trial_id=canonical_trial_id,
                schedule_definition_id=patient.schedule_definition_id or schedule_definition_id,
                external_patient_id=patient.legacy_id, patient_code=patient.subject_id or patient.legacy_id,
                baseline_date=patient.baseline_date, horizon=horizon,
                arm_label=patient.arm_label, cohort_label=patient.cohort_label,
                population_label=patient.population_label, dimension_values=patient.dimension_values,
                anchor_dates=patient.anchor_dates, anchor_date_sources=patient.anchor_date_sources,
                expected_version_id=patient.schedule_version_id)
            report, _ = self._parity.compare_patient(
                enrollment.patient_id, organization_id=organization_id,
                legacy_visits=list(patient.visits), horizon=horizon, actor_id=actor_id, record=True)
        except (KeyError, ValueError, RecordNotFoundError) as error:
            return self._fail(base, patient, organization_id, run, dry_run, _code(error))
        reconciled = report.verdict.value == "MATCH"
        status = "RECONCILED" if reconciled else "DIFFERENCES"
        mapping = SourceMappingRecord(
            organization_id=organization_id, source_system=SOURCE_SYSTEM, source_id=patient.legacy_id,
            source_hash=digest, patient_id=enrollment.patient_id, status=status,
            parity_verdict=report.verdict.value, compared=report.compared, matched=report.matched,
            difference_codes=sorted({item.kind for item in report.differences if hasattr(item, "kind")}),
            run_id=run.id)
        self._uow.run(lambda tx: tx.migrations.save_mapping(mapping))
        return {**base, "result": status, "patient_id": str(enrollment.patient_id),
                "verdict": report.verdict.value, "compared": report.compared, "matched": report.matched}

    def _fail(self, base, patient, organization_id, run, dry_run, code) -> dict[str, Any]:
        item = {**base, "result": "FAILED", "error_code": code}
        if not dry_run:
            mapping = SourceMappingRecord(
                organization_id=organization_id, source_system=SOURCE_SYSTEM, source_id=patient.legacy_id,
                source_hash=base["source_hash"], status="FAILED", error_code=code, run_id=run.id)
            self._uow.run(lambda tx: tx.migrations.save_mapping(mapping))
        return item

    def reconciliation(self, organization_id: UUID) -> dict[str, Any]:
        """Totals over every source mapping: nothing is migrated that is not accounted for."""
        rows = self._uow.read(lambda tx: tx.migrations.list_mappings(organization_id, SOURCE_SYSTEM))
        totals: dict[str, int] = {}
        for row in rows:
            totals[row.status] = totals.get(row.status, 0) + 1
        return {"source_system": SOURCE_SYSTEM, "mappings": len(rows), "by_status": totals,
                "unreconciled": [row.source_id for row in rows if row.status != "RECONCILED"]}


def _code(error: Exception) -> str:
    """A stable code, never the message: errors can carry clinical content."""
    text = str(error).lower()
    for needle, code in (("schedule changed", "SCHEDULE_CHANGED"), ("pinned to another", "PINNED_TO_OTHER_VERSION"),
                         ("canonical trial", "TRIAL_NOT_LINKED"), ("enter these details", "REQUIRED_DETAILS_MISSING"), ("origin", "ANCHOR_UNSTATED"),
                         ("baseline", "BASELINE_INVALID")):
        if needle in text:
            return code
    return type(error).__name__.upper()
