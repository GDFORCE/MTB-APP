"""The amendment plane (P6) on MongoDB.

Unique indexes (migration 0009) are what make concurrent writers safe: one pair per
ordered (organization, from, to), one mapping set per pair, one idempotency key per
patient, and ONE OPEN decision per patient. Guarded writes (revision / status filters)
turn a lost race into ``StaleRevisionError`` instead of a silent overwrite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.application.amendments import (
    AdoptionDecisionRecord,
    AmendmentMappingSetRecord,
    AmendmentPairRecord,
    AmendmentSimulationRecord,
    MappingDecisionRecord,
)
from app.application.errors import DuplicateRecordError, RecordNotFoundError, StaleRevisionError

from .codec import encode
from .documents import from_document, to_document
from .patient_repository import _Base

PAIRS = "amendment_pairs"
MAPPING_SETS = "amendment_mapping_sets"
MAPPING_DECISIONS = "amendment_mapping_decisions"
SIMULATIONS = "amendment_simulations"
DECISIONS = "amendment_decisions"


class MongoAmendmentRepository(_Base):
    # -- pairs -----------------------------------------------------------------
    def add_pair(self, record: AmendmentPairRecord) -> None:
        self._insert(PAIRS, record, "amendment pair")

    def get_pair(self, organization_id: UUID, pair_id: UUID) -> AmendmentPairRecord:
        doc = self._db[PAIRS].find_one(
            {"_id": pair_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("amendment pair not found")
        return from_document(AmendmentPairRecord, doc)

    def find_pair(self, organization_id, from_schedule_version_id, to_schedule_version_id):
        doc = self._db[PAIRS].find_one(
            {"organization_id": organization_id,
             "from_schedule_version_id": from_schedule_version_id,
             "to_schedule_version_id": to_schedule_version_id}, session=self._session)
        return from_document(AmendmentPairRecord, doc) if doc else None

    def list_pairs_for_trial(self, organization_id: UUID, trial_id: UUID):
        return self._list(PAIRS, AmendmentPairRecord,
                          {"organization_id": organization_id, "trial_id": trial_id},
                          [("created_at", ASCENDING), ("_id", ASCENDING)])

    # -- mapping sets and review decisions ----------------------------------------
    def add_mapping_set(self, record: AmendmentMappingSetRecord) -> None:
        self._insert(MAPPING_SETS, record, "mapping set")

    def get_mapping_set(self, organization_id: UUID, pair_id: UUID) -> AmendmentMappingSetRecord:
        doc = self._db[MAPPING_SETS].find_one(
            {"pair_id": pair_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("mapping set not found")
        return from_document(AmendmentMappingSetRecord, doc)

    def save_mapping_set(self, record: AmendmentMappingSetRecord, *, expected_revision: int):
        document = to_document(record)
        document.pop("_id")
        document["revision"] = expected_revision + 1
        document["updated_at"] = datetime.now(timezone.utc)
        saved = self._db[MAPPING_SETS].find_one_and_replace(
            {"_id": record.id, "organization_id": record.organization_id,
             "revision": expected_revision},
            document, return_document=ReturnDocument.AFTER, session=self._session)
        if saved is None:
            if self._db[MAPPING_SETS].find_one(
                    {"_id": record.id, "organization_id": record.organization_id},
                    {"_id": 1}, session=self._session) is None:
                raise RecordNotFoundError("mapping set not found")
            raise StaleRevisionError("the mapping set changed since it was read")
        return from_document(AmendmentMappingSetRecord, saved)

    def add_mapping_decision(self, record: MappingDecisionRecord) -> None:
        self._insert(MAPPING_DECISIONS, record, "mapping decision")

    def list_mapping_decisions(self, organization_id: UUID, pair_id: UUID):
        return self._list(MAPPING_DECISIONS, MappingDecisionRecord,
                          {"organization_id": organization_id, "pair_id": pair_id},
                          [("created_at", ASCENDING), ("_id", ASCENDING)])

    # -- simulations -------------------------------------------------------------------
    def add_simulation(self, record: AmendmentSimulationRecord) -> None:
        self._insert(SIMULATIONS, record, "simulation")

    def get_simulation(self, organization_id: UUID, simulation_id: UUID):
        doc = self._db[SIMULATIONS].find_one(
            {"_id": simulation_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("simulation not found")
        return from_document(AmendmentSimulationRecord, doc)

    def set_simulation_status(
        self, organization_id: UUID, simulation_id: UUID, to_status: str, *,
        expected: set[str], consumed_by_decision_id: UUID | None = None,
    ):
        update: dict[str, Any] = {"status": str(to_status)}
        if consumed_by_decision_id is not None:
            update["consumed_by_decision_id"] = consumed_by_decision_id
        saved = self._db[SIMULATIONS].find_one_and_update(
            {"_id": simulation_id, "organization_id": organization_id,
             "status": {"$in": sorted(str(item) for item in expected)}},
            {"$set": update}, return_document=ReturnDocument.AFTER, session=self._session)
        if saved is None:
            if self._db[SIMULATIONS].find_one(
                    {"_id": simulation_id, "organization_id": organization_id},
                    {"_id": 1}, session=self._session) is None:
                raise RecordNotFoundError("simulation not found")
            raise StaleRevisionError("the simulation is no longer in the expected state")
        return from_document(AmendmentSimulationRecord, saved)

    def list_simulations_for_patient(self, organization_id: UUID, patient_id: UUID):
        return self._list(SIMULATIONS, AmendmentSimulationRecord,
                          {"organization_id": organization_id, "patient_id": patient_id},
                          [("created_at", ASCENDING), ("_id", ASCENDING)])

    # -- adoption decisions -----------------------------------------------------------------
    def add_decision(self, record: AdoptionDecisionRecord) -> None:
        self._insert(DECISIONS, record, "amendment decision")

    def get_decision(self, organization_id: UUID, decision_id: UUID) -> AdoptionDecisionRecord:
        doc = self._db[DECISIONS].find_one(
            {"_id": decision_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("amendment decision not found")
        return from_document(AdoptionDecisionRecord, doc)

    def save_decision(self, record: AdoptionDecisionRecord, *, expected_revision: int):
        document = to_document(record)
        document.pop("_id")
        document["revision"] = expected_revision + 1
        document["updated_at"] = datetime.now(timezone.utc)
        try:
            saved = self._db[DECISIONS].find_one_and_replace(
                {"_id": record.id, "organization_id": record.organization_id,
                 "revision": expected_revision},
                encode(document), return_document=ReturnDocument.AFTER, session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(
                "the patient already has an open amendment decision",
                key=str(record.patient_id)) from error
        if saved is None:
            if self._db[DECISIONS].find_one(
                    {"_id": record.id, "organization_id": record.organization_id},
                    {"_id": 1}, session=self._session) is None:
                raise RecordNotFoundError("amendment decision not found")
            raise StaleRevisionError("the amendment decision changed since it was read")
        return from_document(AdoptionDecisionRecord, saved)

    def find_decision_by_idempotency(self, organization_id, patient_id, idempotency_key):
        doc = self._db[DECISIONS].find_one(
            {"organization_id": organization_id, "patient_id": patient_id,
             "idempotency_key": idempotency_key}, session=self._session)
        return from_document(AdoptionDecisionRecord, doc) if doc else None

    def find_open_decision(self, organization_id: UUID, patient_id: UUID):
        doc = self._db[DECISIONS].find_one(
            {"organization_id": organization_id, "patient_id": patient_id, "is_open": True},
            session=self._session)
        return from_document(AdoptionDecisionRecord, doc) if doc else None

    def list_decisions_for_patient(self, organization_id: UUID, patient_id: UUID):
        return self._list(DECISIONS, AdoptionDecisionRecord,
                          {"organization_id": organization_id, "patient_id": patient_id},
                          [("created_at", ASCENDING), ("_id", ASCENDING)])

    def list_open_decisions(self, organization_id: UUID, *, trial_id: UUID | None = None,
                            limit: int = 100):
        query: dict[str, Any] = {"organization_id": organization_id, "is_open": True}
        if trial_id is not None:
            query["trial_id"] = trial_id
        return self._list(DECISIONS, AdoptionDecisionRecord, query,
                          [("created_at", ASCENDING), ("_id", ASCENDING)])[:limit]

    def list_pending_execution(self, *, limit: int = 100, now=None):
        from datetime import datetime, timezone

        moment = now or datetime.now(timezone.utc)
        cursor = self._db[DECISIONS].find(
            {"is_open": True, "$or": [
                {"status": "WAITING"},
                {"status": "EXECUTING", "lease_expires_at": {"$lte": moment}},
                {"status": "EXECUTING", "lease_expires_at": None}]},
            session=self._session,
        ).sort([("last_checked_at", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)]
               ).limit(max(1, int(limit)))
        return [from_document(AdoptionDecisionRecord, doc) for doc in cursor]
