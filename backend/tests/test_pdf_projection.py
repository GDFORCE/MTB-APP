"""P10 committed snapshot + role-specific conditional responses, on both stores."""
from datetime import date
from uuid import uuid4

from _p6_world import World
from test_p6_amendment_api import C, DenyAccess, client_for

pytest_plugins = ["tests.contract.conftest"]


def url(world):
    patient = world.patient()
    return f"/api/uctsm/mongo-plane/trials/{world.trial_id}/patients/{patient.external_patient_id}/calendar"


def test_snapshot_and_conditional_get_are_bound_to_the_committed_evaluation(uow):
    world = World(uow, reviewed=True)
    with client_for(uow, world.org, [C.PATIENT_SCHEDULE_READ]) as client:
        reply = client.get(url(world))
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body["projection_version"] == "uctsm-projection.v1"
        assert body["revision"] >= 1 and body["evaluation_id"]
        assert all(row["evaluation_id"] == body["evaluation_id"] for row in body["calendar"])
        assert all(row["schedule_version_id"] == body["schedule_version_id"] for row in body["calendar"])
        cached = client.get(url(world), headers={"If-None-Match": reply.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        assert "private" in cached.headers["cache-control"]
        assert "Authorization" in cached.headers["vary"]


def test_fact_overlay_invalidates_etag_even_if_the_protocol_target_does_not_move(uow):
    world = World(uow, reviewed=True)
    with client_for(uow, world.org, [C.PATIENT_SCHEDULE_READ]) as client:
        first = client.get(url(world))
        world.record(world.patient_ids[0], "DOSE:0", date(2026, 1, 15))
        second = client.get(url(world), headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200
        assert second.headers["etag"] != first.headers["etag"]
        dose = next(row for row in second.json()["calendar"] if row["logical_key"] == "DOSE:0")
        assert dose["actual_visit_date"] == "2026-01-15"


def test_privacy_profiles_do_not_share_an_etag_and_foreign_user_never_gets_304(uow):
    world = World(uow, reviewed=True)
    with client_for(uow, world.org, [C.PATIENT_SCHEDULE_READ]) as staff:
        first = staff.get(url(world))
    with client_for(uow, world.org, [C.PATIENT_SCHEDULE_READ], role="sponsor") as sponsor:
        second = sponsor.get(url(world), headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200 and second.headers["etag"] != first.headers["etag"]
        assert second.json()["pending_anchor_candidates"] == []
        for row in second.json()["calendar"]:
            assert row["override_authority"] is None and row["override_reason"] is None
    with client_for(uow, uuid4(), [C.PATIENT_SCHEDULE_READ], access=DenyAccess()) as foreign:
        assert foreign.get(url(world), headers={"If-None-Match": "*"}).status_code == 404
