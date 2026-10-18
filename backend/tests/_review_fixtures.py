"""Synthetic schedules and an API client for the Visit Schedule review tests.

Every schedule here is invented for the test. None of it is an interpretation
of a real protocol.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.uctsm import create_uctsm_router
from _authz_fixtures import permissive_authorizer
from app.db import models as db
from app.db.base import Base, get_session
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Activity, Anchor, ApplicabilityRule, ClaimEvidence, Evidence, Event, Qualifier,
    QualifierCategory, QualifierScope, ScheduleMetadata, ScheduleStatus, StudyDimension,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, NonNegativeTemporalAmount, OffsetTiming, ProtocolDayTiming,
    RangeTiming, TemporalAmount, Window,
)

USER = {
    "id": "00000000-0000-0000-0000-000000000001",
    "organization_id": "00000000-0000-0000-0000-000000000002",
    "role": "sponsor", "status": "Active",
}
API = "/api/uctsm"


async def authenticated_user():
    return USER


def build_client(role: str | None = None) -> tuple[TestClient, sessionmaker, object]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app = FastAPI()
    user_dependency = authenticated_user
    if role is not None:
        acting = {**USER, "role": role}

        def user_dependency():                     # noqa: F811 - role-specific stand-in
            return acting
    app.include_router(create_uctsm_router(
        user_dependency,
        authorizer=permissive_authorizer(USER["id"], USER["organization_id"])))
    app.dependency_overrides[get_session] = session_override
    return TestClient(app), factory, engine


def _claims(evidence_id, entity_type, entity_id, *claim_types):
    return [ClaimEvidence(evidence_id=evidence_id, claim_type=claim, claim_entity_type=entity_type,
                          claim_entity_id=entity_id, confidence=1) for claim in claim_types]


def seed_schedule(factory: sessionmaker, *, with_issues: bool) -> str:
    """Persist one synthetic draft and return its schedule version id.

    Clean variant: Screening (a RANGE, so a window is not applicable), Day 1
    (no window stated), Week 4 (a stated ±3 day window, an arm restriction and a
    resolved timing footnote that the compact row does not show).

    ``with_issues`` adds two visits counted from an anchor the draft never
    defines (one shared question, two affected rows) and one visit whose window
    footnote is unresolved (window Needs review).
    """
    with factory() as session:
        trial = db.Trial(organization_id=UUID(USER["organization_id"]), protocol_id="SYN-1",
                         study_title="Synthetic review trial", external_trial_id="SYN-EXT-1")
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="SYN-1")
        session.add(protocol)
        session.flush()
        protocol_version = db.ProtocolVersion(
            protocol_id=protocol.id, version_label="1.0", document_name="synthetic.pdf",
            document_uri="private://synthetic.pdf", document_hash="a" * 64,
            uploaded_by=UUID(USER["id"]),
        )
        session.add(protocol_version)
        session.flush()
        protocol.current_version_id = protocol_version.id
        definition = db.ScheduleDefinition(protocol_version_id=protocol_version.id, name="Synthetic schedule")
        session.add(definition)
        session.flush()

        evidence = Evidence(evidence_type="PROTOCOL_TEXT", page_number=12, section_title="Schedule",
                            source_text="Synthetic source sentence for the test.")
        baseline = Anchor(code="BASELINE", display_name="Randomisation", anchor_type="RANDOMISATION",
                          status="RESOLVED", derivation_rule={"role": "SCHEDULE_ORIGIN"},
                          evidence_refs=[evidence.id])
        arm = StudyDimension(code="A", protocol_label="Arm A", display_name="Arm A")
        ref = AnchorReference(code="BASELINE")
        screening = Event(
            code="SCREENING", protocol_label="Screening", display_name="Screening",
            event_type="VISIT", sequence_number=1,
            timing=RangeTiming(reference=ref, start=TemporalAmount(value=-28, unit="DAY"),
                               end=TemporalAmount(value=-1, unit="DAY")),
            evidence_refs=[evidence.id],
        )
        day1 = Event(
            code="DAY_1", protocol_label="Day 1", display_name="Day 1", event_type="VISIT",
            sequence_number=2, timing=ProtocolDayTiming(reference=ref, day=1),
            evidence_refs=[evidence.id],
        )
        vitals = Activity(code="VITALS", protocol_label="Vital signs", display_name="Vital signs",
                          activity_type="ASSESSMENT", evidence_refs=[evidence.id])
        timing_note = Qualifier(marker="a", text="Collect pre-dose.", scope=QualifierScope.VISIT,
                                category=QualifierCategory.TIMING, resolved=True,
                                target_codes=["WEEK_4"], evidence_refs=[evidence.id])
        week4 = Event(
            code="WEEK_4", protocol_label="Week 4", display_name="Week 4", event_type="VISIT",
            sequence_number=3,
            timing=OffsetTiming(reference=ref, offset=TemporalAmount(value=28, unit="DAY")),
            window=Window(before=NonNegativeTemporalAmount(value=3, unit="DAY"),
                          after=NonNegativeTemporalAmount(value=3, unit="DAY")),
            applicability=[ApplicabilityRule(dimension="ARM", values=["A"])],
            qualifiers=[timing_note], activities=[vitals], evidence_refs=[evidence.id],
        )
        events = [screening, day1, week4]
        claims = []
        for event in events:
            claims += _claims(evidence.id, "EVENT", event.id, "EVENT_NAME", "TIMING")
        claims += _claims(evidence.id, "EVENT", week4.id, "WINDOW", "APPLICABILITY")
        claims += _claims(evidence.id, "ACTIVITY", vitals.id, "ACTIVITY")
        claims += _claims(evidence.id, "QUALIFIER", timing_note.id, "QUALIFIER")
        if with_issues:
            surgery = AnchorReference(code="SURGERY")
            post_op_1 = Event(
                code="POST_OP_1", protocol_label="Post-op 1", display_name="Post-op check 1",
                event_type="VISIT", sequence_number=4,
                timing=OffsetTiming(reference=surgery, offset=TemporalAmount(value=7, unit="DAY")),
                evidence_refs=[evidence.id],
            )
            post_op_2 = Event(
                code="POST_OP_2", protocol_label="Post-op 2", display_name="Post-op check 2",
                event_type="VISIT", sequence_number=5,
                timing=OffsetTiming(reference=surgery, offset=TemporalAmount(value=2, unit="MONTH")),
                evidence_refs=[evidence.id],
            )
            window_note = Qualifier(marker="b", text="Window per local practice.",
                                    scope=QualifierScope.VISIT, category=QualifierCategory.WINDOW,
                                    resolved=False, target_codes=["MONTH_6"],
                                    evidence_refs=[evidence.id])
            month6 = Event(
                code="MONTH_6", protocol_label="Month 6", display_name="Month 6", event_type="VISIT",
                sequence_number=6,
                timing=OffsetTiming(reference=ref, offset=TemporalAmount(value=6, unit="MONTH")),
                qualifiers=[window_note], evidence_refs=[evidence.id],
            )
            claims += _claims(evidence.id, "QUALIFIER", window_note.id, "QUALIFIER")
            for event in (post_op_1, post_op_2, month6):
                claims += _claims(evidence.id, "EVENT", event.id, "EVENT_NAME", "TIMING")
            events += [post_op_1, post_op_2, month6]
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name="Synthetic schedule", protocol_version_id=protocol_version.id,
                status=ScheduleStatus.VALIDATION_REQUIRED,
            ),
            anchors=[baseline], arms=[arm], events=events, evidence=[evidence], claim_evidence=claims,
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()
        return str(schedule.schedule_version_id)


def view(client: TestClient, version_id: str) -> dict:
    response = client.get(f"{API}/schedule-versions/{version_id}/review-view")
    assert response.status_code == 200, response.text
    return response.json()


def row(view_body: dict, code: str) -> dict:
    return next(item for item in view_body["rows"] if item["code"] == code)


def new_id() -> str:
    return str(uuid4())
