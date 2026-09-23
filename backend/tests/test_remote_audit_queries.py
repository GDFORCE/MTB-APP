"""Query and CAPA state machines — spec §11, §16.9.

Written against the shortcut implementations, not just the correct one. The
failures these guard are all cases where a plausible simplification quietly
loses information an inspector would later ask for: an edited response, a
reopen that resets history, a CAPA closed by the person who did the work, an
`overdue` flag that stopped being true.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import queries as q  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN = uuid.uuid4().hex[:8]
AUDIT = f"audit-{RUN}"
REVIEWER = f"reviewer-{RUN}"
SITE = f"site-user-{RUN}"
TODAY = datetime.now(timezone.utc).date()


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await q.ensure_indexes(database)
    yield database
    await database[q.QUERIES_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})
    await database[q.CAPAS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})


async def new_query(db, **kwargs):
    return await q.create_query(
        db, audit_id=kwargs.pop("audit_id", AUDIT), raised_by=REVIEWER,
        title=kwargs.pop("title", "Fridge temperature log incomplete"),
        detail=kwargs.pop("detail", "Three days are missing from the log."), **kwargs)


async def new_capa(db, **kwargs):
    return await q.create_capa(
        db, audit_id=kwargs.pop("audit_id", AUDIT), created_by=REVIEWER,
        title=kwargs.pop("title", "Re-train staff on temperature logging"),
        description=kwargs.pop("description", "Retrain and re-verify."),
        owner_user_id=kwargs.pop("owner_user_id", SITE),
        due_date=kwargs.pop("due_date", (TODAY + timedelta(days=30)).isoformat()),
        **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Queries
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_new_query_is_open_and_carries_its_criticality(db):
    query = await new_query(db, criticality="critical")
    assert query["status"] == q.QUERY_OPEN
    assert query["criticality"] == "critical"
    assert query["reopen_count"] == 0


@pytest.mark.asyncio
async def test_a_query_needs_a_title(db):
    with pytest.raises(q.QueryError) as exc:
        await new_query(db, title="   ")
    assert exc.value.code == "TITLE_REQUIRED"


def test_the_transition_table_covers_only_real_states():
    for origin, targets in q.QUERY_TRANSITIONS.items():
        assert origin in q.QUERY_STATES
        assert targets <= q.QUERY_STATES, f"{origin} names a state that does not exist"
    assert q.QUERY_TRANSITIONS[q.QUERY_CANCELLED] == set(), "CANCELLED must be terminal"


def test_a_closed_query_can_only_be_reopened():
    """Not re-answered, not re-closed, not cancelled. Reopening is the only
    way back in, and it leaves a trace."""
    assert q.QUERY_TRANSITIONS[q.QUERY_CLOSED] == {q.QUERY_REOPENED}


@pytest.mark.asyncio
async def test_the_full_answer_and_close_cycle(db):
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Logs attached.")
    assert (await q.get_query(db, query["id"]))["status"] == q.QUERY_RESPONDED

    closed = await q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER,
                                 reason_code="SATISFACTORY")
    assert closed["status"] == q.QUERY_CLOSED
    assert closed["closed_by"] == REVIEWER


@pytest.mark.asyncio
async def test_responses_are_append_only_and_ordered(db):
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="First answer.")
    await q.reject_response(db, query_id=query["id"], reviewer_id=REVIEWER,
                            reason_code="INSUFFICIENT_DETAIL")
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Second answer, fuller.")

    responses = await q.list_responses(db, query["id"])
    assert [r["sequence"] for r in responses] == [1, 2]
    # The superseded first answer is still there verbatim. Overwriting it
    # would change what the record says the site originally said.
    assert responses[0]["body"] == "First answer."
    assert responses[1]["body"] == "Second answer, fuller."


@pytest.mark.asyncio
async def test_rejecting_a_response_requires_a_reason_code(db):
    """"Rejected, no reason given" is not something a site can act on or an
    inspector can later interpret."""
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Answer.")
    with pytest.raises(q.QueryError) as exc:
        await q.reject_response(db, query_id=query["id"], reviewer_id=REVIEWER, reason_code="")
    assert exc.value.code == "REASON_CODE_REQUIRED"
    assert (await q.get_query(db, query["id"]))["status"] == q.QUERY_RESPONDED


@pytest.mark.asyncio
async def test_rejection_returns_the_query_to_open_with_the_reason_recorded(db):
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Answer.")
    rejected = await q.reject_response(
        db, query_id=query["id"], reviewer_id=REVIEWER, reason_code="INSUFFICIENT_DETAIL")
    assert rejected["status"] == q.QUERY_OPEN
    assert rejected["last_rejection_reason_code"] == "INSUFFICIENT_DETAIL"


@pytest.mark.asyncio
async def test_reopening_preserves_every_prior_response(db):
    """REOPENED is a state, not a reset. The thread an inspector asks about
    is the whole thread, including what was said before it was closed."""
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Original answer.")
    await q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER)

    reopened = await q.reopen_query(db, query_id=query["id"], reviewer_id=REVIEWER,
                                    reason="New evidence contradicts the original answer.")
    assert reopened["status"] == q.QUERY_REOPENED
    assert reopened["reopen_count"] == 1
    assert reopened["reopen_reason"] == "New evidence contradicts the original answer."
    # And the closure itself is still on the record.
    assert reopened["closed_by"] == REVIEWER

    responses = await q.list_responses(db, query["id"])
    assert len(responses) == 1 and responses[0]["body"] == "Original answer."

    # A reopened query can be answered again, continuing the same thread.
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Revised answer.")
    assert len(await q.list_responses(db, query["id"])) == 2


@pytest.mark.asyncio
async def test_reopening_needs_a_reason(db):
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Answer.")
    await q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER)
    with pytest.raises(q.QueryError) as exc:
        await q.reopen_query(db, query_id=query["id"], reviewer_id=REVIEWER, reason="  ")
    assert exc.value.code == "REOPEN_REASON_REQUIRED"


@pytest.mark.asyncio
async def test_an_open_query_cannot_be_closed_without_an_answer(db):
    """Closing an unanswered query would record that a finding was resolved
    when nobody ever addressed it."""
    query = await new_query(db)
    with pytest.raises(q.InvalidQueryTransition):
        await q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER)


@pytest.mark.asyncio
async def test_a_cancelled_query_is_terminal(db):
    query = await new_query(db)
    await q.cancel_query(db, query_id=query["id"], reviewer_id=REVIEWER, reason="Raised in error.")
    for attempt in (
        q.add_response(db, query_id=query["id"], author_id=SITE, body="…"),
        q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER),
        q.reopen_query(db, query_id=query["id"], reviewer_id=REVIEWER, reason="x"),
    ):
        with pytest.raises(q.QueryError):
            await attempt


@pytest.mark.asyncio
async def test_optimistic_locking_stops_two_reviewers_overwriting_each_other(db):
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Answer.")
    stale = await q.get_query(db, query["id"])

    await q.close_query(db, query_id=query["id"], reviewer_id=REVIEWER,
                        expected_version=stale["version"])

    # The second reviewer, still holding the version they loaded, is told
    # rather than silently reopening a decision that was already made.
    with pytest.raises(q.QueryError) as exc:
        await q.reject_response(db, query_id=query["id"], reviewer_id="other",
                                reason_code="X", expected_version=stale["version"])
    assert exc.value.code in ("STALE_VERSION", "INVALID_TRANSITION")


@pytest.mark.asyncio
async def test_concurrent_decisions_produce_exactly_one_winner(db):
    """The Phase 5 exit criterion: the state machine cannot be forced into an
    invalid transition under concurrency."""
    query = await new_query(db)
    await q.add_response(db, query_id=query["id"], author_id=SITE, body="Answer.")

    async def close():
        try:
            return (await q.close_query(db, query_id=query["id"], reviewer_id="a"))["status"]
        except q.QueryError:
            return None

    async def reject():
        try:
            return (await q.reject_response(db, query_id=query["id"], reviewer_id="b",
                                            reason_code="X"))["status"]
        except q.QueryError:
            return None

    results = [r for r in await asyncio.gather(close(), reject()) if r]
    assert len(results) == 1, f"expected one winner, got {results}"
    assert (await q.get_query(db, query["id"]))["status"] == results[0]


@pytest.mark.asyncio
async def test_referencing_queries_finds_live_references_only(db):
    entity = f"equipment-{uuid.uuid4().hex[:8]}"
    live = await new_query(db, linked_entity_type="equipment", linked_entity_id=entity)
    closed = await new_query(db, linked_entity_type="equipment", linked_entity_id=entity)
    await q.add_response(db, query_id=closed["id"], author_id=SITE, body="Fixed.")
    await q.close_query(db, query_id=closed["id"], reviewer_id=REVIEWER)

    refs = await q.referencing_queries(db, linked_entity_id=entity)
    ids = [r["id"] for r in refs]
    # A live reference blocks deleting the equipment; a resolved one does not.
    assert live["id"] in ids
    assert closed["id"] not in ids


# ═══════════════════════════════════════════════════════════════════════════
# CAPAs
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_capa_requires_an_owner_and_a_due_date(db):
    with pytest.raises(q.QueryError) as no_owner:
        await new_capa(db, owner_user_id="")
    assert no_owner.value.code == "OWNER_REQUIRED"

    with pytest.raises(q.QueryError) as bad_date:
        await new_capa(db, due_date="next Tuesday")
    assert bad_date.value.code == "INVALID_DUE_DATE"


@pytest.mark.asyncio
async def test_an_ambiguous_due_date_is_refused_not_guessed(db):
    with pytest.raises(q.QueryError):
        await new_capa(db, due_date="03/04/2026")


@pytest.mark.asyncio
async def test_completed_is_not_closed(db):
    """The owner saying "done" and someone else confirming it worked are two
    different claims by two different parties. Collapsing them would remove
    the effectiveness review entirely."""
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    completed = await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE,
                                      note="Retraining delivered on 3 March.")
    assert completed["status"] == q.CAPA_COMPLETED
    assert completed["status"] != q.CAPA_CLOSED
    assert completed["effectiveness_review"] is None


@pytest.mark.asyncio
async def test_completing_needs_a_note_describing_what_was_done(db):
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    with pytest.raises(q.QueryError) as exc:
        await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE, note="")
    assert exc.value.code == "COMPLETION_NOTE_REQUIRED"


@pytest.mark.asyncio
async def test_closure_requires_an_effective_review(db):
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE, note="Done.")

    closed = await q.record_effectiveness_review(
        db, capa_id=capa["id"], reviewer_id=REVIEWER, outcome=q.EFFECTIVENESS_EFFECTIVE,
        note="Verified against the March logs.")
    assert closed["status"] == q.CAPA_CLOSED
    assert closed["effectiveness_review"]["outcome"] == q.EFFECTIVENESS_EFFECTIVE
    assert closed["effectiveness_review"]["reviewed_by"] == REVIEWER


@pytest.mark.asyncio
async def test_an_ineffective_review_reopens_rather_than_closing(db):
    """The finding does not disappear because the first attempt failed — it
    goes back to the owner, and the record shows it took more than one try."""
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE, note="Done.")

    reopened = await q.record_effectiveness_review(
        db, capa_id=capa["id"], reviewer_id=REVIEWER, outcome=q.EFFECTIVENESS_INEFFECTIVE,
        note="Gaps reappeared the following week.")
    assert reopened["status"] == q.CAPA_REOPENED
    assert reopened["reopen_count"] == 1

    # And the work can genuinely resume.
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    assert (await q.get_capa(db, capa["id"]))["status"] == q.CAPA_IN_PROGRESS


@pytest.mark.asyncio
async def test_the_person_who_did_the_work_cannot_certify_it_worked(db):
    """Separation of duties. Self-certification is exactly the failure mode a
    CAPA effectiveness review exists to prevent."""
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE, note="Done.")

    with pytest.raises(q.QueryError) as exc:
        await q.record_effectiveness_review(
            db, capa_id=capa["id"], reviewer_id=SITE, outcome=q.EFFECTIVENESS_EFFECTIVE,
            note="Looks fine to me.")
    assert exc.value.code == "SELF_REVIEW_FORBIDDEN"
    assert (await q.get_capa(db, capa["id"]))["status"] == q.CAPA_COMPLETED


@pytest.mark.asyncio
async def test_an_effectiveness_review_cannot_skip_completion(db):
    capa = await new_capa(db)
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    with pytest.raises(q.InvalidQueryTransition):
        await q.record_effectiveness_review(
            db, capa_id=capa["id"], reviewer_id=REVIEWER, outcome=q.EFFECTIVENESS_EFFECTIVE,
            note="Skipping ahead.")


def test_capa_terminal_states_are_terminal():
    for terminal in (q.CAPA_CLOSED, q.CAPA_CANCELLED):
        assert q.CAPA_TRANSITIONS[terminal] == set()


# ── Overdue is derived, never stored ────────────────────────────────────────

def test_is_overdue_is_computed_from_the_due_date():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert q.is_overdue({"status": q.CAPA_IN_PROGRESS, "due_date": yesterday}, as_of=TODAY) is True
    assert q.is_overdue({"status": q.CAPA_IN_PROGRESS, "due_date": tomorrow}, as_of=TODAY) is False
    # Due today is not yet overdue — a CAPA has the whole of its due date.
    assert q.is_overdue({"status": q.CAPA_IN_PROGRESS, "due_date": TODAY.isoformat()}, as_of=TODAY) is False


def test_a_resolved_capa_is_never_overdue_however_old():
    """A CAPA closed last year is not "overdue" — it's done. Showing it as
    overdue would keep a resolved finding permanently on someone's list."""
    ancient = "2019-01-01"
    for status in (q.CAPA_CLOSED, q.CAPA_CANCELLED):
        assert q.is_overdue({"status": status, "due_date": ancient}, as_of=TODAY) is False
    assert q.is_overdue({"status": q.CAPA_IN_PROGRESS, "due_date": ancient}, as_of=TODAY) is True


def test_a_malformed_due_date_is_not_reported_as_overdue():
    assert q.is_overdue({"status": q.CAPA_OPEN, "due_date": "not a date"}, as_of=TODAY) is False
    assert q.is_overdue({"status": q.CAPA_OPEN, "due_date": None}, as_of=TODAY) is False


@pytest.mark.asyncio
async def test_the_overdue_sweep_returns_only_outstanding_past_due_capas(db):
    audit = f"{AUDIT}-overdue"
    past = (TODAY - timedelta(days=5)).isoformat()
    future = (TODAY + timedelta(days=5)).isoformat()

    late = await new_capa(db, audit_id=audit, due_date=past)
    on_time = await new_capa(db, audit_id=audit, due_date=future)
    resolved = await new_capa(db, audit_id=audit, due_date=past)
    await q.cancel_capa(db, capa_id=resolved["id"], actor_id=REVIEWER, reason="Withdrawn.")

    overdue = await q.overdue_capas(db, audit_id=audit, as_of=TODAY)
    ids = [c["id"] for c in overdue]
    assert late["id"] in ids
    assert on_time["id"] not in ids
    assert resolved["id"] not in ids


# ── Decision blocking ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_critical_capas_drive_decision_blocking(db):
    audit = f"{AUDIT}-critical"
    critical = await new_capa(db, audit_id=audit, criticality="critical")
    await new_capa(db, audit_id=audit, criticality="minor")

    blocking = await q.open_critical_capas(db, audit)
    assert [c["id"] for c in blocking] == [critical["id"]]

    # Resolving it unblocks — and so does withdrawing it, which is why the
    # check uses CAPA_OUTSTANDING_STATES rather than `status != CLOSED`.
    await q.cancel_capa(db, capa_id=critical["id"], actor_id=REVIEWER, reason="Duplicate.")
    assert await q.open_critical_capas(db, audit) == []


@pytest.mark.asyncio
async def test_a_completed_critical_capa_still_blocks_until_reviewed(db):
    """"The site says they fixed it" is not "we confirmed they fixed it".
    Treating COMPLETED as resolved would let an audit be signed off on an
    unverified claim."""
    audit = f"{AUDIT}-completed-critical"
    capa = await new_capa(db, audit_id=audit, criticality="critical")
    await q.start_capa(db, capa_id=capa["id"], actor_id=SITE)
    await q.complete_capa(db, capa_id=capa["id"], actor_id=SITE, note="Fixed.")

    assert [c["id"] for c in await q.open_critical_capas(db, audit)] == [capa["id"]]

    await q.record_effectiveness_review(
        db, capa_id=capa["id"], reviewer_id=REVIEWER, outcome=q.EFFECTIVENESS_EFFECTIVE,
        note="Confirmed.")
    assert await q.open_critical_capas(db, audit) == []
