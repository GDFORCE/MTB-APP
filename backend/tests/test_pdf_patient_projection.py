"""P10-03: the patient calendar is an allowlist DTO; nothing staff-only can reach a patient."""
import json
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.patient_projection import (
    CLASSIFICATION, PATIENT, PATIENT_VERSION, STAFF, patient_calendar, patient_status)
from app.application.patients import CalendarEntry
from test_uctsm_authz_http import FakeOperationalAccess
from test_uctsm_authz_mongo_plane import build, person
from test_uctsm_p1_api import make_env, url

pytest_plugins = ["tests.contract.conftest"]
TODAY = date(2026, 3, 12)


def entry(**extra):
    base = dict(logical_key="C1D1:0", event_code="C1D1", occurrence_index=0, logical_occurrence_id=uuid4(),
                evaluation_id=uuid4(), status="RESOLVED", planned_start=date(2026, 3, 20),
                earliest=date(2026, 3, 18), latest=date(2026, 3, 22), window_known=True)
    base.update(extra)
    return CalendarEntry(**base)


def one(calendar, names=None):
    return patient_calendar(calendar, {"C1D1": "Cycle 1 Day 1"} if names is None else names, today=TODAY)["visits"][0]


def test_every_calendar_field_is_classified_so_a_new_field_cannot_leak_by_default():
    fields = set(CalendarEntry.model_fields)
    assert fields == set(CLASSIFICATION), f"classify: {fields ^ set(CLASSIFICATION)}"
    assert set(CLASSIFICATION.values()) == {PATIENT, STAFF}


def test_a_patient_sees_the_allowed_facts_and_none_of_the_staff_detail():
    secret = entry(override_target=date(2026, 3, 25), override_reason="clinician note: transport issue",
                   override_authority="PI", cancelled_reason="internal", last_command_id=uuid4(),
                   track_code="TREATMENT", track_name="Treatment", track_rebase_policy="PREVIOUS_ACTUAL",
                   appointment_date=date(2026, 3, 19), original_planned=date(2026, 3, 10),
                   actual_treatment_date=date(2026, 3, 1))
    visit = one([secret])
    text = json.dumps(visit)
    for leaked in ("transport", "clinician", "PI", "TREATMENT", "PREVIOUS_ACTUAL", "2026-03-25", "2026-03-10",
                   "2026-03-01", "evaluation", "command", "override"):
        assert leaked not in text
    assert visit["arranged_with_care_team"] is True and visit["appointment_date"] == "2026-03-19"
    assert set(visit) == {"id", "title", "status", "planned_date", "window", "appointment_date", "completed_on",
                          "arranged_with_care_team", "accessible_label"}


@pytest.mark.parametrize("kwargs, status", [
    ({}, "UPCOMING"),
    ({"planned_start": date(2026, 3, 12), "earliest": date(2026, 3, 10), "latest": date(2026, 3, 14)}, "DUE_NOW"),
    ({"planned_start": date(2026, 3, 1), "earliest": date(2026, 2, 27), "latest": date(2026, 3, 3)}, "OVERDUE"),
    ({"completed": True, "status": "COMPLETED", "actual_visit_date": date(2026, 3, 9)}, "DONE"),
    ({"cancelled": True}, "CANCELLED"),
    ({"status": "MISSED"}, "MISSED"),
    ({"status": "PAUSED"}, "ON_HOLD"),
    ({"status": "WAITING_FOR_ANCHOR", "planned_start": None, "earliest": None, "latest": None}, "TO_BE_CONFIRMED"),
    ({"planned_start": None, "earliest": None, "latest": None}, "TO_BE_CONFIRMED"),
    ({"provisional": True}, "TO_BE_CONFIRMED"),
    ({"rule_handling": "MANUAL_ONLY"}, "TO_BE_CONFIRMED"),
])
def test_status_is_patient_friendly_and_an_undecidable_visit_is_never_given_a_date(kwargs, status):
    assert patient_status(entry(**kwargs), TODAY) == status
    visit = one([entry(**kwargs)])
    if status in {"TO_BE_CONFIRMED", "CANCELLED", "ON_HOLD"}:
        assert visit["planned_date"] is None and visit["window"] is None


def test_not_applicable_visits_are_omitted_and_order_is_by_date_with_undated_last():
    calendar = [entry(logical_key="B:0", event_code="B", planned_start=date(2026, 4, 1), earliest=None, latest=None, window_known=False),
                entry(logical_key="A:0", event_code="A", planned_start=date(2026, 3, 15)),
                entry(logical_key="N:0", event_code="N", status="NOT_APPLICABLE"),
                entry(logical_key="W:0", event_code="W", status="WAITING_FOR_CONDITION", planned_start=None, earliest=None, latest=None)]
    view = patient_calendar(calendar, {"A": "A", "B": "B", "W": "W", "N": "N"}, today=TODAY)
    assert [row["id"] for row in view["visits"]] == ["A:0", "B:0", "W:0"]
    assert view["projection_version"] == PATIENT_VERSION and view["visits"][1]["window"] is None
    assert view["visits"][1]["planned_date"] == "2026-04-01"


def test_every_visit_has_a_screen_reader_label_and_an_unknown_name_is_generic():
    visit = one([entry(appointment_date=date(2026, 3, 19))], names={})
    assert visit["title"] == "Study visit"
    assert visit["accessible_label"].startswith("Study visit: planned 2026-03-20, between 2026-03-18 and 2026-03-22")
    assert "your appointment is 2026-03-19" in visit["accessible_label"]
    done = one([entry(completed=True, status="COMPLETED", actual_visit_date=date(2026, 3, 9))])
    assert done["accessible_label"] == "Cycle 1 Day 1: done on 2026-03-09" and done["completed_on"] == "2026-03-09"


def test_the_patient_http_route_returns_the_allowlist_dto_with_an_etag_while_staff_keep_the_full_view(uow):
    env = make_env(uow)
    patient = person("patient")
    access = FakeOperationalAccess()
    access.link(patient, own=env.external_patient_id)
    client, _, _ = build(uow, patient, access)
    response = client.get(url(env, "calendar"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projection_version"] == PATIENT_VERSION and "visits" in body and "calendar" not in body
    flat = json.dumps(body)
    for forbidden in ("evaluation_id", "schedule_version_id", "authority_plane", "override_reason", "track_code",
                      "last_command_id", "logical_occurrence_id", "pending_anchor_candidates"):
        assert forbidden not in flat
    tag = response.headers["etag"]
    assert client.get(url(env, "calendar"), headers={"If-None-Match": tag}).status_code == 304
    pi = person("pi")
    access.link(pi, patient=env.external_patient_id)
    staff, _, _ = build(uow, pi, access)
    staff_body = staff.get(url(env, "calendar")).json()
    assert "calendar" in staff_body and staff_body["calendar"] and "visits" not in staff_body
