"""Add Trial's native-extraction path must produce canonical draft versions,
once, per detected schedule option - the counterpart to
test_canonical_bridge.py for import_native_schedules, which persists an
already-computed ExtractionResult instead of translating a legacy plan.
"""

from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, Event, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import UnresolvedTiming
from app.services.native_import import import_native_schedules

ORG = uuid4()
ACTOR = uuid4()


def native_schedule(*, name: str = "Primary", event_code: str = "C1D1") -> dict:
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name=name, status=ScheduleStatus.EXTRACTED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        events=[Event(
            code=event_code, protocol_label=event_code, display_name=event_code,
            event_type="SITE_VISIT",
            timing=UnresolvedTiming(reason="test fixture - no real timing needed"),
        )],
    )
    return schedule.model_dump(mode="json")


def definition(external_id: str, *, schedule=None) -> dict:
    return {
        "id": external_id,
        "native_schedule": schedule if schedule is not None else native_schedule(),
        "native_issues": [],
    }


def session_factory() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def run(session, definitions, *, external_trial_id="trial-1"):
    return import_native_schedules(
        session, organization_id=ORG, actor_id=ACTOR,
        external_trial_id=external_trial_id, protocol_number="ABC-123",
        study_title="A study", document_uri="private://protocol-uploads/abc.pdf",
        document_name="protocol.pdf", document_hash="h" * 64, definitions=definitions,
    )


def test_import_creates_a_reviewable_draft_linked_to_the_operational_trial():
    with session_factory() as session:
        trial, imported = run(session, [definition("sd-1")])
        session.commit()

        assert trial.external_trial_id == "trial-1"
        assert len(imported) == 1
        row = imported[0]
        assert row.created is True
        assert row.version_number == 1
        # Doc 10: an AI draft is never approved on arrival.
        assert row.status == "VALIDATION_REQUIRED"

        definition_row = session.get(db.ScheduleDefinition, row.schedule_definition_id)
        assert definition_row.external_schedule_definition_id == "sd-1"

        schedule = ScheduleRepository(session).get(row.schedule_version_id)
        assert [event.code for event in schedule.events] == ["C1D1"]


def test_the_protocol_version_points_at_the_real_uploaded_document():
    """Unlike canonical_bridge's synthetic, never-opened
    private://protocol-extractions/{id} URI, the native path's protocol
    version must be genuinely resolvable back to the uploaded PDF."""
    with session_factory() as session:
        run(session, [definition("sd-1")])
        session.commit()

        version = session.scalar(select(db.ProtocolVersion))
        assert version.document_uri == "private://protocol-uploads/abc.pdf"
        assert version.document_hash == "h" * 64


def test_reimporting_the_same_extraction_reuses_the_draft_under_review():
    with session_factory() as session:
        _, first = run(session, [definition("sd-1")])
        session.commit()
        _, second = run(session, [definition("sd-1")])
        session.commit()

        assert second[0].created is False
        assert second[0].schedule_version_id == first[0].schedule_version_id
        assert len(list(session.scalars(select(db.ScheduleVersion)))) == 1


def test_every_detected_schedule_option_becomes_its_own_canonical_definition_sharing_one_protocol_version():
    """All options came from the SAME real PDF - one ProtocolVersion, several
    ScheduleDefinitions, unlike canonical_bridge's per-definition versions."""
    with session_factory() as session:
        _, imported = run(session, [
            definition("sd-1", schedule=native_schedule(name="Substudy A", event_code="A1")),
            definition("sd-2", schedule=native_schedule(name="Substudy B", event_code="B1")),
        ])
        session.commit()

        assert [item.name for item in imported] == ["Substudy A", "Substudy B"]
        assert len({item.schedule_definition_id for item in imported}) == 2
        assert len(list(session.scalars(select(db.ProtocolVersion)))) == 1


def test_a_definition_without_a_native_schedule_is_skipped_not_imported_empty():
    with session_factory() as session:
        _, imported = run(session, [
            {"id": "sd-1", "native_schedule": None},
            {"id": "sd-2", "native_schedule": {"events": []}},
        ])
        session.commit()
        assert imported == []
        assert list(session.scalars(select(db.ScheduleVersion))) == []


def test_a_second_upload_adds_a_version_without_replacing_the_one_in_use():
    """An amendment must not silently re-point the protocol's current version."""
    with session_factory() as session:
        _, first = run(session, [definition("sd-1")])
        session.commit()
        protocol = session.scalar(select(db.Protocol))
        pinned = protocol.current_version_id

        _, second = import_native_schedules(
            session, organization_id=ORG, actor_id=ACTOR,
            external_trial_id="trial-1", protocol_number="ABC-123", study_title="A study",
            document_uri="private://protocol-uploads/def.pdf", document_name="protocol-v2.pdf",
            document_hash="i" * 64, definitions=[definition("sd-2")],
        )
        session.commit()

        assert second[0].schedule_version_id != first[0].schedule_version_id
        session.refresh(protocol)
        assert protocol.current_version_id == pinned
        assert len(list(session.scalars(select(db.ProtocolVersion)))) == 2
