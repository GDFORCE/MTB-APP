"""WP3 in the API and review: process transitions and occurrence facts through
preview/confirm, calendar series policies, processes and process-scoped actions.
Synthetic schedules and the local demo workspace only."""

from __future__ import annotations

import csv
import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from _review_fixtures import API, build_client, row, view
from app.db import models as db
from app.domain.schedule.models import ConditionalDefinition
from test_review_constraints import persist


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("UCTSM_DEMO_MODE", "true")
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


# --- review view: calendar series, processes, process actions --------------------------

def _calendar(**policies):
    return {"type": "CALENDAR", "start_reference": {"kind": "ANCHOR", "code": "BASELINE"},
            "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
            "termination": {"type": "COUNT", "count": 30}, **policies}


def _wp3_schedule(factory, calendar):
    offset = lambda days: {"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                           "offset": {"value": days, "unit": "DAY"}}
    return persist(factory, [
        {"code": "RT", "timing": offset(0), "calendar_recurrence": calendar},
        {"code": "DOSE_A", "timing": offset(1)},
        {"code": "DOSE_B", "timing": offset(1)},
    ], extra={
        "processes": [
            {"code": "DRUG_A", "display_name": "Drug A", "kind": "INTERVENTION", "event_codes": ["DOSE_A"],
             "hold_resume_policy": "SHIFT_BY_HOLD", "resume_trigger": "EXPLICIT_RESUME"},
            {"code": "DRUG_B", "display_name": "Drug B", "kind": "INTERVENTION", "event_codes": ["DOSE_B"]},
        ],
        # Built directly (as the compiler does). Passing it only as nested data
        # hits a models.py gap: ConditionalDefinition is never model_rebuild()-ed,
        # so its own serializer stays a placeholder and persisting fails.
        "conditional_definitions": [ConditionalDefinition.model_validate({
            "code": "HEPATOTOX", "protocol_label": "Hepatotoxicity", "display_name": "Hepatotoxicity",
            "condition": {"type": "EXISTS", "field": "patient.grade3_alt"},
            "actions": [{"action_type": "STOP_PROCESS", "target_code": "DRUG_A",
                         "condition": {"type": "EXISTS", "field": "patient.grade3_alt"}}],
        })],
    })


def test_unstated_calendar_policies_read_needs_review_and_hold_the_row(api):
    client, factory = api
    body = view(client, _wp3_schedule(factory, _calendar()))
    rt = row(body, "RT")
    policies = {item["key"]: item for item in rt["details"]["recurrence"]["policies"]}

    assert rt["details"]["recurrence"]["type"] == "CALENDAR"
    assert rt["details"]["recurrence"]["summary"].startswith("Every Mon, Tue, Wed, Thu, Fri from Randomisation, for 30")
    assert {key for key, item in policies.items() if item["value"] == "Needs review"} == {
        "count_basis", "holiday_policy", "missed_policy"}
    assert rt["review_status"] == "PENDING"
    assert any(reason.startswith("Repetition policy not stated") for reason in rt["pending_reasons"])
    kinds = [item["kind"] for item in rt["details"]["constraints"]]
    assert kinds.count("CALENDAR_POLICY") == 3 and "ADDITIONAL_RULE" not in kinds


def test_stated_calendar_policies_show_their_values(api):
    client, factory = api
    body = view(client, _wp3_schedule(factory, _calendar(
        count_basis="DELIVERED", holiday_policy="SKIP", missed_policy="APPEND_REPLACEMENT")))
    rt = row(body, "RT")
    values = {item["key"]: item["value"] for item in rt["details"]["recurrence"]["policies"]}
    assert values == {
        "count_basis": "Delivered sessions — a missed session does not count",
        "holiday_policy": "Skip the session",
        "missed_policy": "Add a replacement session at the end",
    }
    assert "delivered sessions" in rt["details"]["recurrence"]["summary"]
    assert not any(reason.startswith("Repetition policy") for reason in rt["pending_reasons"])


def test_processes_and_process_scoped_actions_are_shown_and_exported(api):
    client, factory = api
    version_id = _wp3_schedule(factory, _calendar())
    body = view(client, version_id)
    processes = {item["code"]: item for item in body["processes"]}

    assert processes["DRUG_A"]["hold_resume_label"] == "Shift later dates by the length of the hold"
    assert processes["DRUG_A"]["resume_trigger_label"] == "Resumes only when a resume is recorded"
    assert processes["DRUG_B"]["hold_resume_needs_review"] is True
    dose_a, dose_b = row(body, "DOSE_A"), row(body, "DOSE_B")
    [action] = [item for item in dose_a["details"]["constraints"] if item["kind"] == "PROCESS_ACTION"]
    assert action["summary"].startswith("Stops Drug A only when patient.grade3_alt is recorded")
    assert not [item for item in dose_b["details"]["constraints"] if item["kind"] == "PROCESS_ACTION"]
    assert [item["code"] for item in dose_a["details"]["processes"]] == ["DRUG_A"]

    exported = client.get(f"{API}/schedule-versions/{version_id}/export?format=json").json()
    assert {item["code"] for item in exported["processes"]} == {"DRUG_A", "DRUG_B"}
    records = list(csv.reader(io.StringIO(
        client.get(f"{API}/schedule-versions/{version_id}/export?format=csv").text)))
    start = [record[:1] for record in records].index(["PROCESSES APPENDIX"])
    assert {records[start + 2][0], records[start + 3][0]} == {"DRUG_A", "DRUG_B"}


# --- patient routes: occurrence facts and process transitions -----------------------------

def _approved_patient(client) -> dict:
    workspace = client.post(f"{API}/demo/seed").json()
    version_id, patient_id = workspace["schedule_version_id"], workspace["patient_id"]
    schedule = client.get(f"{API}/schedule-versions/{version_id}").json()
    client.post(f"{API}/schedule-versions/{version_id}/validate")
    client.post(f"{API}/schedule-versions/{version_id}/submit-review")
    for event in schedule["events"]:
        for field_path in ["display_name", "timing"] + (["conditions"] if event["conditions"] else []):
            client.post(f"{API}/schedule-versions/{version_id}/review-decisions", json={
                "decision": "CONFIRM", "entity_type": "EVENT", "entity_id": event["id"],
                "field_path": field_path, "comment": "test"})
    assert client.post(f"{API}/schedule-versions/{version_id}/review",
                       json={"decision": "APPROVE", "comment": "test"}).status_code == 200
    last_dose = next(item for item in schedule["anchors"] if item["code"] == "LAST_DOSE")
    candidate = client.post(f"{API}/patients/{patient_id}/anchors", json={
        "anchor_definition_id": last_dose["id"], "value_date": "2026-12-15", "source_type": "DEMO_UI"}).json()
    preview = client.post(f"{API}/patients/{patient_id}/anchors/{candidate['id']}/impact-preview",
                          json={"horizon": "2028-12-31", "reason": "last dose"}).json()
    assert client.post(f"{API}/schedule-impact-proposals/{preview['id']}/confirm",
                       json={"reason": "ok"}).status_code == 200
    return workspace


def _event(client, patient_id, event_code_fragment="SAFETY"):
    schedule = client.get(f"{API}/patients/{patient_id}/schedule").json()
    return next(item for item in schedule["events"] if event_code_fragment in (item.get("logical_key") or ""))


def test_booked_and_performed_facts_with_correction_through_the_api(api):
    client, factory = api
    patient_id = _approved_patient(client)["patient_id"]
    target = _event(client, patient_id)
    nominal = target["nominal_start_date"]

    booked = client.post(f"{API}/patients/{patient_id}/occurrence-facts", json={
        "logical_key": target["logical_key"], "kind": "BOOKED", "value": "2027-06-01",
        "reason": "Clinic only had a June slot"})
    assert booked.status_code == 201, booked.text
    after_booking = _event(client, patient_id)
    assert after_booking["booked_time"] == "2027-06-01"
    assert after_booking["booked_outside_permitted"] is True
    assert after_booking["nominal_start_date"] == nominal  # the rule never moves

    performed = client.post(f"{API}/patients/{patient_id}/occurrence-facts", json={
        "logical_key": target["logical_key"], "kind": "PERFORMED", "value": "2027-01-14",
        "reason": "Visit done"})
    assert performed.status_code == 201, performed.text
    corrected = client.post(f"{API}/patients/{patient_id}/occurrence-facts", json={
        "logical_key": target["logical_key"], "kind": "PERFORMED", "value": "2027-01-15",
        "supersedes_occurrence_id": performed.json()["occurrence_id"], "reason": "Date typo"})
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["supersedes_occurrence_id"] == performed.json()["occurrence_id"]

    latest = _event(client, patient_id)
    assert latest["actual_time"] == "2027-01-15"
    supersedes = {item["supersedes_occurrence_id"] for item in latest["occurrence_records"]}
    assert performed.json()["occurrence_id"] in supersedes
    with factory() as session:
        actions = session.scalars(select(db.AuditEvent.action).where(
            db.AuditEvent.action.like("PATIENT_OCCURRENCE_FACT_%"))).all()
        assert "PATIENT_OCCURRENCE_FACT_CORRECTED" in actions


def test_occurrence_facts_are_tenant_scoped_and_validated(api):
    client, factory = api
    patient_id = _approved_patient(client)["patient_id"]
    key = _event(client, patient_id)["logical_key"]
    assert client.post(f"{API}/patients/{uuid4()}/occurrence-facts", json={
        "logical_key": key, "kind": "MISSED", "reason": "x"}).status_code == 404
    assert client.post(f"{API}/patients/{patient_id}/occurrence-facts", json={
        "logical_key": key, "kind": "BOOKED", "reason": "no date"}).status_code == 422
    with factory() as session:
        patient = session.get(db.Patient, UUID(patient_id))
        patient.organization_id = uuid4()
        session.commit()
    assert client.post(f"{API}/patients/{patient_id}/occurrence-facts", json={
        "logical_key": key, "kind": "MISSED", "reason": "x"}).status_code == 404
    assert client.post(f"{API}/patients/{patient_id}/schedule/impact-preview", json={
        "horizon": "2028-12-31", "reason": "x"}).status_code == 404


def test_process_transition_preview_then_confirm_through_the_api(api):
    client, factory = api
    patient_id = _approved_patient(client)["patient_id"]
    preview = client.post(f"{API}/patients/{patient_id}/schedule/impact-preview", json={
        "horizon": "2028-12-31", "reason": "Patient withdrew consent",
        "process_transition": {
            "target": {"type": "PATIENT"}, "to_state": "STOPPED", "effective_at": "2026-12-20",
            "reason": "WITHDRAWAL_OF_CONSENT", "authority": "PI", "source_ref": "note-7"}})
    assert preview.status_code == 201, preview.text
    body = preview.json()
    assert body["impact"]["process_transition"]["to_state"] == "STOPPED"
    # Nothing is applied until the proposal is confirmed.
    assert _event(client, patient_id)["process_state"] is None

    confirmed = client.post(f"{API}/schedule-impact-proposals/{body['proposal_id']}/confirm",
                            json={"reason": "PI confirmed withdrawal"})
    assert confirmed.status_code == 200, confirmed.text
    after = _event(client, patient_id)
    assert after["status"] == "CANCELLED"
    assert after["process_state"]["state"] == "STOPPED"


def test_invalid_process_transitions_are_refused(api):
    client, factory = api
    patient_id = _approved_patient(client)["patient_id"]
    base = {"horizon": "2028-12-31", "reason": "x"}
    missing_code = client.post(f"{API}/patients/{patient_id}/schedule/impact-preview", json={
        **base, "process_transition": {"target": {"type": "PROCESS"}, "to_state": "HELD",
                                       "effective_at": "2026-12-20", "reason": "DECISION"}})
    assert missing_code.status_code == 422
    confirmed_status = client.post(f"{API}/patients/{patient_id}/schedule/impact-preview", json={
        **base, "process_transition": {"target": {"type": "PATIENT"}, "to_state": "STOPPED",
                                       "effective_at": "2026-12-20", "reason": "WITHDRAWAL_OF_CONSENT",
                                       "status": "CONFIRMED"}})
    assert confirmed_status.status_code == 422  # only a PENDING proposal can be submitted


def test_a_delivered_count_basis_determines_the_missed_session_outcome(api):
    """Matches the engine (AT-05): counting delivered sessions replaces a missed
    one whatever the missed policy says, so that policy is not an open question."""
    client, factory = api
    body = view(client, _wp3_schedule(factory, _calendar(count_basis="DELIVERED", holiday_policy="SKIP")))
    rt = row(body, "RT")
    missed = next(item for item in rt["details"]["recurrence"]["policies"] if item["key"] == "missed_policy")
    assert missed["status"] == "IMPLIED" and "implied by counting delivered" in missed["value"]
    assert not any(reason.startswith("Repetition policy") for reason in rt["pending_reasons"])
    # With scheduled-slot counting the same unstated policy IS open.
    slots = row(view(client, _wp3_schedule(factory, _calendar(count_basis="SLOTS", holiday_policy="SKIP"))), "RT")
    assert any("when a session is missed" in reason for reason in slots["pending_reasons"])
