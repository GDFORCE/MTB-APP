"""Document lifecycle — spec §10.1, Production-Grade Implementation Plan v1.3
Phase 4.

The state machine is the security control here, so these tests are written to
FAIL against the plausible shortcut implementations, not merely to pass
against the correct one: a machine that allows any transition, a visibility
rule that shows blocked files, an expiry sweep that acts on AI suggestions.
"""
from __future__ import annotations

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

from app.services.remote_audit import documents as d  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN = uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await d.ensure_indexes(database)
    yield database
    await database[d.DOCUMENTS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})


async def _new_doc(db, *, audit_id=None, document_type="calibration_certificate", **kwargs):
    return await d.create_document(
        db, audit_id=audit_id or f"audit-{RUN}-{uuid.uuid4().hex[:6]}",
        uploaded_by=f"user-{RUN}", document_type=document_type,
        file_name=kwargs.pop("file_name", "cert.pdf"),
        mime_claimed=kwargs.pop("mime_claimed", "application/pdf"),
        size_claimed=kwargs.pop("size_claimed", 4096), **kwargs)


# ── Creation validates claims only ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_document_starts_quarantined_and_invisible(db):
    doc = await _new_doc(db)
    assert doc["status"] == d.STATUS_QUARANTINED
    # The single most important property in this module: an uploaded file is
    # not evidence and is not shown to anyone until it has been scanned.
    assert d.is_visible_to_reviewer(doc["status"]) is False
    assert doc["sha256_server"] is None
    assert doc["extracted"] is None and doc["confirmed"] is None


@pytest.mark.asyncio
async def test_object_key_is_server_generated_and_ignores_the_client_filename(db):
    doc = await _new_doc(db, file_name="../../../etc/passwd.pdf")
    assert doc["id"] in doc["object_key"]
    assert "passwd" not in doc["object_key"]
    assert ".." not in doc["object_key"]
    # The display name is kept for the human, with separators stripped.
    assert "/" not in doc["display_name"] and "\\" not in doc["display_name"]


@pytest.mark.asyncio
async def test_unsupported_media_type_is_refused(db):
    with pytest.raises(d.DocumentError) as exc:
        await _new_doc(db, mime_claimed="application/x-msdownload")
    assert exc.value.code == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.asyncio
async def test_oversized_claim_is_refused(db):
    with pytest.raises(d.DocumentError) as exc:
        await _new_doc(db, size_claimed=d.DEFAULT_MAX_DOCUMENT_BYTES + 1)
    assert exc.value.code == "FILE_TOO_LARGE"


# ── Visibility ──────────────────────────────────────────────────────────────

def test_processing_blocked_is_never_reviewer_visible():
    """v1.3 correction #15, stricter than REVIEW on purpose: when the malware
    scanner is unreachable the file stays hidden rather than being shown with
    a caveat. A reviewer is never the scanner of last resort."""
    assert d.is_visible_to_reviewer(d.STATUS_PROCESSING_BLOCKED) is False
    assert d.is_visible_to_reviewer(d.STATUS_QUARANTINED) is False
    assert d.is_visible_to_reviewer(d.STATUS_PROCESSING) is False
    assert d.is_visible_to_reviewer(d.STATUS_PROCESSING_FAILED) is False


def test_post_check_states_are_visible():
    for status in (d.STATUS_TECHNICALLY_VALID, d.STATUS_REVIEW_REQUIRED, d.STATUS_ACCEPTED,
                   d.STATUS_REJECTED, d.STATUS_SUPERSEDED, d.STATUS_EXPIRED):
        assert d.is_visible_to_reviewer(status) is True


# ── The transition table ────────────────────────────────────────────────────

def test_accepted_is_unreachable_without_completed_checks():
    """The shortcut this guards against is a handler that accepts from
    whatever state it happens to find. ACCEPTED must be reachable ONLY from a
    state whose checks actually ran."""
    for origin in (d.STATUS_QUARANTINED, d.STATUS_PROCESSING, d.STATUS_PROCESSING_BLOCKED,
                   d.STATUS_PROCESSING_FAILED):
        assert d.can_transition(origin, d.STATUS_ACCEPTED) is False
    assert d.can_transition(d.STATUS_TECHNICALLY_VALID, d.STATUS_ACCEPTED) is True
    assert d.can_transition(d.STATUS_REVIEW_REQUIRED, d.STATUS_ACCEPTED) is True


def test_terminal_states_are_terminal():
    for terminal in (d.STATUS_REJECTED, d.STATUS_SUPERSEDED, d.STATUS_EXPIRED):
        for target in d.ALL_STATUSES:
            assert d.can_transition(terminal, target) is False, f"{terminal} -> {target}"


def test_every_transition_target_is_a_real_status():
    """Guards a typo'd status string silently creating an unreachable state."""
    for origin, targets in d.ALLOWED_TRANSITIONS.items():
        assert origin in d.ALL_STATUSES
        assert targets <= d.ALL_STATUSES, f"{origin} names a status that does not exist"


@pytest.mark.asyncio
async def test_illegal_transition_raises_and_changes_nothing(db):
    doc = await _new_doc(db)
    with pytest.raises(d.InvalidTransition):
        await d.transition(db, doc["id"], d.STATUS_ACCEPTED)
    after = await d.get_document(db, doc["id"])
    assert after["status"] == d.STATUS_QUARANTINED


@pytest.mark.asyncio
async def test_expected_status_guard_rejects_a_stale_caller(db):
    doc = await _new_doc(db)
    await d.transition(db, doc["id"], d.STATUS_PROCESSING, expected_status=d.STATUS_QUARANTINED)
    # A second caller still believing the document is QUARANTINED must lose,
    # not silently re-run the transition.
    with pytest.raises(d.InvalidTransition):
        await d.transition(db, doc["id"], d.STATUS_PROCESSING, expected_status=d.STATUS_QUARANTINED)


@pytest.mark.asyncio
async def test_concurrent_transitions_produce_exactly_one_winner(db):
    import asyncio

    doc = await _new_doc(db)
    await d.transition(db, doc["id"], d.STATUS_PROCESSING, expected_status=d.STATUS_QUARANTINED)

    async def move(target):
        try:
            await d.transition(db, doc["id"], target, expected_status=d.STATUS_PROCESSING)
            return target
        except d.InvalidTransition:
            return None

    results = await asyncio.gather(
        move(d.STATUS_TECHNICALLY_VALID), move(d.STATUS_REVIEW_REQUIRED))
    winners = [r for r in results if r]
    assert len(winners) == 1, f"expected exactly one winner, got {results}"
    assert (await d.get_document(db, doc["id"]))["status"] == winners[0]


# ── Append-only check runs ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_runs_are_append_only_and_chained(db):
    """v1.3 correction #7 applied to documents: a re-run after a rule-set
    change must add a row, never overwrite the one a reviewer already saw."""
    doc = await _new_doc(db)
    first = await d.record_check(db, document_id=doc["id"], outcome=d.STATUS_REVIEW_REQUIRED,
                                 checks={"malware_status": "CLEAN"}, findings=[{"code": "A"}],
                                 rule_set_version="v1")
    second = await d.record_check(db, document_id=doc["id"], outcome=d.STATUS_TECHNICALLY_VALID,
                                  checks={"malware_status": "CLEAN"}, findings=[],
                                  rule_set_version="v2")

    assert first["check_run_id"] == 1 and first["supersedes_check_id"] is None
    assert second["check_run_id"] == 2 and second["supersedes_check_id"] == first["id"]

    history = await d.list_checks(db, doc["id"])
    assert len(history) == 2
    # The superseded run is still fully retrievable, findings and all.
    assert history[0]["findings"] == [{"code": "A"}]
    assert (await d.latest_check(db, doc["id"]))["check_run_id"] == 2


# ── extract -> review -> commit ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extraction_never_lands_in_confirmed(db):
    doc = await _new_doc(db)
    await d.set_extraction(db, doc["id"], {"source": "vision:anthropic",
                                           "fields": {"due_date": "2027-01-01"},
                                           "confidence": "low"})
    after = await d.get_document(db, doc["id"])
    assert after["extracted"]["fields"]["due_date"] == "2027-01-01"
    # The model's guess is not a fact and must not become one by being stored.
    assert after["confirmed"] is None
    assert after["expires_on"] is None


@pytest.mark.asyncio
async def test_confirming_sets_expiry_from_the_confirmed_value(db):
    doc = await _new_doc(db)
    confirmed = await d.confirm_fields(
        db, doc["id"], fields={"due_date": "2027-03-01", "certificate_number": "CAL-9"},
        confirmed_by="reviewer-1")
    assert confirmed["confirmed"]["due_date"] == "2027-03-01"
    assert confirmed["expires_on"] == "2027-03-01"
    assert confirmed["confirmed_by"] == "reviewer-1"


@pytest.mark.asyncio
async def test_unconfirmable_field_is_refused(db):
    """A caller must not be able to smuggle `status` (or anything else) into
    the confirmed sub-document."""
    doc = await _new_doc(db)
    with pytest.raises(d.DocumentError) as exc:
        await d.confirm_fields(db, doc["id"], fields={"status": "ACCEPTED"}, confirmed_by="u")
    assert exc.value.code == "UNKNOWN_FIELD"


@pytest.mark.asyncio
async def test_ambiguous_date_is_refused_rather_than_guessed(db):
    """`03/04/2026` is April 3rd or March 4th depending on where you are.
    Recording a guess would silently move a calibration expiry by a month."""
    doc = await _new_doc(db)
    with pytest.raises(d.DocumentError) as exc:
        await d.confirm_fields(db, doc["id"], fields={"due_date": "03/04/2026"}, confirmed_by="u")
    assert exc.value.code == "AMBIGUOUS_DATE"


# ── Expiry & supersession ───────────────────────────────────────────────────

async def _accepted_doc(db, *, audit_id, expires_on=None):
    doc = await _new_doc(db, audit_id=audit_id)
    await d.transition(db, doc["id"], d.STATUS_PROCESSING, expected_status=d.STATUS_QUARANTINED)
    await d.transition(db, doc["id"], d.STATUS_TECHNICALLY_VALID, expected_status=d.STATUS_PROCESSING)
    if expires_on:
        await d.confirm_fields(db, doc["id"], fields={"due_date": expires_on}, confirmed_by="rev")
    await d.transition(db, doc["id"], d.STATUS_ACCEPTED, expected_status=d.STATUS_TECHNICALLY_VALID)
    return await d.get_document(db, doc["id"])


@pytest.mark.asyncio
async def test_expiry_sweep_expires_only_past_due_confirmed_documents(db):
    audit_id = f"audit-{RUN}-expiry"
    # Pinned to an explicit reference day rather than `date.today()`: the
    # sweep works in UTC, and a server running east of UTC before 05:30 local
    # would otherwise disagree with the test about which day "yesterday" is.
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    overdue = await _accepted_doc(db, audit_id=audit_id, expires_on=yesterday)
    current = await _accepted_doc(db, audit_id=audit_id, expires_on=tomorrow)

    expired = await d.expire_due_documents(db, audit_id=audit_id, as_of=today)

    assert overdue["id"] in expired
    assert current["id"] not in expired
    assert (await d.get_document(db, overdue["id"]))["status"] == d.STATUS_EXPIRED
    assert (await d.get_document(db, current["id"]))["status"] == d.STATUS_ACCEPTED


@pytest.mark.asyncio
async def test_expiry_sweep_ignores_an_unconfirmed_ai_due_date(db):
    """The failure this prevents: a model misreads a date, and the sweep
    silently invalidates a site's compliance record on the strength of a
    suggestion nobody ever confirmed."""
    audit_id = f"audit-{RUN}-aiexpiry"
    today = datetime.now(timezone.utc).date()
    doc = await _accepted_doc(db, audit_id=audit_id)  # accepted with NO confirmed due date
    await d.set_extraction(db, doc["id"], {
        "source": "vision:anthropic",
        "fields": {"due_date": (today - timedelta(days=400)).isoformat()},
        "confidence": "low"})

    expired = await d.expire_due_documents(db, audit_id=audit_id, as_of=today)

    assert doc["id"] not in expired
    assert (await d.get_document(db, doc["id"]))["status"] == d.STATUS_ACCEPTED


@pytest.mark.asyncio
async def test_supersession_links_both_ways_and_never_deletes(db):
    audit_id = f"audit-{RUN}-supersede"
    old = await _accepted_doc(db, audit_id=audit_id, expires_on="2027-01-01")
    new = await _new_doc(db, audit_id=audit_id)

    await d.supersede(db, old_document_id=old["id"], new_document_id=new["id"])

    old_after = await d.get_document(db, old["id"])
    new_after = await d.get_document(db, new["id"])
    assert old_after["status"] == d.STATUS_SUPERSEDED
    assert old_after["superseded_by_document_id"] == new["id"]
    assert new_after["supersedes_document_id"] == old["id"]
    # "What did the site have on file in March?" must stay answerable.
    assert old_after["confirmed"]["due_date"] == "2027-01-01"


@pytest.mark.asyncio
async def test_cannot_supersede_a_document_that_was_never_accepted(db):
    doc = await _new_doc(db)
    with pytest.raises(d.InvalidTransition):
        await d.supersede(db, old_document_id=doc["id"], new_document_id="whatever")


@pytest.mark.asyncio
async def test_a_document_is_valid_through_the_whole_of_its_due_date(db):
    """The boundary, pinned. A certificate due today is still valid today —
    expiring it a day early would flag a compliant site as non-compliant on a
    paperwork technicality."""
    audit_id = f"audit-{RUN}-boundary"
    today = datetime.now(timezone.utc).date()
    due_today = await _accepted_doc(db, audit_id=audit_id, expires_on=today.isoformat())

    assert await d.expire_due_documents(db, audit_id=audit_id, as_of=today) == []
    assert (await d.get_document(db, due_today["id"]))["status"] == d.STATUS_ACCEPTED

    # ...and expires the following day.
    expired = await d.expire_due_documents(db, audit_id=audit_id, as_of=today + timedelta(days=1))
    assert due_today["id"] in expired
