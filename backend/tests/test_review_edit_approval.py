"""Semantic edits, Add Visit, shared-question resolution and approval
(UI-06, UI-07, UI-08, RV-04, RV-05, AT-15). Synthetic schedules only."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from _review_fixtures import API, build_client, row, seed_schedule, view
from app.db import models as db


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


def edit(client, version_id, event_id, revision, changes, rationale="Checked against source", **extra):
    return client.patch(f"{API}/schedule-versions/{version_id}/events/{event_id}", json={
        "expected_revision": revision, "rationale": rationale, "changes": changes, **extra,
    })


def status_of(factory, version_id):
    with factory() as session:
        return session.get(db.ScheduleVersion, __import__("uuid").UUID(version_id)).status


def test_label_edit_changes_the_label_and_never_the_timing(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    before = view(client, version_id)
    target = row(before, "WEEK_4")
    schedule_before = client.get(f"{API}/schedule-versions/{version_id}").json()
    event_before = next(item for item in schedule_before["events"] if item["code"] == "WEEK_4")

    response = edit(client, version_id, target["id"], before["revision"], {"display_name": "Week 4 clinic"})
    assert response.status_code == 200, response.text
    result = response.json()

    event_after = next(item for item in client.get(f"{API}/schedule-versions/{version_id}").json()["events"]
                       if item["code"] == "WEEK_4")
    assert event_after["display_name"] == "Week 4 clinic"
    for field in ("timing", "window", "applicability", "recurrence", "activities", "qualifiers"):
        if field == "activities":
            assert [a["id"] for a in event_after[field]] == [a["id"] for a in event_before[field]]
        else:
            assert event_after[field] == event_before[field], field
    assert result["revision"] != before["revision"]
    assert result["changed_row_ids"] == [target["id"]]
    after_row = row(result["view"], "WEEK_4")
    assert after_row["timing_summary"] == target["timing_summary"]
    # The reviewer statement is provenance of its own kind (RV-02).
    assert any(item["is_reviewer_decision"] and item["quote"] == "Checked against source"
               for item in after_row["details"]["evidence"])


def test_stale_edit_is_rejected_with_409_and_changes_nothing(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    first = view(client, version_id)
    target = row(first, "DAY_1")
    assert edit(client, version_id, target["id"], first["revision"], {"display_name": "Day 1 dosing"}).status_code == 200

    stale = edit(client, version_id, target["id"], first["revision"], {"display_name": "Something else"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_REVISION"
    assert row(view(client, version_id), "DAY_1")["visit_name"] == "Day 1 dosing"


def test_window_edit_states_a_window_and_null_restates_not_stated(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    target = row(body, "DAY_1")
    window = {"before": {"value": 0, "unit": "DAY"}, "after": {"value": 2, "unit": "DAY"}}
    stated = edit(client, version_id, target["id"], body["revision"], {"window": window}).json()
    assert row(stated["view"], "DAY_1")["window_summary"] == "−0 / +2 days"
    # The reviewer's window is cited by reviewer evidence, so it does not block.
    assert stated["view"]["summary"]["blocking_issues"] == 0

    cleared = edit(client, version_id, target["id"], stated["revision"], {"window": None}).json()
    assert row(cleared["view"], "DAY_1")["window_status"] == "NOT_STATED"


def test_unknown_or_invalid_patch_fields_are_refused(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    target = row(body, "DAY_1")
    assert edit(client, version_id, target["id"], body["revision"], {"review_status": "READY"}).status_code == 422
    assert edit(client, version_id, target["id"], body["revision"],
                {"timing": {"type": "OFFSET", "offset": {"value": 3}}}).status_code == 422


def test_add_visit_records_manual_origin_rationale_and_review_state(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    response = client.post(f"{API}/schedule-versions/{version_id}/events", json={
        "expected_revision": body["revision"], "rationale": "Sponsor letter 3 adds a phone check",
        "display_name": "Week 2 phone check",
        "timing": {"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                   "offset": {"value": 2, "unit": "WEEK"}},
        "activities": ["Adverse event review"],
    })
    assert response.status_code == 201, response.text
    result = response.json()
    added = next(item for item in result["view"]["rows"] if item["id"] == result["event_id"])

    assert added["origin"] == "MANUAL"
    assert added["details"]["manual"]["rationale"] == "Sponsor letter 3 adds a phone check"
    assert added["details"]["manual"]["review_state"] == "REVIEWER_ENTERED"
    assert added["timing_summary"] == "2 weeks after Randomisation"
    # Nothing is defaulted: no window, no attendance mode.
    assert added["window_status"] == "NOT_STATED"
    assert added["details"]["visit_mode"] is None
    assert result["view"]["summary"]["displayed_rows"] == 4
    assert result["view"]["summary"]["blocking_issues"] == 0
    assert result["changed_row_ids"] == [result["event_id"]]


def test_shared_question_is_resolved_once_for_every_affected_row(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)
    body = view(client, version_id)
    response = client.post(
        f"{API}/schedule-versions/{version_id}/review-questions/missing-anchor:SURGERY/resolve",
        json={"expected_revision": body["revision"], "rationale": "Protocol section 6.2 defines surgery",
              "action": "DEFINE_ANCHOR", "display_name": "Surgery", "anchor_type": "PROCEDURE"})
    assert response.status_code == 200, response.text
    after = response.json()["view"]

    assert after["shared_review_questions"] == []
    for code in ("POST_OP_1", "POST_OP_2"):
        assert row(after, code)["review_status"] == "READY"
        assert row(after, code)["timing_summary"].endswith("after Surgery")
    assert after["summary"]["pending_rows"] == 1  # only Month 6's own footnote remains


def test_remap_rewrites_every_affected_reference(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)
    body = view(client, version_id)
    response = client.post(
        f"{API}/schedule-versions/{version_id}/review-questions/missing-anchor:SURGERY/resolve",
        json={"expected_revision": body["revision"], "rationale": "Surgery is day of randomisation here",
              "action": "REMAP_ANCHOR", "anchor_code": "BASELINE"})
    assert response.status_code == 200, response.text
    after = response.json()["view"]
    assert row(after, "POST_OP_1")["timing_summary"] == "7 days after Randomisation"
    assert row(after, "POST_OP_2")["timing_summary"] == "2 months after Randomisation"


def test_approval_is_blocked_by_backend_gates_whatever_the_client_says(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)
    body = view(client, version_id)

    claimed = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": body["revision"], "reason": "looks fine", "can_approve": True})
    assert claimed.status_code == 422  # unknown fields refused, not ignored
    blocked = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": body["revision"], "reason": "looks fine"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "APPROVAL_BLOCKED"
    assert status_of(factory, version_id) != "APPROVED"


def test_approval_is_whole_version_only(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    partial = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": body["revision"], "reason": "ok", "row_ids": [body["rows"][0]["id"]]})
    assert partial.status_code == 422


def test_stale_approval_is_rejected_without_partial_activation(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    reviewed = view(client, version_id)
    # Someone else edits the draft after the reviewer loaded it (AT-15).
    assert edit(client, version_id, row(reviewed, "DAY_1")["id"], reviewed["revision"],
                {"display_name": "Day 1 (edited)"}).status_code == 200

    stale = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": reviewed["revision"], "reason": "reviewed"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_REVISION"
    assert status_of(factory, version_id) != "APPROVED"
    with factory() as session:
        assert session.scalars(select(db.ReviewDecision).where(
            db.ReviewDecision.decision == "APPROVE")).first() is None

    old_route = client.post(f"{API}/schedule-versions/{version_id}/approve-table", json={
        "reason": "reviewed", "expected_revision": reviewed["revision"]})
    assert old_route.status_code == 409


def test_current_revision_approves_then_the_version_is_immutable(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    approved = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": body["revision"], "reason": "Whole table reviewed against source"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["semantic_fingerprint"] == body["semantic_fingerprint"]

    after = view(client, version_id)
    assert after["can_approve"] is False
    locked = edit(client, version_id, row(after, "DAY_1")["id"], after["revision"], {"display_name": "x"})
    assert locked.status_code == 409
    export = client.get(f"{API}/schedule-versions/{version_id}/export?format=json").json()
    assert export["label"] == "APPROVED"
    with factory() as session:
        audit = session.scalars(select(db.AuditEvent).where(
            db.AuditEvent.action == "SCHEDULE_VERSION_APPROVED_AT_REVISION")).one()
        assert audit.after["revision"] == body["revision"]


def test_confirming_a_row_clears_its_review_flag_only(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    with factory() as session:
        event = session.scalars(select(db.Event).where(db.Event.code == "DAY_1")).one()
        event.requires_review = True
        session.commit()
    body = view(client, version_id)
    assert row(body, "DAY_1")["review_status"] == "PENDING"
    assert body["can_approve"] is False
    confirmed = edit(client, version_id, row(body, "DAY_1")["id"], body["revision"], {}, confirm_row=True)
    assert confirmed.status_code == 200, confirmed.text
    assert row(confirmed.json()["view"], "DAY_1")["review_status"] == "READY"
    assert confirmed.json()["view"]["can_approve"] is True
