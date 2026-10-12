"""Behavioural parity of the demo seeder: SQL service vs port."""

from __future__ import annotations

from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.demo import DemoService as RepoDemo
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.services.demo_service import DemoService as SqlDemo

pytest_plugins = ["tests.contract.conftest"]

ACTOR = uuid4()


class SqlHarness:
    def __init__(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)

    def seed(self, organization_id):
        result = SqlDemo(self.session).seed(organization_id=organization_id, actor_id=ACTOR)
        self.session.commit()
        return result

    def describe(self, result):
        version = UUID_(result["schedule_version_id"])
        schedule = ScheduleRepository(self.session).get(version)
        patient = self.session.get(db.Patient, UUID_(result["patient_id"]))
        return shape(schedule, patient.patient_code, patient.current_schedule_version_id == version)

    def audit(self):
        return sorted(a.action for a in self.session.scalars(select(db.AuditEvent)))

    def trials(self):
        return len(list(self.session.scalars(select(db.Trial))))


class RepoHarness:
    def __init__(self, uow):
        self.uow = uow
        self.organization = None

    def seed(self, organization_id):
        return RepoDemo(self.uow).seed(organization_id=organization_id, actor_id=ACTOR)

    def describe(self, result):
        version = UUID_(result["schedule_version_id"])
        schedule = self.uow.read(lambda tx: tx.schedules.get(version))
        patient = self.uow.read(
            lambda tx: tx.patients.get(self.organization, UUID_(result["patient_id"])))
        return shape(schedule, patient.patient_code, patient.current_schedule_version_id == version)

    def audit(self):
        return sorted(a.action for a in self.uow.read(
            lambda tx: tx.audit.list_for_organization(self.organization, limit=100000)))



def UUID_(value):
    from uuid import UUID

    return UUID(value)


def shape(schedule, patient_code, pinned):
    return (
        schedule.schedule_metadata.name, schedule.schedule_metadata.status.value,
        sorted((e.code, e.event_type, getattr(e, "activation", None)) for e in schedule.events),
        sorted(a.code for a in schedule.anchors), patient_code, pinned)


def test_the_demo_seeds_one_trial_with_a_draft_schedule_and_a_pinned_patient(uow):
    organization = uuid4()
    sql = SqlHarness()
    repo = RepoHarness(uow)
    repo.organization = organization
    expected = sql.seed(organization)
    actual = repo.seed(organization)
    assert sql.describe(expected) == repo.describe(actual)
    assert sql.audit() == repo.audit() == ["UCTSM_DEMO_SEEDED"]
    sql.session.close()


def test_seeding_twice_returns_the_same_ids_and_writes_nothing_more(uow):
    organization = uuid4()
    sql = SqlHarness()
    repo = RepoHarness(uow)
    repo.organization = organization
    first_sql, first_repo = sql.seed(organization), repo.seed(organization)
    second_sql, second_repo = sql.seed(organization), repo.seed(organization)
    assert second_sql == first_sql and second_repo == first_repo
    assert sql.audit() == repo.audit() == ["UCTSM_DEMO_SEEDED"]
    assert sql.trials() == 1
    sql.session.close()


def test_two_organisations_get_isolated_demos(uow):
    sql = SqlHarness()
    repo = RepoHarness(uow)
    first, second = uuid4(), uuid4()
    repo.organization = first
    one_sql, two_sql = sql.seed(first), sql.seed(second)
    one_repo, two_repo = repo.seed(first), repo.seed(second)
    assert one_sql["trial_id"] != two_sql["trial_id"]
    assert one_repo["trial_id"] != two_repo["trial_id"]
    assert one_repo["patient_id"] != two_repo["patient_id"]
    sql.session.close()
