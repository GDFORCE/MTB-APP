"""The cross-trial "what needs my decision" list (Phase 4 / BUILD-SEQUENCE.md).

Replaces the legacy `schedule_reviews` Mongo inbox `clinical/schedule-review.tsx`
used to read. `ScheduleReviewService.list_pending_review` queries the same
tables `approve`/`reject` already act on directly, rather than a second,
independently-maintained record of "what's pending" that could drift from the
truth.
"""

from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.canonical_bridge import import_schedule_definitions  # noqa: E402
from app.services.schedule_service import ScheduleReviewService  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402,F401
import test_canonical_journey as journey  # noqa: E402


@pytest.fixture()
def session():
    from app.db.base import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def imported(session, *, organization_id, external_trial_id, protocol_number, study_title):
    trial, rows = import_schedule_definitions(
        session, organization_id=organization_id, actor_id=journey.REVIEWER,
        external_trial_id=external_trial_id, protocol_number=protocol_number,
        study_title=study_title, definitions=[journey.definition(f"sd-{external_trial_id}")],
    )
    session.commit()
    return trial, rows[0]


def test_a_draft_schedule_is_not_pending_review(session):
    organization_id = uuid4()
    imported(
        session, organization_id=organization_id, external_trial_id="trial-a",
        protocol_number="A-1", study_title="Trial A",
    )

    pending = ScheduleReviewService(session).list_pending_review(organization_id=organization_id)
    assert pending == []


def test_a_submitted_schedule_appears_in_its_organizations_inbox(session):
    organization_id = uuid4()
    trial, row = imported(
        session, organization_id=organization_id, external_trial_id="trial-b",
        protocol_number="B-1", study_title="Trial B",
    )
    ScheduleReviewService(session).submit_for_review(
        row.schedule_version_id, actor_id=journey.REVIEWER)
    session.commit()

    pending = ScheduleReviewService(session).list_pending_review(organization_id=organization_id)
    assert len(pending) == 1
    item = pending[0]
    assert item["trial_id"] == str(trial.id)
    assert item["trial_name"] == "Trial B"
    assert item["schedule_version_id"] == str(row.schedule_version_id)
    assert item["submitted_at"] is not None


def test_another_organizations_pending_schedule_is_invisible(session):
    """No cross-tenant leak: an org's inbox never shows another org's schedule."""
    own_org = uuid4()
    other_org = uuid4()
    imported(
        session, organization_id=own_org, external_trial_id="trial-c",
        protocol_number="C-1", study_title="Trial C",
    )
    _trial, other_row = imported(
        session, organization_id=other_org, external_trial_id="trial-d",
        protocol_number="D-1", study_title="Trial D",
    )
    ScheduleReviewService(session).submit_for_review(
        other_row.schedule_version_id, actor_id=journey.REVIEWER)
    session.commit()

    pending = ScheduleReviewService(session).list_pending_review(organization_id=own_org)
    assert pending == []


def test_an_approved_schedule_leaves_the_inbox(session):
    """`journey.approve` submits for review internally before approving."""
    organization_id = uuid4()
    _trial, row = imported(
        session, organization_id=organization_id, external_trial_id="trial-e",
        protocol_number="E-1", study_title="Trial E",
    )
    service = ScheduleReviewService(session)

    journey.approve(session, row.schedule_version_id)

    assert service.list_pending_review(organization_id=organization_id) == []


def test_a_rejected_schedule_leaves_the_inbox(session):
    organization_id = uuid4()
    _trial, row = imported(
        session, organization_id=organization_id, external_trial_id="trial-f",
        protocol_number="F-1", study_title="Trial F",
    )
    service = ScheduleReviewService(session)
    service.submit_for_review(row.schedule_version_id, actor_id=journey.REVIEWER)
    session.commit()

    service.reject(row.schedule_version_id, reviewer_id=journey.REVIEWER, reason="Not yet ready")
    session.commit()

    assert service.list_pending_review(organization_id=organization_id) == []
