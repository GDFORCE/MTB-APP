"""In-memory amendment repository (P6): same uniqueness and guard rules as the Mongo
indexes, so the shared contract suite proves them on both."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.application.amendments import (
    AdoptionDecisionRecord,
    AmendmentMappingSetRecord,
    AmendmentPairRecord,
    AmendmentSimulationRecord,
    MappingDecisionRecord,
)
from app.application.errors import DuplicateRecordError, RecordNotFoundError, StaleRevisionError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryAmendmentRepository:
    def __init__(self, store: Any):
        self._s = store

    # -- pairs ---------------------------------------------------------------
    def add_pair(self, record: AmendmentPairRecord) -> None:
        for row in self._s.amendment_pairs.values():
            if row.id == record.id or (
                    row.organization_id == record.organization_id
                    and row.from_schedule_version_id == record.from_schedule_version_id
                    and row.to_schedule_version_id == record.to_schedule_version_id):
                raise DuplicateRecordError("amendment pair already exists", key=str(record.id))
        self._s.amendment_pairs[record.id] = record.model_copy(deep=True)

    def get_pair(self, organization_id: UUID, pair_id: UUID) -> AmendmentPairRecord:
        row = self._s.amendment_pairs.get(pair_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("amendment pair not found")
        return row.model_copy(deep=True)

    def find_pair(self, organization_id, from_schedule_version_id, to_schedule_version_id):
        for row in self._s.amendment_pairs.values():
            if (row.organization_id, row.from_schedule_version_id, row.to_schedule_version_id) == (
                    organization_id, from_schedule_version_id, to_schedule_version_id):
                return row.model_copy(deep=True)
        return None

    def list_pairs_for_trial(self, organization_id: UUID, trial_id: UUID):
        rows = [row for row in self._s.amendment_pairs.values()
                if row.organization_id == organization_id and row.trial_id == trial_id]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    # -- mapping sets and review decisions ---------------------------------------
    def add_mapping_set(self, record: AmendmentMappingSetRecord) -> None:
        if record.id in self._s.amendment_mapping_sets or any(
                row.pair_id == record.pair_id for row in self._s.amendment_mapping_sets.values()):
            raise DuplicateRecordError("mapping set already exists", key=str(record.pair_id))
        self._s.amendment_mapping_sets[record.id] = record.model_copy(deep=True)

    def get_mapping_set(self, organization_id: UUID, pair_id: UUID) -> AmendmentMappingSetRecord:
        for row in self._s.amendment_mapping_sets.values():
            if row.pair_id == pair_id and row.organization_id == organization_id:
                return row.model_copy(deep=True)
        raise RecordNotFoundError("mapping set not found")

    def save_mapping_set(self, record: AmendmentMappingSetRecord, *, expected_revision: int):
        current = self._s.amendment_mapping_sets.get(record.id)
        if current is None or current.organization_id != record.organization_id:
            raise RecordNotFoundError("mapping set not found")
        if current.revision != expected_revision:
            raise StaleRevisionError("the mapping set changed since it was read")
        saved = record.model_copy(
            update={"revision": expected_revision + 1, "updated_at": _now()}, deep=True)
        self._s.amendment_mapping_sets[record.id] = saved
        return saved.model_copy(deep=True)

    def add_mapping_decision(self, record: MappingDecisionRecord) -> None:
        if any(row.id == record.id for row in self._s.amendment_mapping_decisions):
            raise DuplicateRecordError("mapping decision already exists", key=str(record.id))
        self._s.amendment_mapping_decisions.append(record.model_copy(deep=True))

    def list_mapping_decisions(self, organization_id: UUID, pair_id: UUID):
        rows = [row for row in self._s.amendment_mapping_decisions
                if row.organization_id == organization_id and row.pair_id == pair_id]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    # -- simulations --------------------------------------------------------------
    def add_simulation(self, record: AmendmentSimulationRecord) -> None:
        if record.id in self._s.amendment_simulations:
            raise DuplicateRecordError("simulation already exists", key=str(record.id))
        self._s.amendment_simulations[record.id] = record.model_copy(deep=True)

    def get_simulation(self, organization_id: UUID, simulation_id: UUID):
        row = self._s.amendment_simulations.get(simulation_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("simulation not found")
        return row.model_copy(deep=True)

    def set_simulation_status(
        self, organization_id: UUID, simulation_id: UUID, to_status: str, *,
        expected: set[str], consumed_by_decision_id: UUID | None = None,
    ):
        row = self._s.amendment_simulations.get(simulation_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("simulation not found")
        if str(row.status) not in {str(item) for item in expected}:
            raise StaleRevisionError(f"the simulation is {row.status}, not {sorted(map(str, expected))}")
        saved = row.model_copy(update={
            "status": to_status,
            "consumed_by_decision_id": consumed_by_decision_id or row.consumed_by_decision_id})
        self._s.amendment_simulations[simulation_id] = saved
        return saved.model_copy(deep=True)

    def list_simulations_for_patient(self, organization_id: UUID, patient_id: UUID):
        rows = [row for row in self._s.amendment_simulations.values()
                if row.organization_id == organization_id and row.patient_id == patient_id]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    # -- adoption decisions ---------------------------------------------------------
    def add_decision(self, record: AdoptionDecisionRecord) -> None:
        for row in self._s.amendment_decisions.values():
            if row.id == record.id:
                raise DuplicateRecordError("decision already exists", key=str(record.id))
            if (record.idempotency_key and row.organization_id == record.organization_id
                    and row.patient_id == record.patient_id
                    and row.idempotency_key == record.idempotency_key):
                raise DuplicateRecordError(
                    "decision idempotency key already used", key=record.idempotency_key)
            if (record.is_open and row.is_open and row.patient_id == record.patient_id
                    and row.organization_id == record.organization_id):
                raise DuplicateRecordError(
                    "the patient already has an open amendment decision", key=str(record.patient_id))
        self._s.amendment_decisions[record.id] = record.model_copy(deep=True)

    def get_decision(self, organization_id: UUID, decision_id: UUID) -> AdoptionDecisionRecord:
        row = self._s.amendment_decisions.get(decision_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("amendment decision not found")
        return row.model_copy(deep=True)

    def save_decision(self, record: AdoptionDecisionRecord, *, expected_revision: int):
        current = self._s.amendment_decisions.get(record.id)
        if current is None or current.organization_id != record.organization_id:
            raise RecordNotFoundError("amendment decision not found")
        if current.revision != expected_revision:
            raise StaleRevisionError("the amendment decision changed since it was read")
        if record.is_open and any(
                row.is_open and row.patient_id == record.patient_id and row.id != record.id
                and row.organization_id == record.organization_id
                for row in self._s.amendment_decisions.values()):
            raise DuplicateRecordError(
                "the patient already has an open amendment decision", key=str(record.patient_id))
        saved = record.model_copy(
            update={"revision": expected_revision + 1, "updated_at": _now()}, deep=True)
        self._s.amendment_decisions[record.id] = saved
        return saved.model_copy(deep=True)

    def find_decision_by_idempotency(self, organization_id, patient_id, idempotency_key):
        for row in self._s.amendment_decisions.values():
            if (row.organization_id, row.patient_id, row.idempotency_key) == (
                    organization_id, patient_id, idempotency_key):
                return row.model_copy(deep=True)
        return None

    def find_open_decision(self, organization_id: UUID, patient_id: UUID):
        for row in self._s.amendment_decisions.values():
            if row.organization_id == organization_id and row.patient_id == patient_id and row.is_open:
                return row.model_copy(deep=True)
        return None

    def list_decisions_for_patient(self, organization_id: UUID, patient_id: UUID):
        rows = [row for row in self._s.amendment_decisions.values()
                if row.organization_id == organization_id and row.patient_id == patient_id]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    def list_open_decisions(self, organization_id: UUID, *, trial_id: UUID | None = None,
                            limit: int = 100):
        rows = [row for row in self._s.amendment_decisions.values()
                if row.organization_id == organization_id and row.is_open
                and (trial_id is None or row.trial_id == trial_id)]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def list_pending_execution(self, *, limit: int = 100, now=None):
        from datetime import datetime, timezone

        moment = now or datetime.now(timezone.utc)

        def runnable(row) -> bool:
            status = str(getattr(row.status, "value", row.status))
            if not row.is_open:
                return False
            if status == "WAITING":
                return True
            expires = row.lease_expires_at
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return status == "EXECUTING" and (expires is None or expires <= moment)

        floor = datetime.min.replace(tzinfo=timezone.utc)

        def order(row):
            checked = row.last_checked_at
            if checked is not None and checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            return (checked is not None, checked or floor, row.created_at, str(row.id))

        rows = sorted((row for row in self._s.amendment_decisions.values() if runnable(row)), key=order)
        return [row.model_copy(deep=True) for row in rows[:limit]]
