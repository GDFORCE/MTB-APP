"""One authority decision for every schedule-changing writer of the command plane.

The red case from the audit: a patient persisted with ``authority_plane="SQL"``
had its command preview refused (409) while the OLDER ``actual-occurrences`` route
returned 201 and wrote an occurrence plus a ledger row. Memory AND real Mongo, at
service and HTTP level; denial leaves no partial effect.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _authz_fixtures import permissive_authorizer
from app.api.uctsm_mongo import create_uctsm_mongo_router
from app.application.authority import PlaneAuthorityError
from app.application.patient_commands import CommandRefusedError, PatientCommandService
from app.domain.schedule.commands import MarkMissed
from app.domain.schedule.models import OccurrenceFactKind, ScheduleStatus
from test_p5_tracks_domain import program
from test_uctsm_p1_occurrence_recalculation import ORG, Env

pytest_plugins = ["tests.contract.conftest"]

TODAY = date(2026, 3, 1)
ACTOR = uuid4()


def make(uow):
    schedule = program()
    schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
    env = Env(uow, schedule, baseline=date(2026, 1, 1))
    env.evaluate()
    return env


def set_authority(uow, env, plane):
    def work(tx):
        patient = tx.patients.get(ORG, env.patient_id)
        tx.patients.save(patient.model_copy(update={"authority_plane": plane}))
    uow.run(work)


def effects(uow, env):
    def read(tx):
        return (len(tx.ledger.list_for_patient(ORG, env.patient_id)),
                len(tx.facts.list_occurrences(env.patient_id)),
                len(tx.evaluations.list_evaluations(env.patient_id, env.version_id)),
                len(tx.outbox.list_pending(limit=1000)),
                len(tx.facts.list_states(env.patient_id)))
    return uow.read(read)


def client_for(uow):
    user = {"id": str(ACTOR), "organization_id": str(ORG), "role": "pi", "status": "Active"}
    app = FastAPI()
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow,
        authorizer=permissive_authorizer(str(ACTOR), str(ORG))))
    return TestClient(app)


def url(env, tail):
    return (f"/api/uctsm/mongo-plane/trials/{env.trial_id}"
            f"/patients/{env.external_patient_id}/{tail}")


def test_the_older_actual_route_is_refused_exactly_like_command_preview(uow):
    env = make(uow)
    set_authority(uow, env, "SQL")
    before = effects(uow, env)
    client = client_for(uow)
    body = {"logical_key": "C1D1:0", "kind": "PERFORMED", "visit_date": "2026-01-08",
            "reason": "attended", "horizon": "2026-12-31"}
    old = client.post(url(env, "actual-occurrences"), json=body, headers={"Idempotency-Key": "a"})
    assert old.status_code == 409
    assert old.json()["detail"]["code"] == "PLANE_NOT_AUTHORITATIVE"
    new = client.post(url(env, "commands/preview"), json={
        "type": "RECORD_ACTUAL", "logical_key": "C1D1:0", "visit_date": "2026-01-08",
        "effective_at": "2026-01-08", "reason": "attended", "horizon": "2026-12-31"})
    assert new.status_code == 409 and new.json()["detail"]["code"] == "PLANE_NOT_AUTHORITATIVE"
    assert effects(uow, env) == before                      # nothing written, nothing queued


def test_every_service_writer_enforces_the_same_decision(uow):
    env = make(uow)
    set_authority(uow, env, "SQL")
    before = effects(uow, env)
    service = env.service
    with pytest.raises(PlaneAuthorityError):
        service.record_actual_occurrence(
            env.patient_id, organization_id=ORG, logical_key="C1D1:0",
            kind=OccurrenceFactKind.PERFORMED, visit_date=date(2026, 1, 8), today=TODAY,
            actor_id=ACTOR, reason="x", idempotency_key="k")
    with pytest.raises(PlaneAuthorityError):
        service.record_occurrence_fact(
            env.patient_id, organization_id=ORG, logical_key="C1D1:0",
            kind=OccurrenceFactKind.PERFORMED, value=date(2026, 1, 8), actor_id=ACTOR)
    with pytest.raises(PlaneAuthorityError):
        service.record_activity(
            env.patient_id, organization_id=ORG, event_code="C1D1", occurrence_index=0,
            activity_code="A", status="NOT_DONE", actual_time=None, actor_id=ACTOR)
    with pytest.raises(PlaneAuthorityError):
        service.create_unscheduled_visit(
            env.patient_id, organization_id=ORG, event_code="C1D1", occurred_on=date(2026, 2, 1),
            reason="r", actor_id=ACTOR)
    assert effects(uow, env) == before


def test_authority_changing_after_preview_refuses_confirm_and_replay(uow):
    env = make(uow)
    service = PatientCommandService(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=MarkMissed(logical_key="DOSE:3"),
        effective_at=date(2026, 2, 1), reason="r", today=TODAY, horizon=date(2026, 12, 31))
    first = service.confirm(preview.proposal_id, organization_id=ORG, actor_id=ACTOR,
                            idempotency_key="same", today=TODAY)
    again = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=MarkMissed(logical_key="DOSE:3"),
        effective_at=date(2026, 2, 1), reason="r", today=TODAY, horizon=date(2026, 12, 31))
    set_authority(uow, env, "SQL")
    before = effects(uow, env)
    with pytest.raises(CommandRefusedError) as raised:        # replay after authority moved
        service.confirm(again.proposal_id, organization_id=ORG, actor_id=ACTOR,
                        idempotency_key="same", today=TODAY)
    assert raised.value.code == "PLANE_NOT_AUTHORITATIVE" and first.command_id
    assert effects(uow, env) == before


def test_readable_history_stays_available_after_authority_moves(uow):
    env = make(uow)
    service = PatientCommandService(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=MarkMissed(logical_key="DOSE:3"),
        effective_at=date(2026, 2, 1), reason="r", today=TODAY, horizon=date(2026, 12, 31))
    service.confirm(preview.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)
    set_authority(uow, env, "SQL")
    assert len(service.ledger(env.patient_id, organization_id=ORG)) == 1
    assert client_for(uow).get(url(env, "calendar")).status_code == 200


def test_unclassified_authority_fails_closed_and_native_is_explicit(uow):
    from app.application.authority import require_command_plane_authority
    env = make(uow)
    patient = uow.read(lambda tx: tx.patients.get(ORG, env.patient_id))
    assert patient.authority_plane == "MONGO"
    assert require_command_plane_authority(patient) == "NATIVE"
    with pytest.raises(PlaneAuthorityError) as raised:
        require_command_plane_authority(patient.model_copy(update={"authority_plane": None}))
    assert raised.value.authority == "UNCLASSIFIED" and raised.value.code == "PLANE_NOT_AUTHORITATIVE"


def test_unclassified_patient_cannot_be_changed_by_any_writer_but_stays_readable(uow):
    env = make(uow)
    service = PatientCommandService(uow)
    set_authority(uow, env, None)
    before = effects(uow, env)
    client = client_for(uow)
    preview = client.post(url(env, "commands/preview"), json={
        "type": "RECORD_ACTUAL", "logical_key": "C1D1:0", "visit_date": "2026-01-08",
        "effective_at": "2026-01-08", "reason": "attended", "horizon": "2026-12-31"})
    assert preview.status_code == 409 and preview.json()["detail"]["code"] == "PLANE_NOT_AUTHORITATIVE"
    old = client.post(
        url(env, "actual-occurrences"), headers={"Idempotency-Key": "u"},
        json={"logical_key": "C1D1:0", "kind": "PERFORMED", "visit_date": "2026-01-08",
              "reason": "attended", "horizon": "2026-12-31"})
    assert old.status_code == 409
    with pytest.raises(CommandRefusedError) as raised:
        service.preview(
            env.patient_id, organization_id=ORG, actor_id=ACTOR,
            payload=MarkMissed(logical_key="DOSE:3"), effective_at=date(2026, 2, 1), reason="r",
            today=TODAY, horizon=date(2026, 12, 31))
    assert raised.value.code == "PLANE_NOT_AUTHORITATIVE"
    assert effects(uow, env) == before
    assert client.get(url(env, "calendar")).status_code == 200      # history/read stays available
    assert len(service.ledger(env.patient_id, organization_id=ORG)) == 0


def test_a_request_body_cannot_choose_or_change_authority(uow):
    env = make(uow)
    set_authority(uow, env, None)
    client = client_for(uow)
    response = client.post(url(env, "commands/preview"), json={
        "type": "RECORD_ACTUAL", "logical_key": "C1D1:0", "visit_date": "2026-01-08",
        "effective_at": "2026-01-08", "reason": "r", "authority_plane": "MONGO"})
    assert response.status_code == 422
    assert uow.read(lambda tx: tx.patients.get(ORG, env.patient_id)).authority_plane is None
