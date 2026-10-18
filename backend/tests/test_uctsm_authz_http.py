"""P3-03 / P3-05: the REAL guard on the REAL registered UCTSM routes.

Nothing here substitutes the policy or the guard. Only two things are test
doubles: authentication (who the caller is, chosen per request by a header) and
the operational relationship lookup (which operational trials / patients a user
is tied to), because those live in the legacy Mongo store.

Principals
----------
sponsor    sponsor staff, owning organization, default role rights only
org_admin  sponsor staff who administers the organization (NOT a clinical right)
approver   sponsor staff holding explicit review/correct/approve grants
pi_a       investigator at a DIFFERENT organization, assigned to trial A only
pi_b       investigator assigned to trial B only
patient_a / patient_b   patients, each their own record
outsider   staff of an unrelated organization
suspended  an account an administrator suspended
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _review_fixtures as fx                                    # noqa: E402
from app.api.uctsm import create_uctsm_router                    # noqa: E402
from app.authorization.catalog import Capability                 # noqa: E402
from app.authorization.grants import MemoryGrantStore            # noqa: E402
from app.authorization.guard import RouteAuthorizer              # noqa: E402
from app.db import models as db                                  # noqa: E402
from app.db.base import Base, get_session                        # noqa: E402

API = "/api/uctsm"
ORG_OWNER = fx.USER["organization_id"]
ORG_SITE = "00000000-0000-0000-0000-0000000000a2"
ORG_OTHER = "00000000-0000-0000-0000-0000000000a3"


def person(name: str, role: str, org: str, **extra) -> dict:
    return {"id": str(uuid4()), "role": role, "organization_id": org,
            "status": "Active", "name": name, **extra}


class FakeOperationalAccess:
    """Stand-in for the legacy Mongo relationship rules."""

    def __init__(self) -> None:
        self.trials: dict[str, set[str]] = {}
        self.patients: dict[str, set[str]] = {}
        self.own: dict[str, set[str]] = {}

    def link(self, user: dict, *, trial: str | None = None, patient: str | None = None,
             own: str | None = None) -> None:
        if trial:
            self.trials.setdefault(user["id"], set()).add(trial)
        if patient:
            self.patients.setdefault(user["id"], set()).add(patient)
        if own:
            self.own.setdefault(user["id"], set()).add(own)

    async def can_access_trial(self, user, external_trial_id):
        return external_trial_id in self.trials.get(user["id"], set())

    async def can_access_patient(self, user, external_patient_id):
        return external_patient_id in self.patients.get(user["id"], set())

    async def is_own_patient_record(self, user, external_patient_id):
        return external_patient_id in self.own.get(user["id"], set())


class World:
    def __init__(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:",
                               connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.grants = MemoryGrantStore()
        self.access = FakeOperationalAccess()
        self.sink: list[dict] = []

        async def sink(record):
            self.sink.append(record)

        self.authorizer = RouteAuthorizer(grants=self.grants, access=self.access, denial_sink=sink)

        self.sponsor = person("sponsor", "sponsor", ORG_OWNER)
        self.org_admin = person("org_admin", "sponsor", ORG_OWNER, org_admin=True)
        self.approver = person("approver", "sponsor", ORG_OWNER)
        self.pi_a = person("pi_a", "pi", ORG_SITE)
        self.pi_b = person("pi_b", "pi", ORG_SITE)
        self.patient_a = person("patient_a", "patient", ORG_OWNER)
        self.patient_b = person("patient_b", "patient", ORG_OWNER)
        self.outsider = person("outsider", "sponsor", ORG_OTHER)
        self.suspended = person("suspended", "sponsor", ORG_OWNER, status="Suspended")
        self.users = {u["name"]: u for u in (
            self.sponsor, self.org_admin, self.approver, self.pi_a, self.pi_b,
            self.patient_a, self.patient_b, self.outsider, self.suspended)}

        # two trials of the owning organization, each with a draft schedule
        self.version_a = fx.seed_schedule(self.factory, with_issues=False)
        self.version_b = fx.seed_schedule(self.factory, with_issues=False)
        with self.factory() as session:
            trials = session.scalars(select(db.Trial).order_by(db.Trial.created_at, db.Trial.id)).all()
            by_version = {}
            for version_id in (self.version_a, self.version_b):
                trial = session.execute(
                    select(db.Trial)
                    .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
                    .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
                    .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
                    .join(db.ScheduleVersion, db.ScheduleVersion.schedule_definition_id == db.ScheduleDefinition.id)
                    .where(db.ScheduleVersion.id == UUID(version_id))).scalar_one()
                by_version[version_id] = trial
            self.trial_a, self.trial_b = by_version[self.version_a], by_version[self.version_b]
            assert self.trial_a.id != self.trial_b.id and len(trials) == 2
            self.trial_a.external_trial_id = "op-trial-A"
            self.trial_b.external_trial_id = "op-trial-B"
            self.patient_row_a = db.Patient(
                organization_id=self.trial_a.organization_id, trial_id=self.trial_a.id,
                patient_code="P-A", external_patient_id="op-pat-A")
            self.patient_row_b = db.Patient(
                organization_id=self.trial_b.organization_id, trial_id=self.trial_b.id,
                patient_code="P-B", external_patient_id="op-pat-B")
            session.add_all([self.patient_row_a, self.patient_row_b])
            session.commit()
            self.trial_a_id, self.trial_b_id = str(self.trial_a.id), str(self.trial_b.id)
            self.patient_a_id, self.patient_b_id = str(self.patient_row_a.id), str(self.patient_row_b.id)

        # operational relationships
        a = self.access
        # sponsor-side staff reach their organization's trials AND (as the legacy
        # rule says) the patients enrolled in them
        for staff in (self.sponsor, self.org_admin, self.approver):
            a.link(staff, trial="op-trial-A", patient="op-pat-A")
            a.link(staff, trial="op-trial-B", patient="op-pat-B")
        a.link(self.pi_a, trial="op-trial-A", patient="op-pat-A")
        a.link(self.pi_b, trial="op-trial-B", patient="op-pat-B")
        a.link(self.patient_a, own="op-pat-A")
        a.link(self.patient_b, own="op-pat-B")
        # explicit, persisted clinical grants for the approver only
        for capability in (Capability.SCHEDULE_CORRECT, Capability.SCHEDULE_REVIEW,
                           Capability.SCHEDULE_APPROVE, Capability.SCHEDULE_REJECT):
            self.grants.grant_sync(self.approver["id"], ORG_OWNER, capability.value)

        users = self.users

        async def current_user(request: Request):
            return dict(users[request.headers["X-Test-User"]])

        def session_override() -> Generator[Session, None, None]:
            with self.factory() as session:
                yield session

        app = FastAPI()
        app.include_router(create_uctsm_router(current_user, authorizer=self.authorizer))
        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def call(self, who: str, method: str, path: str, **kw):
        headers = {**kw.pop("headers", {}), "X-Test-User": who}
        return self.client.request(method, f"{API}{path}", headers=headers, **kw)

    def count(self, model) -> int:
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def status_of(self, version_id: str) -> str:
        with self.factory() as session:
            return session.get(db.ScheduleVersion, UUID(version_id)).status


@pytest.fixture()
def world() -> Generator[World, None, None]:
    instance = World()
    with instance.client:
        yield instance
    instance.engine.dispose()


# ----------------------------------------------------------------- allow paths

def test_role_default_with_a_persisted_relationship_allows_a_read(world: World):
    assert world.call("sponsor", "GET", f"/schedule-versions/{world.version_a}").status_code == 200


def test_assigned_investigator_in_another_organization_can_read_only_their_trial(world: World):
    own = world.call("pi_a", "GET", f"/schedule-versions/{world.version_a}")
    assert own.status_code == 200, own.text
    other = world.call("pi_a", "GET", f"/schedule-versions/{world.version_b}")
    assert other.status_code == 404


def test_holder_of_an_explicit_grant_passes_authorization_for_approval(world: World):
    response = world.call("approver", "POST", f"/schedule-versions/{world.version_a}/approve-version",
                          json={"expected_revision": "x" * 8, "reason": "checked"})
    # authorised: the request reaches the approval logic (stale/blocked), not the guard
    assert response.status_code not in (403, 404), response.text


# ------------------------------------------------- capability missing -> 403

@pytest.mark.parametrize("who", ["sponsor", "org_admin", "pi_a"])
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/schedule-versions/{v}/approve-version", {"expected_revision": "x" * 8, "reason": "r"}),
    ("POST", "/schedule-versions/{v}/approve-table", {"reason": "r"}),
    ("POST", "/schedule-versions/{v}/bulk-confirm", {"reason": "r"}),
    ("POST", "/schedule-versions/{v}/submit-review", None),
    ("POST", "/schedule-versions/{v}/review", {"decision": "APPROVE", "comment": "c"}),
    ("POST", "/schedule-versions/{v}/review", {"decision": "REJECT", "reason": "c"}),
    ("PATCH", "/schedule-versions/{v}/events/{e}", {"expected_revision": "x" * 8, "rationale": "r", "changes": {}}),
])
def test_membership_or_org_admin_is_not_a_clinical_capability(world: World, who, method, path, body):
    version = world.version_a
    with world.factory() as session:   # a REAL child of this version: the guard
        event = str(session.scalars(select(db.Event).where(   # verifies parentage
            db.Event.schedule_version_id == UUID(version))).first().id)
    before = (world.count(db.ReviewDecision), world.count(db.AuditEvent), world.status_of(version))
    response = world.call(who, method, path.format(v=version, e=event), json=body)
    # pi_a is assigned to trial A so the resource is visible: 403, not 404
    assert response.status_code == 403, (who, path, response.text)
    assert response.headers["x-authz-reason"] == "CAPABILITY_MISSING"
    after = (world.count(db.ReviewDecision), world.count(db.AuditEvent), world.status_of(version))
    assert after == before, "a denied request must not change anything"


def test_patient_actions_need_the_capability_and_the_relationship_independently(world: World):
    # sponsor has a relationship to trial A's patient (organization) but no
    # patient.occurrence.record capability by role
    denied = world.call("sponsor", "POST", f"/patients/{world.patient_a_id}/occurrence-facts",
                        json={"logical_key": "V1:0", "kind": "PERFORMED"})
    assert denied.status_code == 403
    # pi_b holds the role capability but has no relationship to trial A's patient
    no_tie = world.call("pi_b", "POST", f"/patients/{world.patient_a_id}/occurrence-facts",
                        json={"logical_key": "V1:0", "kind": "PERFORMED"})
    assert no_tie.status_code == 404


# ------------------------------------------------ no relationship -> 404

@pytest.mark.parametrize("who", ["outsider", "pi_b"])
def test_other_tenant_and_other_trial_are_indistinguishable_from_missing(world: World, who):
    real = world.call(who, "GET", f"/schedule-versions/{world.version_a}")
    missing = world.call(who, "GET", f"/schedule-versions/{uuid4()}")
    assert real.status_code == missing.status_code == 404
    assert real.json() == missing.json(), "a foreign resource must not be distinguishable"


def test_enrolment_options_no_longer_leaks_any_schedule_to_any_caller(world: World):
    # regression: this route had no tenant check at all before P3
    response = world.call("outsider", "GET", f"/schedule-versions/{world.version_a}/enrolment-options")
    assert response.status_code == 404


def test_diff_needs_both_versions_to_be_visible(world: World):
    ok = world.call("sponsor", "GET", f"/schedule-versions/{world.version_a}/diff/{world.version_b}")
    assert ok.status_code == 200
    mixed = world.call("pi_a", "GET", f"/schedule-versions/{world.version_a}/diff/{world.version_b}")
    assert mixed.status_code == 404


def test_a_child_id_from_another_schedule_cannot_ride_an_allowed_parent(world: World):
    with world.factory() as session:
        foreign_event = session.scalars(select(db.Event).where(
            db.Event.schedule_version_id == UUID(world.version_b))).first()
        foreign_event_id = str(foreign_event.id)
    response = world.call(
        "approver", "PATCH", f"/schedule-versions/{world.version_a}/events/{foreign_event_id}",
        json={"expected_revision": "x" * 8, "rationale": "r", "changes": {"window": None}})
    assert response.status_code == 404


def test_forged_client_claims_are_not_authorization_evidence(world: World):
    forged = {"X-Organization-Id": ORG_OWNER, "X-Capabilities": "schedule.approve",
              "X-Role": "sponsor"}
    response = world.call("outsider", "GET", f"/schedule-versions/{world.version_a}",
                          headers=forged, params={"organization_id": ORG_OWNER})
    assert response.status_code == 404
    approve = world.call("sponsor", "POST", f"/schedule-versions/{world.version_a}/approve-version",
                         headers=forged, json={"expected_revision": "x" * 8, "reason": "r",
                                               "capabilities": ["schedule.approve"]})
    assert approve.status_code in (403, 422)


# --------------------------------------------------------------- revocation

def test_revoking_a_grant_takes_effect_on_the_next_request(world: World):
    path = f"/schedule-versions/{world.version_a}/submit-review"
    first = world.call("approver", "POST", path)
    assert first.status_code not in (403, 404)
    import asyncio
    records = asyncio.run(world.grants.list_for_organization(ORG_OWNER))
    for record in records:
        asyncio.run(world.grants.revoke(record.id, organization_id=ORG_OWNER, revoked_by="admin"))
    second = world.call("approver", "POST", path)
    assert second.status_code == 403


def test_revoking_the_operational_assignment_takes_effect_immediately(world: World):
    assert world.call("pi_a", "GET", f"/schedule-versions/{world.version_a}").status_code == 200
    world.access.trials[world.pi_a["id"]].clear()
    assert world.call("pi_a", "GET", f"/schedule-versions/{world.version_a}").status_code == 404


def test_a_suspended_account_is_denied_whatever_it_holds(world: World):
    world.grants.grant_sync(world.suspended["id"], ORG_OWNER, Capability.SCHEDULE_READ.value)
    world.access.link(world.suspended, trial="op-trial-A")
    response = world.call("suspended", "GET", f"/schedule-versions/{world.version_a}")
    assert response.status_code == 403
    assert response.headers["x-authz-reason"] == "ACCOUNT_INACTIVE"


# -------------------------------------------------------------- list scoping

def test_pending_review_inbox_contains_only_authorised_trials(world: World):
    with world.factory() as session:
        for version_id in (world.version_a, world.version_b):
            session.get(db.ScheduleVersion, UUID(version_id)).status = "IN_REVIEW"
        session.commit()
    world.access.trials[world.approver["id"]] = {"op-trial-A"}   # not B
    body = world.call("approver", "GET", "/schedule-versions/pending-review").json()
    trials = {item["trial_id"] for item in body["pending"]}
    assert trials == {world.trial_a_id}, body


def test_dashboard_refuses_an_unauthorised_trial_filter_and_scopes_the_default(world: World):
    world.grants.grant_sync(world.pi_a["id"], ORG_SITE, Capability.DASHBOARD_READ.value)
    own = world.call("pi_a", "GET", "/dashboard/actions", params={"trial_id": world.trial_a_id})
    assert own.status_code == 200
    foreign = world.call("pi_a", "GET", "/dashboard/actions", params={"trial_id": world.trial_b_id})
    assert foreign.status_code == 404


# ------------------------------------------------------------ patient privacy

def test_a_patient_reads_only_their_own_schedule(world: World):
    own = world.call("patient_a", "GET", f"/patients/{world.patient_a_id}/schedule")
    assert own.status_code not in (403, 404), own.text
    other = world.call("patient_a", "GET", f"/patients/{world.patient_b_id}/schedule")
    assert other.status_code == 404


def test_a_patient_cannot_write_or_read_staff_surfaces(world: World):
    write = world.call("patient_a", "POST", f"/patients/{world.patient_a_id}/occurrence-facts",
                       json={"logical_key": "V1:0", "kind": "PERFORMED"})
    assert write.status_code == 403
    staff = world.call("patient_a", "GET", f"/schedule-versions/{world.version_a}/review-view")
    assert staff.status_code == 404


def test_sponsor_may_not_perform_clinical_patient_actions(world: World):
    for path, body in (
        (f"/patients/{world.patient_a_id}/anchors", {}),
        (f"/patients/{world.patient_a_id}/unscheduled-visits", {}),
        (f"/patients/{world.patient_a_id}/states", {}),
    ):
        assert world.call("sponsor", "POST", path, json=body).status_code == 403, path


# ------------------------------------------------------ parity / administration

def test_read_mode_change_and_parity_acceptance_need_their_own_grant(world: World):
    assert world.call("sponsor", "GET", f"/trials/{world.trial_a_id}/read-mode").status_code == 200
    change = world.call("sponsor", "POST", f"/trials/{world.trial_a_id}/read-mode",
                        json={"mode": "ENGINE", "reason": "r"})
    assert change.status_code == 403
    accept = world.call("sponsor", "POST", f"/trials/{world.trial_a_id}/parity-runs/{uuid4()}/accept",
                        json={"reason": "r"})
    assert accept.status_code in (403, 404)


def test_demo_seed_needs_a_grant_even_when_demo_mode_is_on(world: World, monkeypatch):
    monkeypatch.setenv("UCTSM_DEMO_MODE", "true")
    assert world.call("sponsor", "POST", "/demo/seed").status_code == 403
    assert world.call("org_admin", "POST", "/demo/seed").status_code == 403


# ----------------------------------------------------------------- audit

def test_every_denial_is_audited_with_a_stable_reason_and_no_phi(world: World):
    world.call("sponsor", "POST", f"/schedule-versions/{world.version_a}/approve-table",
               json={"reason": "r"})
    world.call("outsider", "GET", f"/schedule-versions/{world.version_a}")
    assert len(world.sink) == 2
    first, second = world.sink
    assert first["reason_code"] == "CAPABILITY_MISSING" and first["capability"] == "schedule.approve"
    assert second["reason_code"] == "NO_RELATIONSHIP"
    for record in world.sink:
        assert record["policy_version"] == "uctsm-authz.v2"
        assert record["correlation_id"]
        assert set(record) >= {"actor_id", "organization_id", "role", "method", "route",
                               "resource_kind", "status"}
        flat = str(record)
        assert "P-A" not in flat and "Synthetic" not in flat      # no patient code / titles
    # the denial carries the canonical id only
    assert first["resource_id"] == world.version_a
