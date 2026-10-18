"""P2-01/P2-03/P2-05: the identity store and service against memory AND real MongoDB.

Storage-level claims, indexes, rollback and concurrency are proven only by the
real replica-set variants (``UCTSM_REQUIRE_MONGO=1`` makes them fail, not skip,
when MongoDB is unavailable).
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.errors import RecordNotFoundError, StaleRevisionError
from app.application.protocol_identity import (
    ExtractionClaimLostError, IdentityStateError, JobState, PagesInput, ProtocolIdentityService, ReviewAction,
    ReviewStatus,
)
from app.domain.protocol_identity import ClaimSource, claim_from_text
from app.persistence.memory.identity_store import InMemoryIdentityStore
from app.persistence.mongodb.identity_repository import MongoIdentityStore

pytest_plugins = ["tests.contract.conftest"]

COLLECTIONS = ["protocol_document_identities", "identity_reviews", "identity_jobs",
               "identity_extraction_results"]


def doc(nct: str, proto: str, *, version="2.0", date="12 March 2015", extra="") -> list[PagesInput]:
    texts = [
        f"PROTOCOL\nTitle: A study\nProtocol Number: {proto}\n"
        f"ClinicalTrials.gov Identifier: {nct}\nVersion {version}\nDate: {date}\n"
        + "The study follows the described assessments. " * 6 + extra,
        "Schedule of assessments and visit windows are described here. " * 6]
    return [PagesInput(number=i + 1, text=t, usable=True) for i, t in enumerate(texts)]


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    if request.param == "memory":
        return InMemoryIdentityStore()
    client = request.getfixturevalue("mongo_client")
    database = request.getfixturevalue("_mongo_database")
    for name in COLLECTIONS:
        database[name].delete_many({})
    return MongoIdentityStore(client, database)


@pytest.fixture
def mongo_store(mongo_client, _mongo_database):
    for name in COLLECTIONS:
        _mongo_database[name].delete_many({})
    return MongoIdentityStore(mongo_client, _mongo_database), _mongo_database


def assess(service, org, pages, *, expected=None, user="u1", filename="p.pdf", sha=None):
    return service.assess(
        organization_id=org, owner_user_id=user, filename=filename, content_type="application/pdf",
        size_bytes=1, binary_sha256=sha or uuid4().hex.ljust(64, "0"), pages=pages,
        expected=[claim_from_text(v, ClaimSource.SUBMITTED) for v in (expected or [])])


def ids():
    n = uuid4().int % 90_000_000 + 10_000_000
    return f"NCT{n:08d}", f"PRT-{uuid4().hex[:6].upper()}"


# --------------------------------------------------------------- claims & reuse
def test_one_claim_per_document_and_engine_then_reuse(store):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    identity = assess(service, org, doc(nct, proto))
    first = service.begin_extraction(identity, "sig-A")
    assert first.kind == "RUN" and first.job.attempt == 1
    assert service.begin_extraction(identity, "sig-A").kind == "IN_PROGRESS"

    service.complete_extraction(first, signature="sig-A", payload={"options": [1]})
    again = service.begin_extraction(identity, "sig-A")
    assert again.kind == "REUSE" and again.result.payload == {"options": [1]}

    # an incompatible result (another engine signature) is never returned as success
    other = service.begin_extraction(identity, "sig-B")
    assert other.kind == "RUN" and other.job.claim_key != first.job.claim_key


def test_same_text_different_bytes_shares_the_claim_and_the_result(store):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    original = assess(service, org, doc(nct, proto))
    plan = service.begin_extraction(original, "sig")
    service.complete_extraction(plan, signature="sig", payload={"x": 1})

    copy = assess(service, org, [PagesInput(number=p.number, text=p.text.replace(" ", "  "),
                                            usable=True) for p in doc(nct, proto)],
                  user="u2")
    assert copy.effective_classification.value == "DUPLICATE"
    assert copy.duplicate_of_id == original.id
    assert service.begin_extraction(copy, "sig").kind == "REUSE"


def test_failed_job_is_retried_once_and_an_expired_lease_is_taken_over(store):
    now = [datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)]
    service = ProtocolIdentityService(store, clock=lambda: now[0])
    org = uuid4()
    nct, proto = ids()
    identity = assess(service, org, doc(nct, proto))
    plan = service.begin_extraction(identity, "sig")
    service.fail_extraction(plan, "provider timeout")
    retry = service.begin_extraction(identity, "sig")
    assert retry.kind == "RUN" and retry.job.attempt == 2 and retry.job.state is JobState.CLAIMED
    assert service.begin_extraction(identity, "sig").kind == "IN_PROGRESS"
    now[0] += timedelta(hours=2)                      # the claimant died
    takeover = service.begin_extraction(identity, "sig")
    assert takeover.kind == "RUN" and takeover.job.attempt == 3


def test_reuse_never_crosses_organizations(store):
    service = ProtocolIdentityService(store)
    org_a, org_b = uuid4(), uuid4()
    nct, proto = ids()
    a = assess(service, org_a, doc(nct, proto))
    plan = service.begin_extraction(a, "sig")
    service.complete_extraction(plan, signature="sig", payload={"x": 1})
    b = assess(service, org_b, doc(nct, proto))
    assert b.effective_classification.value == "MATCH" and b.duplicate_of_id is None
    assert service.begin_extraction(b, "sig").kind == "RUN"        # no peek at A's result
    with pytest.raises(RecordNotFoundError):
        service.get(org_b, a.id)


def test_quarantined_upload_never_seeds_a_duplicate_and_blocked_never_starts(store):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])
    assert wrong.blocked and wrong.accepted_identifiers == []
    assert service.begin_extraction(wrong, "sig").kind == "BLOCKED"
    assert assess(service, org, doc(nct, proto)).effective_classification.value == "MATCH"


# ------------------------------------------------------------------ review rules
def test_review_state_machine_and_one_resume_intent(store):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])
    with pytest.raises(StaleRevisionError):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r1",
                        action=ReviewAction.KEEP_QUARANTINED, reason="x", expected_revision=99)
    with pytest.raises(ValueError):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r1",
                        action=ReviewAction.REJECT, reason=" ", expected_revision=1)
    done = service.resolve(
        organization_id=org, identity_id=wrong.id, actor_id="r1",
        action=ReviewAction.CORRECT_EXPECTED_IDENTITY, reason="typo", expected_revision=1,
        corrected_identifier=nct)
    assert done.identity.review_status is ReviewStatus.APPROVED and done.resume_intent_id
    assert done.review.classification_before.value == "MISMATCH"
    assert done.review.classification_after.value == "MATCH"

    # a conflicting second decision is refused; the identical repeat is a replay
    with pytest.raises(IdentityStateError):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r2",
                        action=ReviewAction.REJECT, reason="no", expected_revision=2)
    replay = service.resolve(
        organization_id=org, identity_id=wrong.id, actor_id="r1",
        action=ReviewAction.CORRECT_EXPECTED_IDENTITY, reason="typo", expected_revision=1,
        corrected_identifier=nct)
    assert replay.replayed and replay.resume_intent_id == done.resume_intent_id

    assert service.claim_resume(org, wrong.id) is not None        # taken once...
    assert service.claim_resume(org, wrong.id) is None            # ...never twice


def test_a_correction_that_contradicts_the_document_is_refused(store):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])
    with pytest.raises(ValueError, match="not an identifier the document states"):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r",
                        action=ReviewAction.CORRECT_EXPECTED_IDENTITY, reason="try",
                        expected_revision=1, corrected_identifier="NCT00000002")
    with pytest.raises(ValueError, match="contradicts"):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r",
                        action=ReviewAction.CONFIRM_DOCUMENT_IDENTITY, reason="try",
                        expected_revision=1, corrected_identifier="NCT00000002")
    again = service.get(org, wrong.id)
    assert again.review_status is ReviewStatus.PENDING_REVIEW and again.revision == 1


def test_a_failure_inside_the_decision_transaction_leaves_nothing_behind(store, monkeypatch):
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])
    tx_type = type(store.read(lambda tx: tx))

    def boom(self, record):
        raise RuntimeError("storage failed after the decision was written")

    monkeypatch.setattr(tx_type, "add_job", boom)
    with pytest.raises(RuntimeError):
        service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r",
                        action=ReviewAction.CORRECT_EXPECTED_IDENTITY, reason="typo",
                        expected_revision=1, corrected_identifier=nct)
    monkeypatch.undo()
    after, reviews = service.detail(org, wrong.id)
    assert after.review_status is ReviewStatus.PENDING_REVIEW and after.revision == 1
    assert reviews == []                                   # no approval without its intent
    assert service.claim_resume(org, wrong.id) is None


# ------------------------------------------------- real MongoDB only: concurrency
def race(count, work):
    gate = threading.Barrier(count)
    results, errors = [], []

    def run(index):
        gate.wait()
        try:
            results.append(work(index))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    with ThreadPoolExecutor(count) as pool:
        list(pool.map(run, range(count)))
    return results, errors


def test_concurrent_uploads_of_one_document_run_exactly_one_extraction(mongo_store):
    store, database = mongo_store
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    identities = [assess(service, org, doc(nct, proto), user=f"u{i}") for i in range(8)]
    results, errors = race(8, lambda i: service.begin_extraction(identities[i], "sig"))
    assert errors == []
    kinds = sorted(item.kind for item in results)
    assert kinds.count("RUN") == 1 and kinds.count("IN_PROGRESS") == 7
    assert database["identity_jobs"].count_documents({"organization_id": org}) == 1
    # eight uploads keep eight provenance records: the content hash is not unique
    assert database["protocol_document_identities"].count_documents({"organization_id": org}) == 8


def test_concurrent_review_resumes_claim_the_intent_once(mongo_store):
    store, database = mongo_store
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])
    service.resolve(organization_id=org, identity_id=wrong.id, actor_id="r",
                    action=ReviewAction.CORRECT_EXPECTED_IDENTITY, reason="typo",
                    expected_revision=1, corrected_identifier=nct)
    results, errors = race(8, lambda _i: service.claim_resume(org, wrong.id))
    assert errors == []
    assert sum(1 for item in results if item is not None) == 1
    assert database["identity_jobs"].count_documents(
        {"organization_id": org, "kind": "RESUME"}) == 1


def test_concurrent_conflicting_decisions_let_exactly_one_win(mongo_store):
    store, database = mongo_store
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    wrong = assess(service, org, doc(nct, proto), expected=["NCT00000001"])

    def decide(index):
        action = ReviewAction.REJECT if index % 2 else ReviewAction.CORRECT_EXPECTED_IDENTITY
        return service.resolve(
            organization_id=org, identity_id=wrong.id, actor_id=f"r{index}", action=action,
            reason="concurrent", expected_revision=1,
            corrected_identifier=None if index % 2 else nct)

    results, errors = race(8, decide)
    winners = [item for item in results if not item.replayed]
    assert len(winners) == 1                      # exactly one decision was applied
    assert len(results) + len(errors) == 8        # every other request was refused or replayed
    final = service.get(org, wrong.id)
    reviews = database["identity_reviews"].count_documents({"identity_id": wrong.id})
    assert reviews == 1 and final.revision == 2
    assert all(isinstance(error, (IdentityStateError, StaleRevisionError)) for error in errors)
    intents = database["identity_jobs"].count_documents({"kind": "RESUME", "identity_id": wrong.id})
    assert intents == (1 if final.review_status is ReviewStatus.APPROVED else 0)


def test_indexes_enforce_the_claim_and_allow_repeated_uploads(mongo_store):
    store, database = mongo_store
    service, org = ProtocolIdentityService(store), uuid4()
    nct, proto = ids()
    sha = "ab" * 32
    one = assess(service, org, doc(nct, proto), sha=sha)
    two = assess(service, org, doc(nct, proto), sha=sha, user="u2")   # same hash: allowed
    assert one.id != two.id
    plan = service.begin_extraction(one, "sig")
    from pymongo.errors import DuplicateKeyError
    job = database["identity_jobs"].find_one({"organization_id": org})
    with pytest.raises(DuplicateKeyError):
        database["identity_jobs"].insert_one({**job, "_id": uuid4()})
    names = {index["name"] for index in database["identity_jobs"].list_indexes()}
    assert "uq_identity_job_claim" in names
    names = {index["name"] for index in database["protocol_document_identities"].list_indexes()}
    assert {"ix_identity_accepted", "ix_identity_binary", "ix_identity_text",
            "ix_identity_review_queue"} <= names
    uniques = [i["name"] for i in database["protocol_document_identities"].list_indexes()
               if i.get("unique") and i["name"] != "_id_"]
    assert uniques == []                                  # nothing unique on hashes
    assert plan.kind == "RUN"


# ============================================================ R1: stale workers are fenced

def _takeover(store):
    now = [datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)]
    service = ProtocolIdentityService(store, clock=lambda: now[0])
    org = uuid4()
    nct, proto = ids()
    identity = assess(service, org, doc(nct, proto))
    worker_a = service.begin_extraction(identity, "sig")
    now[0] += timedelta(hours=2)                       # A's lease expires
    worker_b = service.begin_extraction(identity, "sig")
    assert worker_a.job.attempt == 1 and worker_b.kind == "RUN" and worker_b.job.attempt == 2
    return service, org, identity, worker_a, worker_b, now


def test_a_late_failure_from_the_obsolete_worker_does_not_fail_the_new_claim(store):
    service, org, identity, a, b, _ = _takeover(store)
    service.fail_extraction(a, "late timeout from the first worker")
    assert service.begin_extraction(identity, "sig").kind == "IN_PROGRESS"     # B still owns it
    done = service.complete_extraction(b, signature="sig", payload={"from": "B"})
    assert service.begin_extraction(identity, "sig").result.id == done.id


def test_a_late_completion_from_the_obsolete_worker_publishes_nothing(store):
    service, org, identity, a, b, _ = _takeover(store)
    with pytest.raises(ExtractionClaimLostError):
        service.complete_extraction(a, signature="sig", payload={"from": "STALE_A"})
    assert service.begin_extraction(identity, "sig").kind == "IN_PROGRESS"      # nothing reusable yet
    service.complete_extraction(b, signature="sig", payload={"from": "B"})
    assert service.begin_extraction(identity, "sig").result.payload == {"from": "B"}
    with pytest.raises(ExtractionClaimLostError):                                # still refused after B won
        service.complete_extraction(a, signature="sig", payload={"from": "STALE_A"})
    assert service.begin_extraction(identity, "sig").result.payload == {"from": "B"}


def test_lease_renewal_after_takeover_is_refused_and_the_winner_can_renew(store):
    service, org, identity, a, b, _ = _takeover(store)
    with pytest.raises(ExtractionClaimLostError):
        service.renew_extraction(a)
    service.renew_extraction(b)


def test_the_winning_claim_may_retry_a_lost_completion_response(store):
    service, org, identity, a, b, _ = _takeover(store)
    first = service.complete_extraction(b, signature="sig", payload={"x": 1})
    again = service.complete_extraction(b, signature="sig", payload={"x": 1})
    assert again.id == first.id


def test_takeover_and_completion_racing_on_real_mongo_leave_exactly_one_result(mongo_store):
    store, database = mongo_store
    service, org, identity, a, b, _ = _takeover(store)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def stale():
        barrier.wait()
        try:
            service.complete_extraction(a, signature="sig", payload={"from": "A"})
            outcomes.append("A-published")
        except ExtractionClaimLostError:
            outcomes.append("A-refused")

    def winner():
        barrier.wait()
        service.complete_extraction(b, signature="sig", payload={"from": "B"})
        outcomes.append("B-published")

    threads = [threading.Thread(target=stale), threading.Thread(target=winner)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["A-refused", "B-published"]
    assert database["identity_extraction_results"].count_documents({}) == 1
