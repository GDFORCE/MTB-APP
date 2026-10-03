"""Focused regressions for the journey bugs: B1 (held statements released by a
reviewer decision), B3 (one approval gate for every route), B4 (a reviewer
window clears Needs review), B5 (enrollable version across an amendment), the
calendar-policy edit and the reviewed holiday calendar. Synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from _review_fixtures import API, USER, build_client, row, seed_schedule, view
from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import ScheduleStatus
from app.services.extraction_service import ExtractionService
from app.services.schedule_service import resolve_current_schedule_version
from test_review_accountability import add_extraction_issue, attach, ledger, statement
from test_review_constraints import persist


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


# --- B1 ---------------------------------------------------------------------------------

def _held_draft(factory):
    version_id = seed_schedule(factory, with_issues=True)  # POST_OP_* count from SURGERY
    held = [statement("s-po1", "PENDING_INTERPRETATION", event_code="POST_OP_1"),
            statement("s-po2", "PENDING_INTERPRETATION", event_code="POST_OP_2"),
            # Held by the same question but never compiled into a rule: must stay pending.
            {**statement("s-norule", "PENDING_INTERPRETATION"), "blocking_issue_codes": ["ANCHOR_NOT_CANONICAL"]}]
    for item in held[:2]:
        item["blocking_issue_codes"] = ["ANCHOR_NOT_CANONICAL"]
    attach(factory, version_id, ledger(statements=held))
    for fact in ("s-po1", "s-po2", "s-norule"):
        add_extraction_issue(
            factory, version_id, code="ANCHOR_NOT_CANONICAL", message="'Surgery' is not a reference date.",
            anchor_name="Surgery", anchor_code="SURGERY", fact_ids=[fact], candidates=["BASELINE"],
            question_id="anchor-equivalence-surgery", shared_question=True)
    return version_id


def test_b1_answering_the_shared_question_releases_held_statements_by_recorded_decision(api):
    client, factory = api
    version_id = _held_draft(factory)
    before = view(client, version_id)
    assert before["source_coverage"]["statements"]["uncompiled"] == 3
    # Coverage itself is complete: the blocker is named for what it is.
    codes = [item["code"] for item in before["document_level_issues"]]
    assert "UNCOMPILED_STATEMENTS" in codes and "SOURCE_COVERAGE_COMPLETE" not in codes

    resolved = client.post(
        f"{API}/schedule-versions/{version_id}/review-questions/anchor-equivalence-surgery/resolve",
        json={"expected_revision": before["revision"], "rationale": "Section 6.2: same as randomisation",
              "action": "REMAP_ANCHOR", "anchor_code": "BASELINE"})
    assert resolved.status_code == 200, resolved.text
    after = resolved.json()["view"]

    for code in ("POST_OP_1", "POST_OP_2"):
        assert row(after, code)["review_status"] == "READY", row(after, code)["pending_reasons"]
        [linked] = row(after, code)["details"]["statements"]
        assert linked["disposition"] == "COMPILED" and linked["uncompiled"] is False
    assert after["source_coverage"]["statements"]["uncompiled"] == 1  # the rule-less one stays held

    stored = client.get(f"{API}/schedule-versions/{version_id}/accountability").json()
    released = {item["statement_id"]: item for item in stored["statements"]}
    assert released["s-po1"]["decided_by"] == "REVIEWER"
    assert released["s-po1"]["disposition_history"][0]["disposition"] == "PENDING_INTERPRETATION"
    assert released["s-po1"]["reviewer_decision"]["question_id"] == "anchor-equivalence-surgery"
    assert released["s-norule"]["disposition"] == "PENDING_INTERPRETATION"
    assert stored["summary"]["uncompiled_statements"] == 1 and stored["revision"] == 1
    [decision] = stored["reviewer_decisions"]
    assert sorted(decision["released_statement_ids"]) == ["s-po1", "s-po2"]
    with factory() as session:
        audit = session.scalars(select(db.AuditEvent).where(
            db.AuditEvent.action == "EXTRACTION_STATEMENT_DISPOSITION_DECIDED")).one()
        assert audit.before["s-po1"]["disposition"] == "PENDING_INTERPRETATION"
        assert audit.after["ledger_revision"] == 1


def test_b1_a_statement_with_another_blocking_code_is_not_released(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    item = {**statement("s-x", "PENDING_INTERPRETATION", event_code="DAY_1"),
            "blocking_issue_codes": ["ANCHOR_NOT_CANONICAL", "CONFLICTING_SOURCE"]}
    attach(factory, version_id, ledger(statements=[item]))
    with factory() as session:
        released = ExtractionService(session).record_statement_decisions(
            UUID(version_id), statement_ids=["s-x"], actor_id=UUID(USER["id"]), reason="r",
            action="REMAP_ANCHOR", resolved_issue_codes=["ANCHOR_NOT_CANONICAL"])
        session.commit()
        assert released == []
        stored = ExtractionService(session).accountability_for_version(UUID(version_id))
        assert stored["statements"][0]["disposition"] == "PENDING_INTERPRETATION"
        assert stored["statements"][0]["blocking_issue_codes"] == ["CONFLICTING_SOURCE"]


# --- B4 ---------------------------------------------------------------------------------

def test_b4_a_reviewer_stated_window_supersedes_the_note_and_resolves_the_finding(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    with factory() as session:
        event = session.scalars(select(db.Event).where(db.Event.code == "DAY_1")).one()
        event.metadata_json = {"window_as_stated": {"before": None, "after": {"value": 7, "unit": "DAY"},
                                                    "reason": "only one side of the window is stated"}}
        session.add(db.ValidationIssue(
            schedule_version_id=UUID(version_id), validator_version="uctsm-validator.v1",
            entity_type="EVENT", entity_id=event.id, issue_code="WINDOW_NOT_APPLIED", severity="WARNING",
            message="the stated window could not be applied", details={"origin": "EXTRACTION"},
            blocking=False, status="OPEN"))
        session.commit()
    before = view(client, version_id)
    day1 = row(before, "DAY_1")
    assert day1["window_status"] == "NEEDS_REVIEW" and day1["review_status"] == "PENDING"

    window = {"before": {"value": 0, "unit": "DAY"}, "after": {"value": 7, "unit": "DAY"}}
    edited = client.patch(f"{API}/schedule-versions/{version_id}/events/{day1['id']}", json={
        "expected_revision": before["revision"], "rationale": "Sponsor: not earlier than Day 1",
        "changes": {"window": window}, "confirm_row": True})
    assert edited.status_code == 200, edited.text
    after = row(edited.json()["view"], "DAY_1")
    assert after["window_status"] == "STATED" and after["window_summary"] == "−0 / +7 days"
    assert after["review_status"] == "READY", after["pending_reasons"]
    assert edited.json()["changed_row_ids"] == [day1["id"]]
    assert not any(item["kind"] == "WINDOW_AS_STATED" for item in after["details"]["constraints"])
    with factory() as session:
        issue = session.scalars(select(db.ValidationIssue).where(
            db.ValidationIssue.issue_code == "WINDOW_NOT_APPLIED")).one()
        assert issue.status == "RESOLVED" and "Sponsor" in issue.resolution_note
        stored = session.scalars(select(db.Event).where(db.Event.code == "DAY_1")).one()
        # The compiler's note is kept as history, not silently dropped.
        assert stored.metadata_json["last_edit"]["superseded_window_note"]["reason"].startswith("only one side")


# --- B3 ---------------------------------------------------------------------------------

def test_b3_approve_table_enforces_the_same_gates_as_approve_version(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    with factory() as session:  # a pending row with no validator finding
        session.scalars(select(db.Event).where(db.Event.code == "DAY_1")).one().requires_review = True
        session.commit()
    body = view(client, version_id)
    assert body["can_approve"] is False and body["summary"]["blocking_issues"] == 0

    for route, payload in (("approve-version", {"expected_revision": body["revision"], "reason": "ok"}),
                           ("approve-table", {"reason": "ok"})):
        refused = client.post(f"{API}/schedule-versions/{version_id}/{route}", json=payload)
        assert refused.status_code == 409, (route, refused.text)
    # The review-decision route ends in the same gate, even with every field confirmed.
    client.post(f"{API}/schedule-versions/{version_id}/submit-review")
    client.post(f"{API}/schedule-versions/{version_id}/bulk-confirm", json={"reason": "confirm all"})
    decision = client.post(f"{API}/schedule-versions/{version_id}/review", json={"decision": "APPROVE", "comment": "ok"})
    assert decision.status_code == 409 and "still need review" in decision.json()["detail"]
    with factory() as session:
        assert session.get(db.ScheduleVersion, UUID(version_id)).status != "APPROVED"

    # Uncompiled ledger statements block approve-table too.
    clean = seed_schedule(factory, with_issues=False)
    attach(factory, clean, ledger(statements=[statement("s", "UNSUPPORTED", event_code="DAY_1")]))
    assert client.post(f"{API}/schedule-versions/{clean}/approve-table", json={"reason": "ok"}).status_code == 409
    # ... and it still approves a draft whose gates pass.
    passing = seed_schedule(factory, with_issues=False)
    assert client.post(f"{API}/schedule-versions/{passing}/approve-table", json={"reason": "ok"}).status_code == 200


# --- B5 ---------------------------------------------------------------------------------

def _amend(factory, version_id: str) -> str:
    """A second protocol version (an amendment) with its own definition and draft."""
    with factory() as session:
        version = session.get(db.ScheduleVersion, UUID(version_id))
        definition = session.get(db.ScheduleDefinition, version.schedule_definition_id)
        first = session.get(db.ProtocolVersion, definition.protocol_version_id)
        amendment = db.ProtocolVersion(
            protocol_id=first.protocol_id, version_label="2.0", document_name="amendment.pdf",
            document_uri="private://amendment.pdf", document_hash="b" * 64, uploaded_by=UUID(USER["id"]))
        session.add(amendment)
        session.flush()
        new_definition = db.ScheduleDefinition(protocol_version_id=amendment.id, name=definition.name)
        session.add(new_definition)
        session.flush()
        schedule = ScheduleRepository(session).get(version.id)
        from uuid import uuid4
        remap = {item.id: uuid4() for item in schedule.evidence}
        payload = schedule.model_dump(mode="json")
        text = __import__("json").dumps(payload)
        for collection in ("events", "anchors", "arms"):
            for item in payload[collection]:
                text = text.replace(item["id"], str(uuid4()))
            for event in payload["events"] if collection == "events" else []:
                for child in [*event["activities"], *event["qualifiers"]]:
                    text = text.replace(child["id"], str(uuid4()))
        for old, new in remap.items():
            text = text.replace(str(old), str(new))
        copy = type(schedule).model_validate(__import__("json").loads(text))
        copy = copy.model_copy(update={
            "schedule_version_id": uuid4(), "validation_issues": [],
            "schedule_metadata": copy.schedule_metadata.model_copy(update={
                "protocol_version_id": amendment.id, "status": ScheduleStatus.VALIDATION_REQUIRED,
                "effective_from": None}),
        })
        ScheduleRepository(session).persist_draft(copy, schedule_definition_id=new_definition.id)
        session.commit()
        return str(copy.schedule_version_id)


def _ids(factory, version_id):
    with factory() as session:
        version = session.get(db.ScheduleVersion, UUID(version_id))
        definition = session.get(db.ScheduleDefinition, version.schedule_definition_id)
        protocol_version = session.get(db.ProtocolVersion, definition.protocol_version_id)
        protocol = session.get(db.Protocol, protocol_version.protocol_id)
        return protocol.trial_id, definition.id


def _approve(client, version_id, **extra):
    body = view(client, version_id)
    response = client.post(f"{API}/schedule-versions/{version_id}/approve-version",
                           json={"expected_revision": body["revision"], "reason": "reviewed", **extra})
    assert response.status_code == 200, response.text


def test_b5_v1_enrols_until_the_amendment_is_approved_then_v2_whichever_definition_is_linked(api):
    client, factory = api
    v1 = seed_schedule(factory, with_issues=False)
    _approve(client, v1)
    v2 = _amend(factory, v1)
    trial_id, def1 = _ids(factory, v1)
    _, def2 = _ids(factory, v2)

    with factory() as session:  # amendment still a draft: v1, even when the link names the new definition
        assert str(resolve_current_schedule_version(session, trial_id, def1).id) == v1
        assert str(resolve_current_schedule_version(session, trial_id, def2).id) == v1
    _approve(client, v2)
    with factory() as session:  # approved and in force: v2 for new patients, from either link
        assert str(resolve_current_schedule_version(session, trial_id, def2).id) == v2
        assert str(resolve_current_schedule_version(session, trial_id, def1).id) == v2
        # v1 itself is untouched - patients pinned to it stay on it.
        assert session.get(db.ScheduleVersion, UUID(v1)).status == "APPROVED"


def test_b5_an_amendment_approved_for_a_future_date_leaves_v1_enrolling(api):
    client, factory = api
    v1 = seed_schedule(factory, with_issues=False)
    _approve(client, v1)
    v2 = _amend(factory, v1)
    trial_id, def1 = _ids(factory, v1)
    _, def2 = _ids(factory, v2)
    _approve(client, v2, effective_from=(date.today() + timedelta(days=30)).isoformat())
    with factory() as session:
        assert str(resolve_current_schedule_version(session, trial_id, def1).id) == v1
        assert str(resolve_current_schedule_version(session, trial_id, def2).id) == v1


# --- calendar policy edit + reviewed holiday calendar ------------------------------------

def test_calendar_policies_are_edited_by_typed_patch_and_a_holiday_calendar_is_attached(api):
    client, factory = api
    calendar = {"type": "CALENDAR", "start_reference": {"kind": "ANCHOR", "code": "BASELINE"},
                "weekdays": ["MON", "TUE", "WED", "THU", "FRI"], "termination": {"type": "COUNT", "count": 30}}
    version_id = persist(factory, [{"code": "RT", "calendar_recurrence": calendar, "timing": {
        "type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"}, "offset": {"value": 0, "unit": "DAY"}}}])
    body = view(client, version_id)
    rt = row(body, "RT")
    assert any(reason.startswith("Repetition policy not stated") for reason in rt["pending_reasons"])

    stated = {**calendar, "count_basis": "DELIVERED", "holiday_policy": "SKIP",
              "holiday_calendar_ref": "SITE_HOLIDAYS", "missed_policy": "APPEND_REPLACEMENT"}
    edited = client.patch(f"{API}/schedule-versions/{version_id}/events/{rt['id']}", json={
        "expected_revision": body["revision"], "rationale": "Protocol 5.2 states all three",
        "changes": {"calendar_recurrence": stated}})
    assert edited.status_code == 200, edited.text
    after = row(edited.json()["view"], "RT")
    policies = {item["key"]: item for item in after["details"]["recurrence"]["policies"]}
    assert all(item["status"] == "STATED" for item in policies.values())
    assert "not attached to this version" in policies["holiday_policy"]["value"]
    assert not any(reason.startswith("Repetition policy") for reason in after["pending_reasons"])

    attached = client.post(f"{API}/schedule-versions/{version_id}/holiday-calendars", json={
        "expected_revision": edited.json()["revision"], "rationale": "Site holiday list 2026, signed by PI",
        "calendar": {"code": "SITE_HOLIDAYS", "version": "2026.1",
                     "business_weekdays": ["MON", "TUE", "WED", "THU", "FRI"], "dates": ["2026-12-25"]}})
    assert attached.status_code == 201, attached.text
    result = attached.json()
    assert result["revision"] != edited.json()["revision"]
    [stored] = result["view"]["holiday_calendars"]
    assert stored["code"] == "SITE_HOLIDAYS" and stored["dates"] == ["2026-12-25"]
    value = {item["key"]: item["value"] for item in row(result["view"], "RT")["details"]["recurrence"]["policies"]}
    assert value["holiday_policy"] == "Skip the session (calendar SITE_HOLIDAYS)"

    stale = client.post(f"{API}/schedule-versions/{version_id}/holiday-calendars", json={
        "expected_revision": edited.json()["revision"], "rationale": "again",
        "calendar": {"code": "X", "version": "1", "business_weekdays": ["MON"]}})
    assert stale.status_code == 409
    exported = client.get(f"{API}/schedule-versions/{version_id}/export?format=json").json()
    assert exported["holiday_calendars"][0]["code"] == "SITE_HOLIDAYS"
    assert exported["canonical_schedule"]["holiday_calendars"][0]["version"] == "2026.1"
