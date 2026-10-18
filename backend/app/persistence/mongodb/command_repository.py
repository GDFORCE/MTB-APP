"""The append-only patient command ledger (P5-03) on MongoDB.

Unique indexes (see migration 0008) are what make concurrent writers safe:
``(patient_id, sequence)`` serialises two commands calculated against the same
base, and ``(organization_id, patient_id, idempotency_key)`` makes one key one
command whatever the UI did. The repository only inserts and reads.
"""

from __future__ import annotations

from uuid import UUID

from pymongo import ASCENDING

from app.application.errors import RecordNotFoundError
from app.application.patients import PatientCommandRecord

from .documents import from_document
from .patient_repository import _Base

COMMANDS = "patient_commands"


class MongoPatientCommandRepository(_Base):
    def add(self, record: PatientCommandRecord) -> None:
        self._insert(COMMANDS, record, "patient command")

    def get(self, organization_id: UUID, command_id: UUID) -> PatientCommandRecord:
        doc = self._db[COMMANDS].find_one(
            {"_id": command_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("patient command not found")
        return from_document(PatientCommandRecord, doc)

    def list_for_patient(self, organization_id: UUID, patient_id: UUID) -> list[PatientCommandRecord]:
        return self._list(
            COMMANDS, PatientCommandRecord,
            {"organization_id": organization_id, "patient_id": patient_id},
            [("sequence", ASCENDING)])

    def find_by_idempotency(
        self, organization_id: UUID, patient_id: UUID, idempotency_key: str,
    ) -> PatientCommandRecord | None:
        doc = self._db[COMMANDS].find_one(
            {"organization_id": organization_id, "patient_id": patient_id,
             "idempotency_key": idempotency_key}, session=self._session)
        return from_document(PatientCommandRecord, doc) if doc else None
