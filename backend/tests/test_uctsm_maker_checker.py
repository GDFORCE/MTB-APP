"""P3-04 / P3-05: maker-checker at the approval commit.

SQL plane, real HTTP routes through the real authorization guard, and the direct
service boundary. The repository-port (Mongo) plane is covered in
``test_uctsm_maker_checker_port.py``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.authorization.catalog import Capability
from app.authorization.maker_checker import MakerCheckerPolicy, MakerCheckerViolation, Scope
from app.db import models as db
from app.services.schedule_service import ScheduleReviewService
from test_uctsm_authz_http import ORG_OWNER, World, person


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("UCTSM_MAKER_CHECKER", "on")
    monkeypatch.delenv("UCTSM_MAKER_CHECKER_SCOPE", raising=False)
    instance = World()
    with instance.client:
        yield instance
    instance.engine.dispose()


def make_approver(world: World, name: str) -> str:
    user = person(name, "sponsor", ORG_OWNER)
    world.users[name] = user
    world.access.link(user, trial="op-trial-A", patient="op-pat-A")
    world.access.link(user, trial="op-trial-B", patient="op-pat-B")
    for capability in (Capability.SCHEDULE_CORRECT, Capability.SCHEDULE_REVIEW,
                       Capability.SCHEDULE_APPROVE):
        world.grants.grant_sync(user["id"], ORG_OWNER, capability.value)
    return user["id"]


def revision(world: World, who: str, version: str) -> dict:
    response = world.call(who, "GET", f"/schedule-versions/{version}/review-view")
    # review-view is the projection; the review workflow view is elsewhere
    assert response.status_code == 200, response.text
    return response.json()


def workflow_view(world: World, who: str, version: str) -> dict:
    # the Visit Schedule review view (revision, rows, can_approve)
    from app.services.review_view import ReviewWorkflow
    with world.factory() as session:
        return ReviewWorkflow(session).view(UUID(version))


def event_id(world: World, version: str, code: str) -> str:
    with world.factory() as session:
        return str(session.scalars(select(db.Event).where(
            db.Event.schedule_version_id == UUID(version), db.Event.code == code)).one().id)


def edit(world: World, who: str, version: str, code: str, changes: dict, *, confirm: bool = False):
    view = workflow_view(world, who, version)
    return world.call(who, "PATCH", f"/schedule-versions/{version}/events/{event_id(world, version, code)}",
                      json={"expected_revision": view["revision"], "rationale": "checked against source",
                            "changes": changes, "confirm_row": confirm})


def approve(world: World, who: str, version: str, *, route: str = "approve-version"):
    view = workflow_view(world, who, version)
    if route == "approve-version":
        return world.call(who, "POST", f"/schedule-versions/{version}/approve-version",
                          json={"expected_revision": view["revision"], "reason": "reviewed"})
    if route == "approve-table":
        return world.call(who, "POST", f"/schedule-versions/{version}/approve-table",
                          json={"reason": "reviewed", "expected_revision": view["revision"]})
    raise AssertionError(route)


MATERIAL = {"window": {"before": {"value": 0, "unit": "DAY"}, "after": {"value": 2, "unit": "DAY"}}}


def audit_actions(world: World) -> list[str]:
    with world.factory() as session:
        return [row.action for row in session.scalars(select(db.AuditEvent).order_by(db.AuditEvent.created_at))]


# ----------------------------------------------------------- the core rule

@pytest.mark.parametrize("route", ["approve-version", "approve-table"])
def test_the_material_editor_cannot_approve_their_own_change(world: World, route):
    version = world.version_a
    editor = world.approver["id"]
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200

    refused = approve(world, "approver", version, route=route)
    assert refused.status_code == 403, refused.text
    assert isinstance(refused.json()["detail"], str)       # screens render detail as text
    assert refused.headers["x-authz-code"] == "MAKER_CHECKER"
    assert refused.headers["x-authz-reason"] == "SELF_APPROVAL_OF_MATERIAL_EDIT"
    assert refused.headers["x-authz-policy"] == "maker-checker.v1"

    # nothing was approved or pointed at; the refusal itself is on the record
    assert world.status_of(version) != "APPROVED"
    with world.factory() as session:
        assert session.scalars(select(db.ReviewDecision).where(
            db.ReviewDecision.schedule_version_id == UUID(version),
            db.ReviewDecision.decision == "APPROVE")).first() is None
        assert session.get(db.ScheduleVersion, UUID(version)).approved_by is None
        assert session.get(db.ScheduleVersion, UUID(version)).last_material_editor_id == UUID(editor)
    actions = audit_actions(world)
    assert "SCHEDULE_APPROVAL_DENIED_MAKER_CHECKER" in actions
    assert "SCHEDULE_APPROVED" not in actions


def test_an_independent_authorised_approver_passes_the_existing_gates(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    make_approver(world, "second")
    ok = approve(world, "second", version)
    assert ok.status_code == 200, ok.text
    assert world.status_of(version) == "APPROVED"
    with world.factory() as session:
        row = session.get(db.ScheduleVersion, UUID(version))
        approved = session.scalars(select(db.AuditEvent).where(
            db.AuditEvent.entity_id == row.id, db.AuditEvent.action == "SCHEDULE_APPROVED")).one()
        stamp = approved.after["maker_checker"]
        assert stamp["maker_checker_enabled"] is True
        assert stamp["policy_version"] == "maker-checker.v1"
        assert stamp["reason_code"] == "INDEPENDENT_APPROVER"
        assert stamp["last_material_editor_id"] == world.approver["id"]
        assert stamp["approver_id"] == world.users["second"]["id"]
        assert row.approved_by == UUID(world.users["second"]["id"])


def test_a_user_without_the_approve_capability_is_refused_before_any_editor_logic(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    response = approve(world, "sponsor", version)
    assert response.status_code == 403
    assert response.headers["x-authz-reason"] == "CAPABILITY_MISSING"


# ----------------------------------------------------- what counts as material

def test_a_label_rename_is_not_a_material_edit(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", {"display_name": "Day 1 clinic"}).status_code == 200
    with world.factory() as session:
        assert session.get(db.ScheduleVersion, UUID(version)).last_material_editor_id is None
    assert approve(world, "approver", version).status_code == 200


def test_confirming_a_row_is_not_a_material_edit(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", {}, confirm=True).status_code == 200
    with world.factory() as session:
        assert session.get(db.ScheduleVersion, UUID(version)).last_material_editor_id is None


def test_adding_a_visit_and_a_timing_change_are_material(world: World):
    version = world.version_a
    view = workflow_view(world, "approver", version)
    added = world.call("approver", "POST", f"/schedule-versions/{version}/events", json={
        "expected_revision": view["revision"], "rationale": "added from source",
        "display_name": "Extra visit",
        "timing": {"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                   "offset": {"value": 10, "unit": "DAY"}}})
    assert added.status_code in (200, 201), added.text
    with world.factory() as session:
        assert session.get(db.ScheduleVersion, UUID(version)).last_material_editor_id == UUID(world.approver["id"])


# ---------------------------------------------------------- scope and history

def test_last_editor_scope_only_excludes_the_last_editor(world: World):
    version = world.version_a
    second = make_approver(world, "second")
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    assert edit(world, "second", version, "WEEK_4", {"window": {
        "before": {"value": 1, "unit": "DAY"}, "after": {"value": 1, "unit": "DAY"}}}).status_code == 200
    with world.factory() as session:
        row = session.get(db.ScheduleVersion, UUID(version))
        assert row.last_material_editor_id == UUID(second)
        assert set(row.material_editor_ids) == {world.approver["id"], second}
    # the FIRST editor is not the last one: the requirement's check passes
    assert approve(world, "approver", version).status_code == 200


def test_any_editor_scope_excludes_every_material_editor(world: World, monkeypatch):
    monkeypatch.setenv("UCTSM_MAKER_CHECKER_SCOPE", "ANY_EDITOR")
    version = world.version_a
    make_approver(world, "second")
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    assert edit(world, "second", version, "WEEK_4", {"window": {
        "before": {"value": 1, "unit": "DAY"}, "after": {"value": 1, "unit": "DAY"}}}).status_code == 200
    first = approve(world, "approver", version)
    assert first.status_code == 403 and first.headers["x-authz-reason"] == "SELF_APPROVAL_OF_MATERIAL_EDIT"
    make_approver(world, "third")
    assert approve(world, "third", version).status_code == 200


# ----------------------------------------------------------- configuration

def test_disabled_policy_keeps_existing_behaviour(world: World, monkeypatch):
    monkeypatch.setenv("UCTSM_MAKER_CHECKER", "off")
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    assert approve(world, "approver", version).status_code == 200


def test_default_is_on_and_a_request_cannot_change_the_setting(world: World, monkeypatch):
    monkeypatch.delenv("UCTSM_MAKER_CHECKER", raising=False)
    assert MakerCheckerPolicy.from_environment().enabled is True     # selected policy
    monkeypatch.setenv("UCTSM_MAKER_CHECKER", "on")
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    view = workflow_view(world, "approver", version)
    # an attempt to switch the check off from the request is refused outright
    tampered = world.call("approver", "POST", f"/schedule-versions/{version}/approve-version",
                          json={"expected_revision": view["revision"], "reason": "r",
                                "maker_checker": False},
                          headers={"X-Maker-Checker": "off", "X-Policy": "none"})
    assert tampered.status_code == 422
    assert approve(world, "approver", version).status_code == 403


# ------------------------------------------------------------ concurrency

def test_an_edit_after_the_approval_preview_invalidates_the_approval(world: World):
    version = world.version_a
    make_approver(world, "second")
    preview = workflow_view(world, "second", version)
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    stale = world.call("second", "POST", f"/schedule-versions/{version}/approve-version",
                       json={"expected_revision": preview["revision"], "reason": "reviewed"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_REVISION"
    assert world.status_of(version) != "APPROVED"


# ----------------------------------------------------- the lowest boundary

def test_direct_service_calls_cannot_bypass_the_check(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    editor = UUID(world.approver["id"])
    with world.factory() as session:
        service = ScheduleReviewService(session)
        with pytest.raises(MakerCheckerViolation) as caught:
            service.approve_reviewed_table(UUID(version), reviewer_id=editor, reason="direct")
        assert caught.value.outcome.reason_code == "SELF_APPROVAL_OF_MATERIAL_EDIT"
        session.rollback()
    with world.factory() as session:
        service = ScheduleReviewService(session)
        # move it to IN_REVIEW the legitimate way, then approve() directly
        service.submit_for_review(UUID(version), actor_id=editor)
        with pytest.raises(MakerCheckerViolation):
            service.approve(UUID(version), reviewer_id=editor, comment="direct")
        session.rollback()
    assert world.status_of(version) != "APPROVED"


def test_the_review_endpoint_alias_is_covered_too(world: World):
    version = world.version_a
    assert edit(world, "approver", version, "DAY_1", MATERIAL).status_code == 200
    # the legacy review route needs the draft IN_REVIEW first
    assert world.call("approver", "POST", f"/schedule-versions/{version}/submit-review").status_code == 200
    refused = world.call("approver", "POST", f"/schedule-versions/{version}/review",
                         json={"decision": "APPROVE", "comment": "ok"})
    assert refused.status_code == 403
    assert refused.headers["x-authz-code"] == "MAKER_CHECKER"
    assert world.status_of(version) != "APPROVED"


# ------------------------------------------------ legacy / unknown provenance

def test_legacy_draft_with_no_provenance_never_invents_an_author(world: World, monkeypatch):
    version = world.version_a
    make_approver(world, "second")
    corrector = UUID(world.approver["id"])
    with world.factory() as session:
        # a human correction recorded BEFORE provenance existed: decision rows only
        session.add(db.ReviewDecision(
            schedule_version_id=UUID(version), entity_type="EVENT", decision="CORRECT",
            reviewer_id=corrector, reason="legacy"))
        session.commit()
        assert session.get(db.ScheduleVersion, UUID(version)).last_material_editor_id is None
    # the person named on the legacy correction is conservatively excluded ...
    refused = approve(world, "approver", version)
    assert refused.status_code == 403
    assert refused.headers["x-authz-reason"] == "LEGACY_DRAFT_APPROVER_IS_A_CORRECTOR"
    # ... and everyone else is not
    assert approve(world, "second", version).status_code == 200


def test_a_draft_with_no_recorded_human_edit_has_no_maker_to_segregate(world: World):
    version = world.version_b
    assert approve(world, "approver", version).status_code == 200
    with world.factory() as session:
        stamp = session.scalars(select(db.AuditEvent).where(
            db.AuditEvent.action == "SCHEDULE_APPROVED")).one().after["maker_checker"]
    assert stamp["reason_code"] == "NO_MATERIAL_EDITS_RECORDED"
    assert stamp["provenance_known"] is False


def test_approved_versions_are_never_retrofitted(world: World):
    version = world.version_b
    assert approve(world, "approver", version).status_code == 200
    with world.factory() as session:
        row = session.get(db.ScheduleVersion, UUID(version))
        assert row.last_material_editor_id is None and not row.material_editor_ids
    again = edit(world, "approver", version, "DAY_1", MATERIAL)
    assert again.status_code in (409, 422)   # immutable: provenance cannot be added later


def test_policy_object_is_deployment_owned():
    assert MakerCheckerPolicy.from_environment({"UCTSM_MAKER_CHECKER": "true"}).enabled
    assert not MakerCheckerPolicy.from_environment({"UCTSM_MAKER_CHECKER": "0"}).enabled
    scoped = MakerCheckerPolicy.from_environment(
        {"UCTSM_MAKER_CHECKER": "1", "UCTSM_MAKER_CHECKER_SCOPE": "any_editor"})
    assert scoped.scope is Scope.ANY_EDITOR


def test_default_policy_is_on_with_last_editor_scope_and_off_must_be_explicit():
    from app.authorization.maker_checker import MakerCheckerPolicy, Scope
    default = MakerCheckerPolicy.from_environment({})
    assert default.enabled is True and default.scope is Scope.LAST_EDITOR
    assert MakerCheckerPolicy.from_environment({"UCTSM_MAKER_CHECKER": "garbage"}).enabled is True
    for off in ("0", "false", "no", "off"):
        assert MakerCheckerPolicy.from_environment({"UCTSM_MAKER_CHECKER": off}).enabled is False
