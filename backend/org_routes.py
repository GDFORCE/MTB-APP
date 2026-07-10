"""Org-admin console API — Task 6.1.

Routes live under ``/api/org/{org_id}/…`` and are gated fail-closed by
``org_admin_ctx``: the caller must be a platform admin, OR carry the
``org_admin`` flag AND belong to that exact organization (an org-admin of
org A can never touch org B → 403). A softer ``org_member_ctx`` gate exists
only for the ownership-transfer ACCEPT step, which is performed by the
successor (who is not yet an org admin).

Trial reads return aggregates + masked subjects only (SUBJ-xxx + initials) —
no raw patient PII ever leaves these endpoints. Every mutation is audited.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from server import (
    INVITE_TTL_DAYS,
    Role,
    _invitation_status,
    _invite_link,
    current_user,
    db,
    iso,
    now,
    serialize,
    write_audit,
)

router = APIRouter(prefix='/api/org')
# access-requests live on the trial resource per the audit doc (§CONSOLIDATED):
# POST /api/trials/{id}/access-requests (+grant)
trial_access_router = APIRouter(prefix='/api/trials')


# ── Guards ───────────────────────────────────────────────────────────────────
async def _get_org_or_404(org_id: str) -> dict:
    org = await db.organizations.find_one({'id': org_id}, {'_id': 0})
    if not org:
        raise HTTPException(404, 'Organization not found')
    return org


def _same_org(user: dict, org: dict) -> bool:
    return (user.get('organization') or '').strip() == org['name']


async def org_admin_ctx(org_id: str, user=Depends(current_user)) -> dict:
    """Fail-closed org-admin gate: platform admin passes; otherwise the caller
    must hold the org_admin flag AND be a member of THIS org."""
    org = await _get_org_or_404(org_id)
    if user['role'] == 'admin':
        return {'org': org, 'user': user, 'platform_admin': True}
    if not user.get('org_admin'):
        raise HTTPException(403, 'Org-admin access required')
    if not _same_org(user, org):
        raise HTTPException(403, 'You may only administer your own organization')
    return {'org': org, 'user': user, 'platform_admin': False}


async def org_member_ctx(org_id: str, user=Depends(current_user)) -> dict:
    """Membership gate (ownership-transfer accept only): the successor is a
    member of the org but not yet its admin."""
    org = await _get_org_or_404(org_id)
    if user['role'] != 'admin' and not _same_org(user, org):
        raise HTTPException(403, 'You are not a member of this organization')
    return {'org': org, 'user': user}
