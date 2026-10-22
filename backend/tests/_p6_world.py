"""A synthetic amendment world for the P6 tests: the pilot trial, its approved Pilot 1.0
schedule, an approved Amendment 1 (Pilot 2.0) that declares it amends Pilot 1.0, two
native patients on Pilot 1.0, and helpers to review the mapping and record facts.

Everything is synthetic; nothing here is a real protocol or approval."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.application.amendment_adoption import AdoptionService
from app.application.amendment_service import AmendmentService
from app.application.patient_schedule import PatientScheduleService
from app.application.pilot import PilotService
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.models import OccurrenceFactKind

TODAY = date(2026, 10, 16)
HORIZON = date(2027, 12, 31)


def immediately() -> AdoptionPoint:
    return AdoptionPoint(mode=AdoptionMode.ADOPT_IMMEDIATELY)


def remain() -> AdoptionPoint:
    return AdoptionPoint(mode=AdoptionMode.REMAIN_ON_V1)


class World:
    def __init__(self, uow, *, reviewed: bool = False, builder=None):
        self.uow = uow
        self.org, self.editor, self.approver, self.reviewer, self.pi = (
            uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
        pilot = PilotService(uow)
        base = pilot.seed(organization_id=self.org, editor_id=self.editor, approver_id=self.approver)
        self.trial_id = UUID(base["trial_id"])
        self.patient_ids = [UUID(row["patient_id"]) for row in base["patients"]]
        amended = pilot.seed_amendment(
            organization_id=self.org, editor_id=self.editor, approver_id=self.approver,
            builder=builder)
        self.v1 = UUID(amended["from_schedule_version_id"])
        self.v2 = UUID(amended["to_schedule_version_id"])
        self.authoring = AmendmentService(uow)
        self.adoption = AdoptionService(uow, clock=lambda: TODAY)
        self.patients = PatientScheduleService(uow)
        self.pair = self.authoring.prepare_pair(
            organization_id=self.org, actor_id=self.reviewer,
            from_schedule_version_id=self.v1, to_schedule_version_id=self.v2)
        if reviewed:
            self.review_all()

    # -- mapping -------------------------------------------------------------------------
    def review_all(self) -> dict:
        view = self.authoring.pair_view(organization_id=self.org, pair_id=UUID(self.pair["pair_id"]))
        for row in view["mappings"]:
            if row["review_status"] == "PROPOSED":
                view = self.authoring.review_mapping(
                    organization_id=self.org, actor_id=self.reviewer,
                    pair_id=UUID(self.pair["pair_id"]), expected_revision=view["mapping_set_revision"],
                    action="CONFIRM", mapping_id=UUID(row["id"]), reason="same imaging visit, re-coded")
        self.pair = view
        return view

    # -- facts -----------------------------------------------------------------------------
    def record(self, patient_id, logical_key, visit, *, treatment=None, kind="PERFORMED"):
        return self.patients.record_actual_occurrence(
            patient_id, organization_id=self.org, logical_key=logical_key,
            kind=OccurrenceFactKind(kind), visit_date=visit, treatment_date=treatment,
            actor_id=self.pi, reason="synthetic", horizon=HORIZON, today=TODAY)

    def patient(self, patient_id=None):
        return self.uow.read(lambda tx: tx.patients.get(self.org, patient_id or self.patient_ids[0]))

    def events(self, patient_id=None, version=None):
        patient = self.patient(patient_id)
        version = version or patient.current_schedule_version_id
        return self.uow.read(lambda tx: self.patients.current_events(tx, patient.id, version))

    def simulate(self, patient_id=None, point=None, direction="ADOPT", to=None):
        return self.adoption.simulate(
            organization_id=self.org, actor_id=self.pi, patient_id=patient_id or self.patient_ids[0],
            direction=direction, point=point or immediately(),
            to_schedule_version_id=(to or self.v2) if direction == "ADOPT" else None,
            horizon=HORIZON, as_of=TODAY)

    def confirm(self, simulation, decision="ADOPT", **extra):
        return self.adoption.confirm(
            organization_id=self.org, actor_id=self.pi, simulation_id=UUID(simulation["simulation_id"]),
            decision=decision, reason=extra.pop("reason", "synthetic decision"), **extra)
