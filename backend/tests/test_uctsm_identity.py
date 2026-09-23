from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.identity import activity_logical_key, event_logical_key
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.schedule_service import PatientScheduleService, ScheduleReviewService


def test_identity_helpers_do_not_reintroduce_the_old_mismatch():
    """AD-4f: a generated activity's key must equal the key used to record its actual.

    Before ``identity.py`` existed, ``_persist_activities`` built
    ``f"{event.logical_key}#{activity_code}"`` (``"DAY_30:0#WEIGHT"``) while
    ``record_activity``/the evaluator's actual-time lookup built
    ``f"{event_code}#{occurrence_index}#{activity_code}"`` (``"DAY_30#0#WEIGHT"``)
    -- disagreeing formats for the identical activity, latent only because
    nothing cross-joined them yet.
    """
    assert activity_logical_key("DAY_30", 0, "WEIGHT") == "DAY_30#0#WEIGHT"
    assert event_logical_key("DAY_30", 0) == "DAY_30:0"
    assert activity_logical_key("DAY_30", 0, "WEIGHT") != f"{event_logical_key('DAY_30', 0)}#WEIGHT"


def test_generated_activity_key_matches_recorded_actual_key():
    """End-to-end: the persisted PatientActivity row and a recorded
    PatientActivityRecord for the same activity must share one logical_key, so a
    future join between them (the exact caller the architecture review flagged
    as expected but not yet built) returns the row it should, instead of
    silently returning nothing.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    ids = {name: uuid4() for name in (
        "organization", "trial", "protocol", "protocol_version", "definition",
        "patient", "reviewer",
    )}
    with Session(engine) as session:
        session.add_all([
            db.Trial(id=ids["trial"], organization_id=ids["organization"]),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="ABC-123"),
            db.ProtocolVersion(
                id=ids["protocol_version"], protocol_id=ids["protocol"], version_label="1",
                document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary",
            ),
        ])
        session.flush()
        evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Day 1 visit")
        anchor = Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")
        activity = Activity(
            code="WEIGHT", protocol_label="Weight", display_name="Weight",
            activity_type="ASSESSMENT", evidence_refs=[evidence.id],
        )
        event = Event(
            code="C1D1", protocol_label="Cycle 1 Day 1", display_name="Cycle 1 Day 1",
            event_type="SITE_VISIT",
            timing=OffsetTiming(
                reference=AnchorReference(code="BASELINE"),
                offset=TemporalAmount(value=0, unit="DAY"),
            ),
            activities=[activity],
            evidence_refs=[evidence.id],
        )
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name="Primary", protocol_version_id=ids["protocol_version"],
                status=ScheduleStatus.VALIDATION_REQUIRED,
            ),
            anchors=[anchor], events=[event], evidence=[evidence],
            claim_evidence=[
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="display_name",
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="timing",
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="ACTIVITY", claim_entity_type="ACTIVITY",
                    claim_entity_id=activity.id,
                ),
            ],
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=ids["definition"])
        session.flush()
        review = ScheduleReviewService(session)
        review.submit_for_review(schedule.schedule_version_id, actor_id=ids["reviewer"])
        for path in ("display_name", "timing", "activities"):
            review.record_decision(
                schedule.schedule_version_id, reviewer_id=ids["reviewer"], decision="CONFIRM",
                entity_type="EVENT", entity_id=event.id, field_path=path,
            )
        review.approve(schedule.schedule_version_id, reviewer_id=ids["reviewer"])
        session.add(db.Patient(
            id=ids["patient"], organization_id=ids["organization"], trial_id=ids["trial"],
            patient_code="P001", current_schedule_version_id=schedule.schedule_version_id,
        ))
        session.add(db.PatientAnchor(
            patient_id=ids["patient"], anchor_definition_id=anchor.id,
            value_date=date(2026, 9, 1), status="CONFIRMED",
        ))
        session.commit()

        service = PatientScheduleService(session)
        _, events = service.evaluate(
            ids["patient"], organization_id=ids["organization"], horizon=date(2027, 1, 1),
        )
        session.commit()

        generated_activity = session.scalar(
            select(db.PatientActivity).where(db.PatientActivity.patient_event_id == events[0].id)
        )
        assert generated_activity is not None

        record = service.record_activity(
            ids["patient"], organization_id=ids["organization"], event_code="C1D1",
            occurrence_index=0, activity_code="WEIGHT", status="COMPLETED",
            actual_time=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), actor_id=ids["reviewer"],
        )

        assert generated_activity.logical_key == record.logical_key == "C1D1#0#WEIGHT"
