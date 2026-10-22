"""Versioned committed calendar read in one repository transaction (P10-01).

No scheduling logic is reimplemented here. Facts, version/pointer and the shared
calendar are read from the same snapshot; a concurrent amendment cannot mix planes.
"""
from uuid import UUID

from .patient_schedule import PatientScheduleService
from .ports import UnitOfWork

PROJECTION_VERSION = "uctsm-projection.v1"


class ProjectionSnapshotService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def get(self, *, organization_id: UUID, trial_id: UUID, external_patient_id: str) -> dict:
        def read(tx):
            patient = tx.patients.find_by_external_id(organization_id, trial_id, external_patient_id)
            if patient is None:
                raise KeyError("patient not found")
            version = patient.current_schedule_version_id
            pointer = tx.evaluations.get_pointer(patient.id, version) if version else None
            calendar = PatientScheduleService(self.uow).calendar_in(
                tx, patient.id, organization_id=organization_id)
            pending = tx.facts.list_anchors(patient.id, statuses={"PENDING_CONFIRMATION"})
            return {"projection_version": PROJECTION_VERSION,
                    "patient_id": patient.id, "trial_id": trial_id,
                    "authority_plane": patient.authority_plane, "schedule_version_id": version,
                    "evaluation_id": pointer.current_evaluation_id if pointer else None,
                    "revision": pointer.revision if pointer else None, "calendar": calendar,
                    "pending_anchor_candidates": [
                        {"id": row.id, "anchor_code": row.anchor_code, "value_date": row.value_date,
                         "source_type": row.source_type, "status": row.status} for row in pending]}
        # A normal read() has no Mongo session; this read-only transaction supplies one snapshot.
        return self.uow.run(read)
