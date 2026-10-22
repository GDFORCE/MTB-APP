"""P10-05: reports reconcile exactly with the calendar and are filtered by role."""
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from app.application.patients import CalendarEntry
from app.application.projection_snapshot import ProjectionSnapshotService
from app.application.schedule_report import SPONSOR, STAFF, ScheduleReportService, deviation, to_csv
from app.domain.schedule.models import OccurrenceFactKind
from test_uctsm_authz_http import FakeOperationalAccess
from test_uctsm_authz_mongo_plane import build, person
from test_uctsm_p1_api import make_env
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]


def entry(**extra):
    base = dict(logical_key="C1D1:0", event_code="C1D1", occurrence_index=0, logical_occurrence_id=uuid4(),
                evaluation_id=uuid4(), status="RESOLVED", planned_start=date(2026, 3, 20),
                earliest=date(2026, 3, 18), latest=date(2026, 3, 22), window_known=True)
    base.update(extra)
    return CalendarEntry(**base)


def test_deviation_is_computed_only_from_known_dates_and_never_reported_as_zero_or_compliant():
    assert deviation(entry(actual_visit_date=date(2026, 3, 23))) == (3, True)
    assert deviation(entry(actual_visit_date=date(2026, 3, 19))) == (-1, False)
    assert deviation(entry(actual_visit_date=date(2026, 3, 20))) == (0, False)
    assert deviation(entry()) == (None, None)                                         # no actual: unknown
    assert deviation(entry(planned_start=None, actual_visit_date=date(2026, 3, 1))) == (None, None)
    assert deviation(entry(window_known=False, earliest=None, latest=None,
                           actual_visit_date=date(2026, 3, 25))) == (5, None)         # no window: no verdict


def test_csv_is_deterministic_role_filtered_and_neutralises_spreadsheet_formulas():
    rows = [{"patient_code": "=HYPERLINK(1)", "visit": "+SUM(A1)", "status": "RESOLVED", "target_date": "2026-03-20",
             "window_earliest": None, "window_latest": None, "actual_visit_date": None, "deviation_days": None,
             "outside_window": None, "schedule_version_id": "v", "evaluation_id": "e", "amendment_state": None,
             "appointment_date": "2026-03-19", "actual_treatment_date": None, "override_target": "2026-03-25",
             "cancelled_reason": "private reason", "commands_applied": 2}]
    meta = {"profile": "X", "projection_version": "uctsm-projection.v1"}
    sponsor = to_csv(rows, profile=SPONSOR, provenance=meta)
    staff = to_csv(rows, profile=STAFF, provenance=meta)
    assert sponsor == to_csv(rows, profile=SPONSOR, provenance=meta)
    assert "private reason" not in sponsor and "override_target" not in sponsor and "2026-03-25" not in sponsor
    assert "private reason" in staff and "override_target" in staff
    assert sponsor.splitlines()[0] == "# profile: X" and "'=HYPERLINK" in sponsor and "'+SUM(A1)" in sponsor


def build_world(uow):
    world = World(uow)
    patient = world.patient_ids[0]
    world.patients.record_actual_occurrence(
        patient, organization_id=world.org, logical_key="C1D1:0", kind=OccurrenceFactKind.PERFORMED,
        visit_date=date(2026, 1, 8), treatment_date=date(2026, 1, 15), actor_id=world.pi, reason="late dose",
        horizon=HORIZON, today=TODAY)
    return world, world.patient(patient)


@pytest.mark.parametrize("profile", [STAFF, SPONSOR])
def test_every_report_row_reconciles_exactly_with_the_calendar(uow, profile):
    world, record = build_world(uow)
    service = ScheduleReportService(uow)
    report = service.patient_report(world.org, world.trial_id, record, profile=profile)
    snapshot = ProjectionSnapshotService(uow).get(
        organization_id=world.org, trial_id=world.trial_id, external_patient_id=record.external_patient_id)
    assert service.reconciles(report, snapshot["calendar"]) == []
    assert len(report["rows"]) == len(snapshot["calendar"]) > 0
    done = next(row for row in report["rows"] if row["logical_key"] == "C1D1:0")
    assert done["actual_visit_date"] == "2026-01-08" and done["deviation_days"] is not None
    assert report["evaluation_id"] == str(snapshot["evaluation_id"])


def test_a_tampered_row_is_detected_by_reconciliation(uow):
    world, record = build_world(uow)
    service = ScheduleReportService(uow)
    report = service.patient_report(world.org, world.trial_id, record, profile=STAFF)
    snapshot = ProjectionSnapshotService(uow).get(
        organization_id=world.org, trial_id=world.trial_id, external_patient_id=record.external_patient_id)
    report["rows"][0]["target_date"] = "1999-01-01"
    dropped = report["rows"].pop()
    problems = service.reconciles(report, snapshot["calendar"])
    assert f"MISMATCH:{report['rows'][0]['logical_key']}:target_date" in problems
    assert f"MISSING_IN_REPORT:{dropped['logical_key']}" in problems


def test_staff_rows_carry_operational_detail_and_sponsor_rows_never_do(uow):
    world, record = build_world(uow)
    service = ScheduleReportService(uow)
    staff = service.patient_report(world.org, world.trial_id, record, profile=STAFF)["rows"][0]
    sponsor = service.patient_report(world.org, world.trial_id, record, profile=SPONSOR)["rows"][0]
    assert {"appointment_date", "override_target", "cancelled_reason", "commands_applied"} <= set(staff)
    assert not ({"appointment_date", "override_target", "cancelled_reason", "commands_applied",
                 "actual_treatment_date"} & set(sponsor))


def test_the_report_route_filters_by_relationship_and_role_and_patients_cannot_use_it(uow):
    env = make_env(uow)
    base = f"/api/uctsm/mongo-plane/trials/{env.trial_id}/reports/schedule"
    pi = person("pi")
    access = FakeOperationalAccess()
    access.link(pi, patient=env.external_patient_id)
    client, _, _ = build(uow, pi, access)
    response = client.get(base)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] == "STAFF" and len(body["patients"]) == 1 and body["patients"][0]["rows"]
    assert client.get(base, params={"limit": 0}).status_code == 422
    csv_response = client.get(base, params={"format": "csv"})
    assert csv_response.status_code == 200 and csv_response.text.startswith("# next_cursor")
    assert "patient_code" in csv_response.text and csv_response.headers["cache-control"] == "private, no-store"
    stranger = person("pi")
    other, _, _ = build(uow, stranger, FakeOperationalAccess())
    assert other.get(base).json()["patients"] == []                    # no relationship: zero rows, no leakage
    own = person("patient")
    access.link(own, own=env.external_patient_id)
    patient_client, _, _ = build(uow, own, access)
    assert patient_client.get(base).status_code == 403
    sponsor = person("sponsor")
    access.link(sponsor, patient=env.external_patient_id)
    sponsor_client, _, _ = build(uow, sponsor, access)
    sponsor_body = sponsor_client.get(base).json()
    assert sponsor_body["profile"] == "SPONSOR" and "override_target" not in sponsor_body["patients"][0]["rows"][0]
