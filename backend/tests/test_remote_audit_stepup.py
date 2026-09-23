"""Tests for step-up re-authentication (app/services/remote_audit/stepup.py)
— v1.3 correction #2: password-only step-up is a dev/pilot fallback, and
production (REMOTE_AUDIT_STEPUP_REQUIRES_MFA=true, the default) must refuse
to mint a token without MFA.
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

from app.services.remote_audit import stepup  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await stepup.ensure_indexes(database)
    yield database
    await database[stepup.STEPUP_TOKENS_COLLECTION].delete_many({"user_id": {"$regex": f"^{RUN_ID}"}})
    database.client.close()


def test_stepup_requires_mfa_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", raising=False)
    assert stepup.stepup_requires_mfa() is True


def test_stepup_requires_mfa_can_be_explicitly_disabled_for_dev(monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "false")
    assert stepup.stepup_requires_mfa() is False


@pytest.mark.asyncio
async def test_production_default_refuses_password_only_stepup(db, monkeypatch):
    monkeypatch.delenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", raising=False)
    with pytest.raises(stepup.StepUpError) as exc:
        await stepup.issue_stepup_token(
            db, user_id=f"{RUN_ID}-u1", action_scope="geofence.approve", mfa_verified=False,
        )
    assert exc.value.code == "MFA_REQUIRED"


@pytest.mark.asyncio
async def test_dev_mode_allows_password_only_stepup(db, monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "false")
    token = await stepup.issue_stepup_token(
        db, user_id=f"{RUN_ID}-u2", action_scope="geofence.approve", mfa_verified=False,
    )
    assert token["token"]


@pytest.mark.asyncio
async def test_mfa_verified_always_allowed_regardless_of_environment(db, monkeypatch):
    monkeypatch.delenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", raising=False)  # production default
    token = await stepup.issue_stepup_token(
        db, user_id=f"{RUN_ID}-u3", action_scope="template.activate", mfa_verified=True,
    )
    assert token["token"]


@pytest.mark.asyncio
async def test_token_is_one_time_use(db, monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "false")
    user_id = f"{RUN_ID}-u4"
    token = await stepup.issue_stepup_token(db, user_id=user_id, action_scope="geofence.approve", mfa_verified=False)
    first = await stepup.consume_stepup_token(db, token=token["token"], user_id=user_id, action_scope="geofence.approve")
    second = await stepup.consume_stepup_token(db, token=token["token"], user_id=user_id, action_scope="geofence.approve")
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_token_is_scope_bound(db, monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "false")
    user_id = f"{RUN_ID}-u5"
    token = await stepup.issue_stepup_token(db, user_id=user_id, action_scope="geofence.approve", mfa_verified=False)
    wrong_scope = await stepup.consume_stepup_token(
        db, token=token["token"], user_id=user_id, action_scope="template.activate")
    assert wrong_scope is False


@pytest.mark.asyncio
async def test_token_is_user_bound(db, monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "false")
    user_id = f"{RUN_ID}-u6"
    token = await stepup.issue_stepup_token(db, user_id=user_id, action_scope="geofence.approve", mfa_verified=False)
    wrong_user = await stepup.consume_stepup_token(
        db, token=token["token"], user_id=f"{RUN_ID}-someone-else", action_scope="geofence.approve")
    assert wrong_user is False


@pytest.mark.asyncio
async def test_missing_token_is_rejected(db):
    assert await stepup.consume_stepup_token(db, token=None, user_id="x", action_scope="y") is False
