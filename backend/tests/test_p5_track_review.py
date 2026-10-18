"""P5-01 track review through the real SQL review API: an incomplete draft can be
completed by a reviewer (no hand-built typed edit), the decision is a material
edit with provenance, and an unresolved or foreign choice cannot be approved."""

from __future__ import annotations

import pytest

from _review_fixtures import API, build_client, row, seed_schedule, view


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


def post(client, version_id, revision, **body):
    return client.post(f"{API}/schedule-versions/{version_id}/tracks", json={
        "expected_revision": revision, "rationale": "Protocol section 4: imaging is fixed from baseline",
        **body})


TRACKS = [
    {"code": "TREATMENT", "track_type": "TREATMENT", "display_name": "Treatment",
     "anchor_code": "BASELINE", "rebase_policy": "NO_REBASE", "review_state": "CONFIRMED"},
    {"code": "IMAGING", "track_type": "IMAGING", "display_name": "Imaging",
     "anchor_code": "BASELINE", "rebase_policy": "FIXED_FROM_ANCHOR", "review_state": "CONFIRMED"},
]


def test_a_reviewer_classifies_events_and_the_view_shows_it_after_reload(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    before = view(client, version_id)
    assert before["tracks"] == [] and before["track_membership"] == {}      # legacy: untouched

    response = post(client, version_id, before["revision"], tracks=TRACKS, membership={
        "DAY_1": {"scope": "TRACK", "track_code": "TREATMENT"},
        "WEEK_4": {"scope": "TRACK", "track_code": "IMAGING"},
        "SCREENING": {"scope": "GLOBAL"}})
    assert response.status_code == 201, response.text
    after = view(client, version_id)
    assert after["revision"] != before["revision"]                      # a material edit
    assert {item["code"]: item["events"] for item in after["tracks"]} == {
        "TREATMENT": ["DAY_1"], "IMAGING": ["WEEK_4"]}
    assert after["track_membership"]["SCREENING"] == {"scope": "GLOBAL", "track_code": None}
    imaging = next(item for item in after["tracks"] if item["code"] == "IMAGING")
    assert imaging["rebase_policy"] == "FIXED_FROM_ANCHOR"             # stated, never inferred
    assert after["can_approve"] or all("track" not in str(reason).lower()
                                        for reason in after["approval_blockers"])


def test_an_unresolved_policy_or_unknown_track_cannot_be_approved(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    revision = view(client, version_id)["revision"]
    incomplete = [{**TRACKS[1], "rebase_policy": None, "review_state": "PROPOSED"}]
    response = post(client, version_id, revision, tracks=incomplete, membership={
        "DAY_1": {"scope": "TRACK", "track_code": "IMAGING"}})
    assert response.status_code == 201
    state = view(client, version_id)
    assert state["can_approve"] is False
    approve = client.post(f"{API}/schedule-versions/{version_id}/approve-version", json={
        "expected_revision": state["revision"], "reason": "reviewed"})
    assert approve.status_code in (409, 422)

    unknown = post(client, version_id, state["revision"], tracks=TRACKS, membership={
        "DAY_1": {"scope": "TRACK", "track_code": "NOPE"}})
    assert unknown.status_code == 422                                    # foreign track


def test_a_stale_revision_is_refused_and_changes_nothing(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    revision = view(client, version_id)["revision"]
    assert post(client, version_id, revision, tracks=TRACKS, membership={}).status_code == 201
    stale = post(client, version_id, revision, tracks=[], membership={})
    assert stale.status_code == 409
    assert len(view(client, version_id)["tracks"]) == 2


def test_the_stop_policy_round_trips_as_unknown_allowed_and_disallowed(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    revision = view(client, version_id)["revision"]
    tracks = [
        {**TRACKS[0], "code": "A_UNKNOWN", "stoppable": None},
        {**TRACKS[0], "code": "B_ALLOWED", "stoppable": True},
        {**TRACKS[0], "code": "C_DISALLOWED", "stoppable": False},
    ]
    response = post(client, version_id, revision, tracks=tracks, membership={})
    assert response.status_code == 201, response.text
    stored = {item["code"]: item["stoppable"] for item in view(client, version_id)["tracks"]}
    assert stored == {"A_UNKNOWN": None, "B_ALLOWED": True, "C_DISALLOWED": False}
