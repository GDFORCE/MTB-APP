"""Behavioural parity of extraction runs and schedule import: SQL vs port."""

from __future__ import annotations

from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application import extraction as repo_extraction
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, Event, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import UnresolvedTiming
from app.services import native_import as sql_native

pytest_plugins = ["tests.contract.conftest"]

ORG = UUID("55555555-5555-4555-8555-555555555555")
ACTOR = UUID("66666666-6666-4666-8666-666666666666")


def native_schedule(*, name: str = "Primary", event_code: str = "C1D1") -> dict:
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name=name, status=ScheduleStatus.EXTRACTED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=[Event(
            code=event_code, protocol_label=event_code, display_name=event_code,
            event_type="SITE_VISIT",
            timing=UnresolvedTiming(reason="test fixture - no real timing needed"))])
    return schedule.model_dump(mode="json")


def definition(external_id: str, *, schedule=None, name="Primary") -> dict:
    return {
        "id": external_id,
        "native_schedule": schedule if schedule is not None else native_schedule(name=name),
        "native_issues": [],
    }


class SqlStack:
    def __init__(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)

    def native(self, definitions, *, trial="trial-1", document_hash="h" * 64, label=""):
        trial_row, imported = sql_native.import_native_schedules(
            self.session, organization_id=ORG, actor_id=ACTOR, external_trial_id=trial,
            protocol_number="ABC-123", study_title="A study",
            document_uri="private://protocol-uploads/abc.pdf", document_name="protocol.pdf",
            document_hash=document_hash, version_label=label, definitions=definitions)
        self.session.commit()
        return trial_row.external_trial_id, imported

    def summary(self, trial_external_id: str):
        s = self.session
        trial = s.scalar(select(db.Trial).where(db.Trial.external_trial_id == trial_external_id))
        protocol = s.scalar(select(db.Protocol).where(db.Protocol.trial_id == trial.id))
        versions = list(s.scalars(select(db.ProtocolVersion).where(
            db.ProtocolVersion.protocol_id == protocol.id).order_by(db.ProtocolVersion.version_label)))
        current = s.get(db.ProtocolVersion, protocol.current_version_id)
        definitions = list(s.scalars(select(db.ScheduleDefinition).join(
            db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id,
        ).where(db.ProtocolVersion.protocol_id == protocol.id).order_by(db.ScheduleDefinition.name)))
        schedules = []
        for d in definitions:
            for v in s.scalars(select(db.ScheduleVersion).where(
                    db.ScheduleVersion.schedule_definition_id == d.id).order_by(
                        db.ScheduleVersion.version_number)):
                events = [e.code for e in ScheduleRepository(s).get(v.id).events]
                schedules.append((d.name, d.external_schedule_definition_id, v.version_number,
                                  v.status, tuple(events)))
        runs = sorted((r.status, r.idempotency_key, r.provider, r.prompt_version)
                      for r in s.scalars(select(db.ExtractionRun)))
        audit = sorted(a.action for a in s.scalars(select(db.AuditEvent)))
        return {
            "trial": (trial.study_title, trial.protocol_id),
            "protocol_versions": [(v.version_label, v.document_name, v.extraction_status,
                                   v.document_uri) for v in versions],
            "current_label": current.version_label if current else None,
            "schedules": schedules, "runs": runs, "audit": audit,
        }


class RepoStack:
    def __init__(self, uow):
        self.uow = uow

    def native(self, definitions, *, trial="trial-1", document_hash="h" * 64, label=""):
        trial_row, imported = repo_extraction.import_native_schedules(
            self.uow, organization_id=ORG, actor_id=ACTOR, external_trial_id=trial,
            protocol_number="ABC-123", study_title="A study",
            document_uri="private://protocol-uploads/abc.pdf", document_name="protocol.pdf",
            document_hash=document_hash, version_label=label, definitions=definitions)
        return trial_row.external_trial_id, imported

    def summary(self, trial_external_id: str):
        def work(tx):
            trial = tx.authoring.find_trial_by_external_id(ORG, trial_external_id)
            protocol = tx.authoring.find_protocol_for_trial(trial.id)
            headers = tx.schedules.list_headers(organization_id=ORG, trial_id=trial.id)
            labels = {}
            for h in headers:
                labels.setdefault(h.protocol_version_id, None)
            versions = []
            for version_id in {h.protocol_version_id for h in headers}:
                versions.append(tx.authoring.get_protocol_version(version_id))
            versions.sort(key=lambda v: v.version_label)
            current = tx.authoring.get_protocol_version(protocol.current_version_id) \
                if protocol.current_version_id else None
            schedules = []
            for h in headers:
                d = tx.authoring.get_definition(h.schedule_definition_id)
                events = [e.code for e in tx.schedules.get(h.id).events]
                schedules.append((d.name, d.external_schedule_definition_id, h.version_number,
                                  h.status, tuple(events)))
            schedules.sort(key=lambda r: (r[0], r[2]))
            runs = []
            for h in headers:
                run = tx.authoring.get_run(h.extraction_run_id)
                runs.append((run.status, run.idempotency_key, run.provider, run.prompt_version))
            actions = sorted(a.action for a in tx.audit.list_for_organization(ORG, limit=10000))
            return {
                "trial": (trial.study_title, trial.protocol_id),
                "protocol_versions": [(v.version_label, v.document_name, v.extraction_status,
                                       v.document_uri) for v in versions],
                "current_label": current.version_label if current else None,
                "schedules": schedules, "runs": sorted(runs), "audit": actions,
            }

        return self.uow.read(work)


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


def shape(imported):
    return [(i.external_schedule_definition_id, i.version_number, i.status, i.name, i.created)
            for i in imported]


def both(sql_stack, repo_stack, scenario):
    expected = scenario(sql_stack)
    actual = scenario(repo_stack)
    assert actual == expected
    return actual


def test_a_native_import_creates_one_reviewable_draft_per_option(sql_stack, repo_stack):
    def scenario(stack):
        trial, imported = stack.native([
            definition("sd-1"), definition("sd-2", name="Substudy", schedule=native_schedule(
                name="Substudy", event_code="V1"))])
        return shape(imported), stack.summary(trial)

    imported, summary = both(sql_stack, repo_stack, scenario)
    assert [row[2] for row in imported] == ["VALIDATION_REQUIRED", "VALIDATION_REQUIRED"]
    assert all(row[4] for row in imported)
    assert summary["current_label"] == "Extracted 1"


def test_a_retried_import_reuses_the_draft_instead_of_creating_a_second(sql_stack, repo_stack):
    def scenario(stack):
        stack.native([definition("sd-1")])
        trial, again = stack.native([definition("sd-1")])
        return shape(again), stack.summary(trial)

    again, summary = both(sql_stack, repo_stack, scenario)
    assert again == [("sd-1", 1, "VALIDATION_REQUIRED", "Primary", False)]
    assert len(summary["schedules"]) == 1


def test_an_option_with_no_visits_is_skipped_not_imported_as_an_empty_schedule(sql_stack, repo_stack):
    def scenario(stack):
        empty = native_schedule()
        empty["events"] = []
        trial, imported = stack.native([
            definition("sd-empty", schedule=empty), {"id": "sd-none", "native_issues": []},
            definition("sd-ok")])
        return shape(imported), stack.summary(trial)["schedules"]

    imported, schedules = both(sql_stack, repo_stack, scenario)
    assert [row[0] for row in imported] == ["sd-ok"] and len(schedules) == 1


def test_a_second_document_becomes_a_new_protocol_version_without_taking_over(sql_stack, repo_stack):
    def scenario(stack):
        stack.native([definition("sd-1")], document_hash="a" * 64, label="1.0")
        trial, second = stack.native(
            [definition("sd-2")], document_hash="b" * 64, label="1.0")
        return shape(second), stack.summary(trial)

    second, summary = both(sql_stack, repo_stack, scenario)
    labels = [row[0] for row in summary["protocol_versions"]]
    assert labels == ["1.0", "1.0 (2)"]           # same label stays distinguishable
    assert summary["current_label"] == "1.0"       # an import never switches enrolment


def test_the_same_document_hash_reuses_its_protocol_version(sql_stack, repo_stack):
    def scenario(stack):
        stack.native([definition("sd-1")], document_hash="c" * 64)
        trial, second = stack.native(
            [definition("sd-2", schedule=native_schedule(event_code="C2D1"))],
            document_hash="c" * 64)
        return shape(second), stack.summary(trial)

    second, summary = both(sql_stack, repo_stack, scenario)
    assert len(summary["protocol_versions"]) == 1


def test_a_failed_extraction_run_is_recorded_as_failed(sql_stack, repo_stack):
    """An option set with no usable schedule marks the run FAILED and raises."""
    from app.extraction.contracts import ExtractionResult

    def scenario(stack):
        if isinstance(stack, SqlStack):
            from app.services.extraction_service import ExtractionService

            service = ExtractionService(stack.session)
        else:
            service = repo_extraction.ExtractionService(stack.uow)
        # one imported draft gives us a protocol version and a QUEUED-able run
        trial, _ = stack.native([definition("sd-1")])
        return trial, service

    trial_sql, sql_service = scenario(sql_stack)
    trial_repo, repo_service = scenario(repo_stack)

    # queue a fresh run on each, then complete it with an unusable result
    sql_pv = sql_stack.session.scalar(select(db.ProtocolVersion))
    sql_run = sql_service.queue(
        organization_id=ORG, protocol_version=sql_pv, idempotency_key="fail-1", provider="p",
        model_name="m", model_version=None, prompt_version="v1")
    sql_stack.session.commit()
    nothing = ExtractionResult(schedule=None, issues=[], extraction_trace=[])
    with pytest.raises(ValueError, match="did not produce any schema-valid schedule"):
        sql_service.complete_many(sql_run.id, [(None, nothing)])
    sql_stack.session.commit()
    sql_status = sql_stack.session.get(db.ExtractionRun, sql_run.id).status

    repo_pv_id = repo_stack.uow.read(
        lambda tx: tx.schedules.list_headers(organization_id=ORG)[0].protocol_version_id)
    repo_run = repo_service.queue(
        organization_id=ORG, protocol_version_id=repo_pv_id, idempotency_key="fail-1",
        provider="p", model_name="m", model_version=None, prompt_version="v1")
    with pytest.raises(ValueError, match="did not produce any schema-valid schedule"):
        repo_service.complete_many(repo_run.id, [(None, nothing)])
    repo_status = repo_stack.uow.read(lambda tx: tx.authoring.get_run(repo_run.id)).status
    assert repo_status == sql_status == "FAILED"          # the FAILED state is committed


def test_an_idempotency_key_cannot_be_reused_for_different_inputs(sql_stack, repo_stack):
    def scenario(stack):
        stack.native([definition("sd-1")])
        if isinstance(stack, SqlStack):
            from app.services.extraction_service import ExtractionService

            pv = stack.session.scalar(select(db.ProtocolVersion))
            service = ExtractionService(stack.session)
            first = service.queue(
                organization_id=ORG, protocol_version=pv, idempotency_key="k", provider="p",
                model_name="m1", model_version=None, prompt_version="v1")
            stack.session.commit()
            try:
                service.queue(organization_id=ORG, protocol_version=pv, idempotency_key="k",
                              provider="p", model_name="m2", model_version=None, prompt_version="v1")
            except ValueError as error:
                return ("error", str(error))
            return ("no error", first.model_name)
        pv_id = stack.uow.read(
            lambda tx: tx.schedules.list_headers(organization_id=ORG)[0].protocol_version_id)
        service = repo_extraction.ExtractionService(stack.uow)
        service.queue(organization_id=ORG, protocol_version_id=pv_id, idempotency_key="k",
                      provider="p", model_name="m1", model_version=None, prompt_version="v1")
        try:
            service.queue(organization_id=ORG, protocol_version_id=pv_id, idempotency_key="k",
                          provider="p", model_name="m2", model_version=None, prompt_version="v1")
        except ValueError as error:
            return ("error", str(error))
        return ("no error", "m1")

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome == ("error", "idempotency key was already used for different extraction inputs")


def test_the_trial_version_table_lists_every_version_with_its_patients(sql_stack, repo_stack):
    def scenario(stack):
        stack.native([definition("sd-1")], document_hash="a" * 64)
        stack.native([definition("sd-2")], document_hash="b" * 64)
        if isinstance(stack, SqlStack):
            from app.services.canonical_bridge import list_schedule_versions

            rows = list_schedule_versions(
                stack.session, organization_id=ORG, external_trial_id="trial-1")
        else:
            rows = repo_extraction.list_schedule_versions(
                stack.uow, organization_id=ORG, external_trial_id="trial-1")
        return [(r.name, r.version_number, r.status, r.protocol_version_label,
                 r.is_current_protocol_version, r.patients) for r in rows]

    rows = both(sql_stack, repo_stack, scenario)
    assert len(rows) == 2 and sum(1 for r in rows if r[4]) == 1


def test_a_failed_import_leaves_nothing_behind(repo_stack):
    broken = native_schedule()
    broken["events"][0]["timing"] = {"type": "NOT_A_TIMING"}
    with pytest.raises(Exception):
        repo_stack.native([definition("sd-1"), definition("sd-2", schedule=broken)])
    assert repo_stack.uow.read(
        lambda tx: tx.authoring.find_trial_by_external_id(ORG, "trial-1")) is None
