"""The Mongo-native synthetic pilot, built through the repository ports.

Memory AND real Mongo. Nothing about an existing trial, SQL record or real patient
is touched; the pilot is approved by a different person than its editor; its
patients are native (explicit MONGO authority) and command-eligible.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.authoring import TrialRecord
from app.application.patient_commands import PatientCommandService
from app.application.patient_schedule import PatientScheduleService
from app.application.pilot import (
    PILOT_PROTOCOL_NUMBER, PilotRefusedError, PilotService,
)
from app.domain.schedule.commands import RecordActual

pytest_plugins = ["tests.contract.conftest"]

ORG = uuid4()
EDITOR, APPROVER = uuid4(), uuid4()


def test_the_pilot_is_native_two_person_approved_and_evaluated(uow):
    result = PilotService(uow).seed(organization_id=ORG, editor_id=EDITOR, approver_id=APPROVER)
    assert result["protocol_number"] == PILOT_PROTOCOL_NUMBER
    assert len(result["patients"]) == 2
    assert {item["authority_plane"] for item in result["patients"]} == {"MONGO"}
    version = result["patients"][0]["schedule_version_id"]
    header = uow.read(lambda tx: tx.schedules.get_header(__import__("uuid").UUID(version)))
    assert header.status == "APPROVED"
    audit = uow.read(lambda tx: tx.audit.list_for_organization(ORG, action=None, limit=200))
    approvals = [row for row in audit if "APPROV" in row.action]
    assert approvals and all(row.actor_id == APPROVER for row in approvals
                             if row.action == "SCHEDULE_VERSION_APPROVED")
    assert EDITOR not in {row.actor_id for row in approvals if row.action == "SCHEDULE_VERSION_APPROVED"}


def test_editor_cannot_also_be_the_approver_and_reseed_is_idempotent(uow):
    with pytest.raises(PilotRefusedError):
        PilotService(uow).seed(organization_id=ORG, editor_id=EDITOR, approver_id=EDITOR)
    service = PilotService(uow)
    first = service.seed(organization_id=ORG, editor_id=EDITOR, approver_id=APPROVER)
    second = service.seed(organization_id=ORG, editor_id=EDITOR, approver_id=APPROVER)
    assert [p["patient_id"] for p in first["patients"]] == [p["patient_id"] for p in second["patients"]]


def test_a_non_pilot_trial_with_the_same_code_is_refused_not_repurposed(uow):
    uow.run(lambda tx: tx.authoring.add_trial(TrialRecord(
        organization_id=ORG, protocol_id=PILOT_PROTOCOL_NUMBER, study_title="Real study")))
    with pytest.raises(PilotRefusedError):
        PilotService(uow).seed(organization_id=ORG, editor_id=EDITOR, approver_id=APPROVER)


def test_pilot_delay_moves_labs_and_keeps_imaging_through_the_committed_plane_evaluation(uow):
    seeded = PilotService(uow).seed(organization_id=ORG, editor_id=EDITOR, approver_id=APPROVER)
    patient_id = __import__("uuid").UUID(seeded["patients"][0]["patient_id"])
    service = PatientCommandService(uow)
    preview = service.preview(
        patient_id, organization_id=ORG, actor_id=EDITOR,
        payload=RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                             treatment_date=date(2026, 1, 15)),
        effective_at=date(2026, 1, 15), reason="given a week late",
        today=date(2026, 3, 1), horizon=date(2026, 12, 31))
    outcome = service.confirm(preview.proposal_id, organization_id=ORG, actor_id=EDITOR,
                              today=date(2026, 3, 1))
    calendar = {item.logical_key: item for item in PatientScheduleService(uow).current_calendar(
        patient_id, organization_id=ORG)}
    assert calendar["LAB_AFTER_TX:0"].planned_start == date(2026, 1, 22)
    assert calendar["IMAGING_BASELINE:0"].planned_start == date(2026, 1, 31)
    assert calendar["C1D1:0"].actual_treatment_date == date(2026, 1, 15)
    assert str(calendar["LAB_AFTER_TX:0"].evaluation_id) == str(outcome.evaluation_id)  # same evaluation


# ---- plane routes: pilot seed and the paged, relationship-filtered list ------------------

from fastapi import FastAPI                                        # noqa: E402
from fastapi.testclient import TestClient                          # noqa: E402

from _authz_fixtures import AllowAllAccess, permissive_authorizer   # noqa: E402,F401
from app.api.uctsm_mongo import create_uctsm_mongo_router           # noqa: E402
from test_uctsm_authz_http import FakeOperationalAccess             # noqa: E402


def _client(uow, *, org, access=None, role="pi"):
    user = {"id": str(EDITOR), "organization_id": str(org), "role": role, "status": "Active"}
    authorizer = permissive_authorizer(str(EDITOR), str(org))
    if access is not None:
        authorizer.access = access
    app = FastAPI()
    app.include_router(create_uctsm_mongo_router(
        lambda: user, uow_dependency=lambda: uow, authorizer=authorizer))
    return TestClient(app)


def test_pilot_seed_route_is_off_unless_the_local_pilot_mode_is_enabled(uow, monkeypatch):
    client = _client(uow, org=ORG)
    monkeypatch.delenv("UCTSM_PILOT_MODE", raising=False)
    assert client.post("/api/uctsm/mongo-plane/pilot/seed").status_code == 404
    monkeypatch.setenv("UCTSM_PILOT_MODE", "true")
    seeded = client.post("/api/uctsm/mongo-plane/pilot/seed")
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["protocol_number"] == PILOT_PROTOCOL_NUMBER
    assert {p["authority_plane"] for p in seeded.json()["patients"]} == {"MONGO"}


def test_patient_list_is_paged_stable_and_filtered_by_relationship(uow, monkeypatch):
    monkeypatch.setenv("UCTSM_PILOT_MODE", "true")
    org = uuid4()
    client = _client(uow, org=org)
    trial_id = client.post("/api/uctsm/mongo-plane/pilot/seed").json()["trial_id"]
    first = client.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients", params={"limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert [row["patient_code"] for row in body["items"]] == ["PILOT-P001"]
    assert body["next_cursor"] == "PILOT-P001" and body["limit"] == 1
    second = client.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients",
                        params={"limit": 1, "cursor": body["next_cursor"]}).json()
    assert [row["patient_code"] for row in second["items"]] == ["PILOT-P002"]
    last = client.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients",
                      params={"limit": 5}).json()
    assert [row["patient_code"] for row in last["items"]] == ["PILOT-P001", "PILOT-P002"]
    assert last["next_cursor"] is None
    assert client.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients",
                      params={"limit": 201}).status_code == 422
    # no operational relationship -> no rows, no total, nothing about existence
    nobody = _client(uow, org=org, access=FakeOperationalAccess())
    empty = nobody.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients").json()
    assert empty["items"] == [] and empty["next_cursor"] is None
    # another organization sees none of this trial
    stranger = _client(uow, org=uuid4())
    assert stranger.get(f"/api/uctsm/mongo-plane/trials/{trial_id}/patients").json()["items"] == []


# ---- F3: partial failure is atomic per patient and the seed reconciles on retry ----------

import threading                                                    # noqa: E402
from uuid import UUID                                                # noqa: E402

from app.application.patient_schedule import PatientScheduleService as _PSS   # noqa: E402


def _complete(uow, org, result) -> None:
    assert result["complete"] is True and len(result["patients"]) == 2
    for row in result["patients"]:
        patient_id, version = UUID(row["patient_id"]), UUID(row["schedule_version_id"])
        # exactly one immutable first evaluation per enrolled patient
        assert uow.read(lambda tx: tx.evaluations.count_evaluations(patient_id, version)) == 1
        assert uow.read(lambda tx: tx.evaluations.get_pointer(patient_id, version)) is not None


@pytest.mark.parametrize("fail_on_call", [1, 2])
def test_first_evaluation_failure_leaves_no_unevaluated_patient_and_retry_completes(
        uow, monkeypatch, fail_on_call):
    org = uuid4()
    real = _PSS.evaluate_in
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise RuntimeError("injected first-evaluation failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(_PSS, "evaluate_in", flaky)
    with pytest.raises(RuntimeError):
        PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    # the failed patient was never visible without its evaluation
    trial = uow.read(lambda tx: tx.authoring.find_trial_by_protocol_id(org, PILOT_PROTOCOL_NUMBER))
    assert trial is not None
    for patient in uow.read(lambda tx: tx.patients.list_for_trial(org, trial.id)):
        assert uow.read(lambda tx: tx.evaluations.get_pointer(
            patient.id, patient.current_schedule_version_id)) is not None
    monkeypatch.setattr(_PSS, "evaluate_in", real)
    result = PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    _complete(uow, org, result)
    again = PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    assert [p["patient_id"] for p in again["patients"]] == [p["patient_id"] for p in result["patients"]]
    _complete(uow, org, again)


def test_failure_after_draft_resumes_at_approval_without_a_second_trial(uow, monkeypatch):
    org = uuid4()
    from app.application.schedule_review import ScheduleReviewService
    real = ScheduleReviewService.approve_reviewed_table
    state = {"fail": True}

    def flaky(self, *args, **kwargs):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("injected approval failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ScheduleReviewService, "approve_reviewed_table", flaky)
    with pytest.raises(RuntimeError):
        PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    result = PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    _complete(uow, org, result)
    headers = uow.read(lambda tx: tx.schedules.list_headers(
        organization_id=org, trial_id=UUID(result["trial_id"])))
    assert len(headers) == 1                                     # no second draft was created


def test_concurrent_seeders_converge_on_one_complete_pilot(uow):
    if "Memory" in type(uow).__name__:
        pytest.skip("the in-memory unit of work is single-threaded by design; Mongo variant runs")
    org = uuid4()
    outcomes: list[object] = []

    def run():
        try:
            outcomes.append(PilotService(uow).seed(
                organization_id=org, editor_id=EDITOR, approver_id=APPROVER))
        except Exception as error:      # noqa: BLE001 - surfaced by the assertion below
            outcomes.append(error)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    final = PilotService(uow).seed(organization_id=org, editor_id=EDITOR, approver_id=APPROVER)
    _complete(uow, org, final)
    patients = uow.read(lambda tx: tx.patients.list_for_trial(
        org, UUID(final["trial_id"])))
    assert len(patients) == 2                                    # no duplicate patients
