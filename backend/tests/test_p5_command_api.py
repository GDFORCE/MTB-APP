"""P5-04/P5-05 server side: the command routes on the Mongo patient plane, real
FastAPI app + guard + (memory and real Mongo) unit of work. Authentication is the
only thing substituted; the capability policy is the real one."""

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

from _authz_fixtures import AllowAllAccess
from app.api.uctsm_mongo import create_uctsm_mongo_router
from app.authorization.catalog import Capability
from app.authorization.grants import MemoryGrantStore
from app.authorization.guard import RouteAuthorizer
from app.domain.schedule.models import ScheduleStatus
from test_p5_tracks_domain import program
from test_uctsm_p1_occurrence_recalculation import ORG, Env

pytest_plugins = ["tests.contract.conftest"]

RECORD = [Capability.PATIENT_OCCURRENCE_RECORD, Capability.PATIENT_COMMAND_PREVIEW,
          Capability.PATIENT_SCHEDULE_READ]


def client_for(uow, user_id, capabilities, role="pi"):
    store = MemoryGrantStore()
    for capability in capabilities:
        store.grant_sync(str(user_id), str(ORG), capability.value)
    app = FastAPI()
    user = {"id": str(user_id), "organization_id": str(ORG), "role": role, "status": "Active"}
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow,
        authorizer=RouteAuthorizer(grants=store, access=AllowAllAccess())))
    return TestClient(app)


def make(uow):
    schedule = program()
    schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
    env = Env(uow, schedule, baseline=date(2026, 1, 1))
    env.evaluate()
    return env


def base(env):
    return f"/api/uctsm/mongo-plane/trials/{env.trial_id}/patients/{env.external_patient_id}"


ACTUAL = {"type": "RECORD_ACTUAL", "logical_key": "C1D1:0", "visit_date": "2026-01-08",
          "treatment_date": "2026-01-15", "effective_at": "2026-01-15",
          "reason": "given a week late", "horizon": "2026-12-31"}


def test_preview_then_confirm_over_http_with_before_after_and_replay(uow):
    env, user = make(uow), uuid4()
    client = client_for(uow, user, RECORD)
    preview = client.post(f"{base(env)}/commands/preview", json=ACTUAL)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    rows = {row["logical_key"]: row for row in body["changes"]}
    assert rows["LAB_AFTER_TX:0"]["protocol"]["new_target"] == "2026-01-22"
    assert rows["IMAGING_BASELINE:0"]["changed"] is False
    assert rows["IMAGING_BASELINE:0"]["protocol"]["new_target"] == "2026-01-31"
    assert uow.read(lambda tx: len(tx.ledger.list_for_patient(ORG, env.patient_id))) == 0

    confirm = client.post(f"{base(env)}/commands/{body['proposal_id']}/confirm", json={},
                          headers={"Idempotency-Key": "http-1"})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["digest"] == body["digest"] and confirm.json()["replayed"] is False
    again = client.post(f"{base(env)}/commands/{body['proposal_id']}/confirm", json={},
                        headers={"Idempotency-Key": "http-1"})
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["command_id"] == confirm.json()["command_id"]
    ledger = client.get(f"{base(env)}/commands").json()["commands"]
    assert [item["command_type"] for item in ledger] == ["RECORD_ACTUAL"]
    assert ledger[0]["actor_id"] == str(user)           # from authentication, not the body


def test_client_cannot_supply_actor_or_grants(uow):
    env = make(uow)
    client = client_for(uow, uuid4(), RECORD)
    for extra in ({"actor_id": str(uuid4())}, {"capability": "patient.command.high_impact"}):
        assert client.post(f"{base(env)}/commands/preview", json={**ACTUAL, **extra}).status_code == 422


def test_high_impact_commands_need_their_own_grant_and_a_fact_grant_is_not_enough(uow):
    env = make(uow)
    clinician = client_for(uow, uuid4(), RECORD)
    override = {"type": "OVERRIDE_OCCURRENCE", "logical_key": "IMAGING_BASELINE:0",
                "target_date": "2026-02-20", "authority": "PI", "effective_at": "2026-01-12",
                "reason": "scanner down", "horizon": "2026-12-31"}
    denied = clinician.post(f"{base(env)}/commands/preview", json=override)
    assert denied.status_code == 403, denied.text
    assert denied.headers["X-Authz-Capability"] == "patient.command.high_impact"
    granted = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    assert granted.post(f"{base(env)}/commands/preview", json=override).status_code == 200


def test_confirm_rechecks_the_capability_of_the_proposals_own_type(uow):
    env = make(uow)
    maker = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    proposal = maker.post(f"{base(env)}/commands/preview", json={
        "type": "CANCEL_OCCURRENCE", "logical_key": "DOSE:3", "effective_at": "2026-01-20",
        "reason": "stopped", "horizon": "2026-12-31"}).json()["proposal_id"]
    weaker = client_for(uow, uuid4(), RECORD)          # may record facts, not cancel
    refused = weaker.post(f"{base(env)}/commands/{proposal}/confirm", json={})
    assert refused.status_code == 403
    assert uow.read(lambda tx: len(tx.ledger.list_for_patient(ORG, env.patient_id))) == 0
    assert maker.post(f"{base(env)}/commands/{proposal}/confirm", json={}).status_code == 200


def test_a_proposal_for_another_patient_is_not_found(uow):
    env, other = make(uow), make(uow)
    client = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    theirs = client.post(f"{base(other)}/commands/preview", json=ACTUAL).json()["proposal_id"]
    assert client.post(f"{base(env)}/commands/{theirs}/confirm", json={}).status_code == 404


def test_missing_policy_and_unsupported_input_come_back_as_structured_errors(uow):
    env = make(uow)
    client = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    refused = client.post(f"{base(env)}/commands/preview", json={
        "type": "REBASE_TRACK", "track_code": "IMAGING", "anchor_code": "BASELINE",
        "new_value": "2026-03-01", "effective_at": "2026-01-25", "reason": "x",
        "horizon": "2026-12-31"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "REBASE_NOT_ALLOWED"
    no_reason = client.post(f"{base(env)}/commands/preview", json={**ACTUAL, "reason": ""})
    assert no_reason.status_code == 422


def test_calendar_shows_operational_layers_to_staff_and_withholds_authority_from_others(uow):
    env = make(uow)
    staff = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    override = {"type": "OVERRIDE_OCCURRENCE", "logical_key": "IMAGING_BASELINE:0",
                "target_date": "2026-02-20", "authority": "PI Dr A", "effective_at": "2026-01-12",
                "reason": "scanner down", "horizon": "2026-12-31"}
    proposal = staff.post(f"{base(env)}/commands/preview", json=override).json()["proposal_id"]
    assert staff.post(f"{base(env)}/commands/{proposal}/confirm", json={}).status_code == 200

    row = {item["logical_key"]: item for item in staff.get(f"{base(env)}/calendar").json()["calendar"]}
    assert row["IMAGING_BASELINE:0"]["planned_start"] == "2026-01-31"
    assert row["IMAGING_BASELINE:0"]["override_target"] == "2026-02-20"
    assert row["IMAGING_BASELINE:0"]["override_authority"] == "PI Dr A"

    sponsor = client_for(uow, uuid4(), [Capability.PATIENT_SCHEDULE_READ], role="sponsor")
    seen = {item["logical_key"]: item for item in sponsor.get(f"{base(env)}/calendar").json()["calendar"]}
    assert seen["IMAGING_BASELINE:0"]["override_target"] == "2026-02-20"
    assert seen["IMAGING_BASELINE:0"]["override_authority"] is None
    assert seen["IMAGING_BASELINE:0"]["override_reason"] is None
    assert seen["IMAGING_BASELINE:0"]["last_command_id"] is None


def test_history_withholds_every_private_field_from_a_sponsor_even_with_an_explicit_grant(uow):
    env = make(uow)
    staff = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT])
    override = {"type": "OVERRIDE_OCCURRENCE", "logical_key": "IMAGING_BASELINE:0",
                "target_date": "2026-02-20", "authority": "SENTINEL_AUTHORITY_9",
                "effective_at": "2026-01-12", "reason": "SENTINEL_CLINICAL_REASON_9",
                "horizon": "2026-12-31"}
    proposal = staff.post(f"{base(env)}/commands/preview", json=override).json()["proposal_id"]
    assert staff.post(f"{base(env)}/commands/{proposal}/confirm", json={}).status_code == 200
    own = staff.get(f"{base(env)}/commands")
    assert "SENTINEL_AUTHORITY_9" in own.text                    # staff keep the detail

    sponsor = client_for(uow, uuid4(), [Capability.PATIENT_COMMAND_PREVIEW,
                                        Capability.PATIENT_SCHEDULE_READ], role="sponsor")
    seen = sponsor.get(f"{base(env)}/commands")
    assert seen.status_code == 200 and seen.json()["profile"] == "RESTRICTED"
    for sentinel in ("SENTINEL_AUTHORITY_9", "SENTINEL_CLINICAL_REASON_9"):
        assert sentinel not in seen.text and sentinel not in str(dict(seen.headers))
    row = seen.json()["commands"][0]
    assert "payload" not in row and "reason" not in row and "actor_id" not in row
    assert row["command_type"] == "OVERRIDE_OCCURRENCE"


def test_treatment_then_visit_over_http_survive_reload_and_replay_is_stable(uow):
    env = make(uow)
    client = client_for(uow, uuid4(), RECORD)

    def record(body, key):
        preview = client.post(f"{base(env)}/commands/preview", json={
            "type": "RECORD_ACTUAL", "logical_key": "C1D1:0", "effective_at": "2026-01-15",
            "reason": "documented", "horizon": "2026-12-31", **body})
        assert preview.status_code == 200, preview.text
        confirm = client.post(f"{base(env)}/commands/{preview.json()['proposal_id']}/confirm",
                              json={}, headers={"Idempotency-Key": key})
        assert confirm.status_code == 200, confirm.text
        return confirm.json()

    record({"treatment_date": "2026-01-15"}, "k-treat")
    first = record({"visit_date": "2026-01-08"}, "k-visit")
    replay = record({"visit_date": "2026-01-08"}, "k-visit")
    assert replay["replayed"] is True and replay["command_id"] == first["command_id"]
    rows = {item["logical_key"]: item for item in client.get(f"{base(env)}/calendar").json()["calendar"]}
    assert rows["C1D1:0"]["actual_visit_date"] == "2026-01-08"
    assert rows["C1D1:0"]["actual_treatment_date"] == "2026-01-15"
    assert rows["LAB_AFTER_TX:0"]["planned_start"] == "2026-01-22"
    assert rows["IMAGING_BASELINE:0"]["planned_start"] == "2026-01-31"


def test_a_crc_with_a_valid_high_impact_grant_is_refused_at_preview_confirm_and_replay(uow):
    env = make(uow)
    pi = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT], role="pi")
    cancel = {"type": "CANCEL_OCCURRENCE", "logical_key": "DOSE:3", "effective_at": "2026-01-20",
              "reason": "stopped", "horizon": "2026-12-31"}
    proposal = pi.post(f"{base(env)}/commands/preview", json=cancel).json()["proposal_id"]
    crc = client_for(uow, uuid4(), [*RECORD, Capability.PATIENT_COMMAND_HIGH_IMPACT], role="crc")
    assert crc.post(f"{base(env)}/commands/preview", json=cancel).status_code == 403
    assert crc.post(f"{base(env)}/commands/{proposal}/confirm", json={}).status_code == 403
    assert uow.read(lambda tx: len(tx.ledger.list_for_patient(ORG, env.patient_id))) == 0
    assert pi.post(f"{base(env)}/commands/{proposal}/confirm", json={},
                   headers={"Idempotency-Key": "k"}).status_code == 200
    assert crc.post(f"{base(env)}/commands/{proposal}/confirm", json={},
                    headers={"Idempotency-Key": "k"}).status_code == 403     # replay rechecked too
