"""AD-10 Step 1a's disclosed follow-up: since reads no longer silently
extend a patient's materialized horizon (server.py::_apply_canonical_dates
now only reads), something deliberate must do that extending instead -
server.py::_extend_stale_horizons_sync / extend_stale_horizons.
"""
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.db.base as db_base  # noqa: E402
from app.db import models as db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.repositories import ScheduleRepository  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import (  # noqa: E402
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount, OffsetTiming,
    TemporalAmount, Window,
)

import server  # noqa: E402

ORG = uuid4()
BASELINE = date(2026, 1, 1)


def windowed(days: int) -> NominalWindowTiming:
    return NominalWindowTiming(
        nominal=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        window=Window(
            before=NonNegativeTemporalAmount(value=1, unit="DAY"),
            after=NonNegativeTemporalAmount(value=1, unit="DAY"),
        ),
    )


def _schedule(protocol_version_id, *, day_offset: int) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_ROW", page_number=1, source_text="V1")
    event = Event(
        code="V1", protocol_label="V1", display_name="Visit 1",
        event_type="SITE_VISIT", timing=windowed(day_offset),
        evidence_refs=[evidence.id],
    )
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=1.0,
        )
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        events=[event], evidence=[evidence], claim_evidence=claims,
    )


def _seed_patient(session: Session, *, today: date, days_out: int, external_id: str) -> db.Patient:
    ids = {name: uuid4() for name in ("trial", "protocol", "protocol_version", "definition")}
    session.add_all([
        db.Trial(id=ids["trial"], organization_id=ORG, external_trial_id=f"trial-{external_id}"),
        db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
        db.ProtocolVersion(
            id=ids["protocol_version"], protocol_id=ids["protocol"],
            version_label=external_id, document_name="protocol.pdf",
            document_uri="private://protocol.pdf", document_hash=external_id.ljust(64, "0"),
        ),
        db.ScheduleDefinition(
            id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary"),
    ])
    session.flush()
    schedule = _schedule(ids["protocol_version"], day_offset=days_out)
    ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=ids["definition"])
    patient = db.Patient(
        organization_id=ORG, trial_id=ids["trial"], patient_code=f"P-{external_id}",
        external_patient_id=external_id,
        current_schedule_version_id=schedule.schedule_version_id,
    )
    session.add(patient)
    session.flush()
    session.add(db.PatientAnchor(
        patient_id=patient.id, anchor_definition_id=schedule.anchors[0].id,
        value_date=today, status="CONFIRMED",
    ))
    session.commit()
    from app.services.schedule_service import PatientScheduleService
    PatientScheduleService(session).evaluate(
        patient.id, organization_id=ORG, horizon=today + timedelta(days=days_out + 5),
        schedule_version_id=schedule.schedule_version_id,
        actor_id=None, reason="test setup",
    )
    session.commit()
    return patient


def test_a_patient_inside_the_threshold_is_extended_and_one_outside_it_is_not(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_base, "_session_factory", factory)

    today = date(2026, 9, 5)

    # _extend_stale_horizons_sync's "today" comes from server.now().date() -
    # pin it so the threshold math is deterministic regardless of wall-clock time.
    import datetime as _dt

    def fixed_now():
        return _dt.datetime.combine(today, _dt.time(12, 0), tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(server, "now", fixed_now)

    with factory() as session:
        stale = _seed_patient(session, today=today, days_out=30, external_id="stale-patient")
        _seed_patient(session, today=today, days_out=400, external_id="fresh-patient")

    results = server._extend_stale_horizons_sync(
        server.HORIZON_REFRESH_THRESHOLD_DAYS, server.HORIZON_REFRESH_EXTEND_DAYS)

    by_external_id = {item["external_patient_id"]: item for item in results}
    assert by_external_id["stale-patient"]["status"] == "EXTENDED"
    assert "fresh-patient" not in by_external_id

    with factory() as session:
        # The seeded schedule has one fixed-offset event, so a bigger horizon
        # produces no NEW dated occurrence to check (that only matters for an
        # open-ended repeat) - what this test can and does check is that the
        # stale patient was actually re-evaluated through the real,
        # now-attributed chokepoint, not a second, differently-shaped path.
        audit = session.scalar(select(db.AuditEvent).where(
            db.AuditEvent.entity_id == stale.id,
            db.AuditEvent.reason == "Scheduled rolling-horizon extension",
        ))
        assert audit is not None
        assert audit.actor_id is None
