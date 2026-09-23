"""Retention-enforcement tests — Production-Grade Implementation Plan v1.3
correction #14's five required scenarios, plus a synthetic-collection
integration test proving the generic `sweep()` runner (not just the pure
decision function) works end to end. No evidence/document collection exists
yet (Phase 2/4), so this uses fabricated fixtures throughout, per the plan.
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

from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.retention import (  # noqa: E402
    RetainableItem,
    RetentionAction,
    build_policy_version_change_event,
    build_retention_audit_event,
    decide_retention_action,
    sweep,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOT_EXPIRED = NOW + timedelta(days=30)
EXPIRED = NOW - timedelta(days=1)


def item(**overrides) -> RetainableItem:
    base = dict(
        item_id="item-1", retention_expires_at=NOT_EXPIRED, legal_hold=False,
        object_lock_active=False, referenced_by_open_audit_or_capa=False,
        policy_version="policy-v1",
    )
    base.update(overrides)
    return RetainableItem(**base)


# Scenario 1: expired + no hold -> eligible for retention action -----------

def test_expired_with_no_hold_is_eligible_for_deletion():
    decision = decide_retention_action(item(retention_expires_at=EXPIRED), now=NOW)
    assert decision.action == RetentionAction.ELIGIBLE_FOR_DELETION


def test_not_yet_expired_is_retained_regardless_of_other_flags():
    decision = decide_retention_action(item(retention_expires_at=NOT_EXPIRED, legal_hold=True), now=NOW)
    assert decision.action == RetentionAction.RETAIN


# Scenario 2: expired + legal hold -> deletion blocked, reported as blocked -

def test_expired_with_legal_hold_is_blocked_and_reported_as_blocked():
    decision = decide_retention_action(item(retention_expires_at=EXPIRED, legal_hold=True), now=NOW)
    assert decision.action == RetentionAction.HELD
    event = build_retention_audit_event(decision, resource_type="evidence")
    assert event.result == "blocked"


# Scenario 3: active Object Lock -> deletion remains impossible; status matches reality -

def test_active_object_lock_blocks_deletion_and_is_reported_accurately():
    decision = decide_retention_action(
        item(retention_expires_at=EXPIRED, object_lock_active=True), now=NOW,
    )
    assert decision.action == RetentionAction.BLOCKED_BY_OBJECT_LOCK
    assert "never falsely" not in decision.reason  # sanity: this is prose from the docstring, not the reason text
    event = build_retention_audit_event(decision, resource_type="evidence")
    assert event.result == "blocked"  # never silently reported as a successful deletion


# Scenario 4: policy-version change -> itself versioned and audited --------

def test_policy_version_change_is_itself_versioned_and_audited():
    event = build_policy_version_change_event(old_version="policy-v1", new_version="policy-v2")
    assert event.action == "remote_audit.retention.policy_version_changed"
    assert event.before_ref == "policy-v1"
    assert event.after_ref == "policy-v2"
    assert event.policy_version == "policy-v2"
    assert event.result == "success"


# Scenario 5: referenced by an unresolved audit/CAPA -> excluded regardless of age -

def test_referenced_item_is_excluded_from_deletion_regardless_of_age():
    very_old = NOW - timedelta(days=3650)
    decision = decide_retention_action(
        item(retention_expires_at=very_old, referenced_by_open_audit_or_capa=True), now=NOW,
    )
    assert decision.action == RetentionAction.REFERENCED


def test_legal_hold_takes_precedence_over_object_lock_and_reference_flags():
    """When multiple blocking conditions are true at once, the strongest one
    (legal hold) is what's reported, not an arbitrary one."""
    decision = decide_retention_action(
        item(retention_expires_at=EXPIRED, legal_hold=True, object_lock_active=True,
             referenced_by_open_audit_or_capa=True),
        now=NOW,
    )
    assert decision.action == RetentionAction.HELD


# Integration: the generic sweep() runner over a synthetic collection ------

RUN_ID = uuid.uuid4().hex[:8]
COLLECTION = f"remote_audit_test_retainable_items_{RUN_ID}"


@pytest_asyncio.fixture
async def db():
    database = get_db()
    yield database
    await database.drop_collection(COLLECTION)
    database.client.close()


def _adapt(doc: dict) -> RetainableItem:
    return RetainableItem(
        item_id=doc["id"], retention_expires_at=doc["retention_expires_at"],
        legal_hold=doc["legal_hold"], object_lock_active=doc["object_lock_active"],
        referenced_by_open_audit_or_capa=doc["referenced"], policy_version=doc["policy_version"],
    )


@pytest.mark.asyncio
async def test_sweep_applies_the_correct_decision_to_each_synthetic_item(db):
    fixtures = [
        {"id": "not-expired", "retention_expires_at": NOT_EXPIRED, "legal_hold": False,
         "object_lock_active": False, "referenced": False, "policy_version": "policy-v1"},
        {"id": "expired-clean", "retention_expires_at": EXPIRED, "legal_hold": False,
         "object_lock_active": False, "referenced": False, "policy_version": "policy-v1"},
        {"id": "expired-held", "retention_expires_at": EXPIRED, "legal_hold": True,
         "object_lock_active": False, "referenced": False, "policy_version": "policy-v1"},
        {"id": "expired-locked", "retention_expires_at": EXPIRED, "legal_hold": False,
         "object_lock_active": True, "referenced": False, "policy_version": "policy-v1"},
        {"id": "expired-referenced", "retention_expires_at": EXPIRED, "legal_hold": False,
         "object_lock_active": False, "referenced": True, "policy_version": "policy-v1"},
    ]
    await db[COLLECTION].insert_many(fixtures)

    seen_actions = {}

    async def record(decision, _doc):
        seen_actions[decision.item_id] = decision.action

    decisions = await sweep(db, COLLECTION, adapt=_adapt, on_decision=record, now=NOW)

    assert len(decisions) == 5
    assert seen_actions["not-expired"] == RetentionAction.RETAIN
    assert seen_actions["expired-clean"] == RetentionAction.ELIGIBLE_FOR_DELETION
    assert seen_actions["expired-held"] == RetentionAction.HELD
    assert seen_actions["expired-locked"] == RetentionAction.BLOCKED_BY_OBJECT_LOCK
    assert seen_actions["expired-referenced"] == RetentionAction.REFERENCED
