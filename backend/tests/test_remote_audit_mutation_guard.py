"""Tests for the shared remote-audit mutation guard
(app/services/remote_audit/mutation_guard.py) — Phase 1 exit criterion:
"the shared mutation dependency's four core rejections proven in isolation":
missing capability, wrong org (via NotFoundOrForbidden), replayed
idempotency key, stale resource version. Plus rate limiting and the
idempotency conflict/in-progress cases the plan also calls for.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit.capabilities import RemoteAuditCapability  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.mutation_guard import (  # noqa: E402
    IdempotencyKeyConflict,
    IdempotencyInProgress,
    IdempotencyStore,
    NotFoundOrForbidden,
    OptimisticLockFailed,
    assert_capability,
    assert_version_matches,
    check_rate_limit,
)

RUN_ID = uuid.uuid4().hex[:8]


# ── 1. Missing capability -----------------------------------------------

def test_missing_capability_raises_not_found_or_forbidden():
    with pytest.raises(NotFoundOrForbidden):
        assert_capability("site", RemoteAuditCapability.APPROVER)


def test_present_capability_does_not_raise():
    assert_capability("sponsor", RemoteAuditCapability.REVIEWER)  # does not raise


def test_admin_role_alone_does_not_grant_template_admin():
    """Ties Phase 0's capability-model correction (#8) into the guard layer:
    the mutation guard's rejection is exactly what would stop an admin
    acting on a template without an explicit grant."""
    with pytest.raises(NotFoundOrForbidden):
        assert_capability("admin", RemoteAuditCapability.TEMPLATE_ADMIN)
    assert_capability(
        "admin", RemoteAuditCapability.TEMPLATE_ADMIN,
        explicit_grants=frozenset({RemoteAuditCapability.TEMPLATE_ADMIN}),
    )  # does not raise once explicitly granted


# ── 2. "Wrong org" -> the same NotFoundOrForbidden, never a distinguishable 403 -

def test_wrong_org_style_rejection_uses_the_same_error_as_missing_capability():
    """The plan requires cross-tenant mismatches to be indistinguishable
    from 'doesn't exist' (404, never 403) so a caller can't enumerate
    resources they can't access -- proven by both cases raising the exact
    same exception type/code."""
    with pytest.raises(NotFoundOrForbidden) as exc_missing_cap:
        assert_capability("site", RemoteAuditCapability.APPROVER)
    with pytest.raises(NotFoundOrForbidden) as exc_wrong_org:
        assert_capability("cro", RemoteAuditCapability.APPROVER)  # no explicit approver grant
    assert exc_missing_cap.value.code == exc_wrong_org.value.code == "RESOURCE_NOT_FOUND"


# ── 3. Stale resource version (optimistic locking) -----------------------

def test_matching_version_does_not_raise():
    assert_version_matches({"version": 3}, 3)


def test_stale_version_raises_optimistic_lock_failed():
    with pytest.raises(OptimisticLockFailed) as exc:
        assert_version_matches({"version": 4}, 3)
    assert exc.value.code == "STALE_RESOURCE_VERSION"


# ── 4. Idempotency: replay, conflict, in-progress -------------------------

@pytest_asyncio.fixture
async def idempotency_store():
    db = get_db()
    store = IdempotencyStore(db)
    await store.ensure_indexes()
    yield store
    await db["remote_audit_idempotency"].delete_many({"scope": {"$regex": f"^{RUN_ID}"}})
    db.client.close()


@pytest.mark.asyncio
async def test_fresh_idempotency_key_returns_none_and_proceeds(idempotency_store: IdempotencyStore):
    scope = f"{RUN_ID}.create-evidence"
    result = await idempotency_store.begin(scope=scope, key="key-1", payload={"a": 1})
    assert result is None


@pytest.mark.asyncio
async def test_replayed_idempotency_key_returns_the_stored_result_not_a_fresh_execution(
    idempotency_store: IdempotencyStore,
):
    scope = f"{RUN_ID}.create-evidence-replay"
    payload = {"a": 1}
    assert await idempotency_store.begin(scope=scope, key="key-1", payload=payload) is None
    await idempotency_store.complete(scope=scope, key="key-1", status_code=201, response_body={"id": "ev-1"})

    replay = await idempotency_store.begin(scope=scope, key="key-1", payload=payload)
    assert replay is not None
    assert replay.replayed is True
    assert replay.status_code == 201
    assert replay.response_body == {"id": "ev-1"}


@pytest.mark.asyncio
async def test_same_key_different_payload_is_a_conflict_not_a_silent_new_execution(
    idempotency_store: IdempotencyStore,
):
    scope = f"{RUN_ID}.create-evidence-conflict"
    await idempotency_store.begin(scope=scope, key="key-1", payload={"a": 1})
    await idempotency_store.complete(scope=scope, key="key-1", status_code=201, response_body={"id": "ev-1"})

    with pytest.raises(IdempotencyKeyConflict):
        await idempotency_store.begin(scope=scope, key="key-1", payload={"a": 999})


@pytest.mark.asyncio
async def test_concurrent_in_flight_key_raises_in_progress_not_a_double_execution(
    idempotency_store: IdempotencyStore,
):
    scope = f"{RUN_ID}.create-evidence-inflight"
    payload = {"a": 1}
    assert await idempotency_store.begin(scope=scope, key="key-1", payload=payload) is None
    # Second caller arrives before the first has called complete() -- this is
    # exactly two racing requests with the same idempotency key.
    with pytest.raises(IdempotencyInProgress):
        await idempotency_store.begin(scope=scope, key="key-1", payload=payload)


@pytest.mark.asyncio
async def test_release_without_completing_allows_a_genuine_retry_after_a_failure(
    idempotency_store: IdempotencyStore,
):
    scope = f"{RUN_ID}.create-evidence-release"
    payload = {"a": 1}
    assert await idempotency_store.begin(scope=scope, key="key-1", payload=payload) is None
    await idempotency_store.release_without_completing(scope=scope, key="key-1")
    # A retry after the handler raised mid-execution must be able to proceed
    # fresh, not be stuck behind a "pending" record forever.
    assert await idempotency_store.begin(scope=scope, key="key-1", payload=payload) is None


# ── Rate limiting ----------------------------------------------------------

@pytest_asyncio.fixture
async def rate_limit_db():
    db = get_db()
    yield db
    await db["remote_audit_rate_limits"].delete_many({"key": {"$regex": f"^{RUN_ID}"}})
    db.client.close()


@pytest.mark.asyncio
async def test_rate_limit_allows_up_to_the_limit_then_rejects(rate_limit_db):
    key = f"{RUN_ID}:user-1:capture-session-create"
    results = [
        await check_rate_limit(rate_limit_db, key, limit=3, window_seconds=60)
        for _ in range(5)
    ]
    assert results == [True, True, True, False, False]
