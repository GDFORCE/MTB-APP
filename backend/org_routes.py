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


USER_PROJECTION = {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0}


def _member_row(u: dict, caller_id: str) -> dict:
    status = (u.get('status') or 'active').lower()
    return {
        'id': u['id'], 'name': u.get('full_name', ''), 'email': u.get('email', ''),
        'designation': (u.get('profile') or {}).get('designation', ''),
        'role': u.get('role', ''), 'site': u.get('site', ''),
        'department': (u.get('profile') or {}).get('department', ''),
        'admin': bool(u.get('org_admin')), 'status': status,
        'you': u['id'] == caller_id,
    }


def _masked_subject(p: dict) -> dict:
    """SUBJ-xxx + initials only — org consoles never see patient PII."""
    return {
        'subject': f"SUBJ-{(p.get('id') or '')[-3:]}",
        'initials': p.get('avatar_initials', ''),
        'status': p.get('status', ''),
        'enrolled_date': p.get('enrolled_date', ''),
    }


async def _org_member_ids(org: dict) -> List[str]:
    rows = await db.users.find({'organization': org['name']}, {'_id': 0, 'id': 1}).to_list(5000)
    return [r['id'] for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# TEAM ROSTER
# ═════════════════════════════════════════════════════════════════════════════
class OrgInviteIn(BaseModel):
    email: EmailStr
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    role: Role = 'crc'
    site: Optional[str] = ''


class AssignSiteIn(BaseModel):
    site: str = Field(min_length=1)


@router.get('/{org_id}/members')
async def org_members(ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    rows = await db.users.find({'organization': org['name']}, USER_PROJECTION) \
        .sort('created_at', 1).to_list(2000)
    members = [_member_row(u, user['id']) for u in rows]
    # pending invitations show as "invited" roster rows
    invites = await db.invitations.find({'org': org['name'], 'status': 'pending'},
                                        {'_id': 0}).to_list(500)
    for inv in invites:
        if _invitation_status(inv) != 'pending':
            continue
        members.append({
            'id': f"invite:{inv['id']}", 'name': inv.get('full_name', ''),
            'email': inv.get('email', ''), 'designation': inv.get('designation', ''),
            'role': inv.get('role', ''), 'site': inv.get('site', ''), 'department': '',
            'admin': False, 'status': 'invited', 'you': False,
        })
    return members


@router.post('/{org_id}/members/invite')
async def org_invite_member(body: OrgInviteIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    email = body.email.lower()
    existing = await db.users.find_one({'email': email}, {'_id': 0, 'organization': 1})
    if existing and (existing.get('organization') or '').strip() == org['name']:
        raise HTTPException(400, 'This person is already a member of the organization')
    token = uuid.uuid4().hex
    doc = {
        'id': str(uuid.uuid4()), 'token': token, 'email': email, 'phone': '',
        'full_name': body.full_name or '', 'designation': body.designation or '',
        'role': body.role, 'trial_id': None, 'invited_by': user['id'],
        'org': org['name'], 'org_id': org['id'], 'site': (body.site or '').strip(),
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS), 'resend_count': 0,
    }
    await db.invitations.insert_one(doc)
    await write_audit(user, 'org.member_invite',
                      f"Invited {email} to {org['name']} as {body.role}",
                      target_id=doc['id'], org_id=org['id'])
    return {**serialize(doc), 'invite_link': _invite_link(token)}


@router.delete('/{org_id}/members/{member_id}')
async def org_remove_member(member_id: str, ctx=Depends(org_admin_ctx)):
    """Deactivate a roster member (record retained — regulated app)."""
    org, user = ctx['org'], ctx['user']
    if member_id == user['id']:
        raise HTTPException(400, 'You cannot remove yourself — transfer ownership instead')
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    await db.users.update_one({'id': member_id}, {'$set': {
        'status': 'Deactivated', 'org_admin': False, 'deactivated_at': now(),
        'deactivated_by': user['id']}})
    await write_audit(user, 'org.member_remove',
                      f"Deactivated {member.get('email')} in {org['name']}",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'status': 'deactivated'}


@router.post('/{org_id}/members/{member_id}/make-admin')
async def org_make_admin(member_id: str, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    if member.get('role') == 'patient':
        raise HTTPException(400, 'A patient account cannot administer an organization')
    await db.users.update_one({'id': member_id}, {'$set': {'org_admin': True}})
    await write_audit(user, 'org.member_make_admin',
                      f"Granted org-admin to {member.get('email')} in {org['name']}",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'admin': True}


@router.post('/{org_id}/members/{member_id}/assign-site')
async def org_assign_site(member_id: str, body: AssignSiteIn, ctx=Depends(org_admin_ctx)):
    """Cross-site staff assignment (SMO hospital networks)."""
    org, user = ctx['org'], ctx['user']
    member = await db.users.find_one({'id': member_id}, USER_PROJECTION)
    if not member or (member.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Member not found in this organization')
    site_name = body.site.strip()
    await db.users.update_one({'id': member_id}, {'$set': {'site': site_name}})
    await write_audit(user, 'org.member_assign_site',
                      f"Assigned {member.get('email')} to site \"{site_name}\"",
                      target_id=member_id, org_id=org['id'])
    return {'ok': True, 'id': member_id, 'site': site_name}


# ═════════════════════════════════════════════════════════════════════════════
# OWNERSHIP TRANSFER (admin → successor, acceptance required)
# ═════════════════════════════════════════════════════════════════════════════
class OwnershipTransferIn(BaseModel):
    successor_id: str
    reason: str = Field(min_length=10)
    handover: Literal['deactivate', 'remove'] = 'deactivate'


@router.post('/{org_id}/ownership-transfer')
async def org_start_ownership_transfer(body: OwnershipTransferIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    if body.successor_id == user['id']:
        raise HTTPException(400, 'You already administer this organization')
    successor = await db.users.find_one({'id': body.successor_id}, USER_PROJECTION)
    if not successor or (successor.get('organization') or '').strip() != org['name']:
        raise HTTPException(404, 'Successor not found in this organization')
    if successor.get('role') == 'patient':
        raise HTTPException(400, 'A patient account cannot receive ownership')
    pending = await db.ownership_transfers.find_one(
        {'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'An ownership transfer is already pending for this organization')
    doc = {
        'id': str(uuid.uuid4()), 'org_id': org['id'], 'org_name': org['name'],
        'from_user': user['id'], 'from_name': user.get('full_name', ''),
        'to_user': successor['id'], 'to_name': successor.get('full_name', ''),
        'reason': body.reason, 'handover': body.handover,
        'status': 'pending', 'created_at': now(),
    }
    await db.ownership_transfers.insert_one(doc)
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()), 'user_id': successor['id'],
        'title': f"Ownership transfer · {org['name']}",
        'body': f"{user.get('full_name', 'The current admin')} has asked you to take over "
                f"as the organization admin. Open the console to accept.",
        'kind': 'system', 'read': False, 'created_at': now()})
    await write_audit(user, 'org.ownership_transfer_start',
                      f"Proposed ownership transfer of {org['name']} to "
                      f"{successor.get('email')} — {body.reason}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})


@router.post('/{org_id}/ownership-transfer/{transfer_id}/accept')
async def org_accept_ownership_transfer(transfer_id: str, ctx=Depends(org_member_ctx)):
    """Accepted by the SUCCESSOR (a member who is not yet org admin)."""
    org, user = ctx['org'], ctx['user']
    transfer = await db.ownership_transfers.find_one({'id': transfer_id}, {'_id': 0})
    if not transfer or transfer.get('org_id') != org['id']:
        raise HTTPException(404, 'Transfer not found')
    if transfer.get('status') != 'pending':
        raise HTTPException(400, f"This transfer is already {transfer.get('status')}")
    if transfer.get('to_user') != user['id']:
        raise HTTPException(403, 'Only the designated successor can accept this transfer')
    n = now()
    await db.users.update_one({'id': user['id']}, {'$set': {'org_admin': True}})
    handover = transfer.get('handover', 'deactivate')
    old_updates = {'org_admin': False}
    if handover == 'deactivate':
        old_updates.update({'status': 'Deactivated', 'deactivated_at': n})
    await db.users.update_one({'id': transfer['from_user']}, {'$set': old_updates})
    await db.ownership_transfers.update_one({'id': transfer_id}, {'$set': {
        'status': 'accepted', 'accepted_at': n}})
    await write_audit(user, 'org.ownership_transfer_accept',
                      f"Accepted ownership of {org['name']} from {transfer.get('from_name')} "
                      f"(handover: {handover})",
                      target_id=transfer_id, org_id=org['id'])
    return {'ok': True, 'id': transfer_id, 'status': 'accepted', 'handover': handover}


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL (org-scoped)
# ═════════════════════════════════════════════════════════════════════════════
@router.get('/{org_id}/audit-trail')
async def org_audit_trail(kind: Optional[str] = None, limit: int = 300,
                          ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    member_ids = await _org_member_ids(org)
    q: Dict = {'$or': [{'org': org['name']}, {'user_id': {'$in': member_ids}},
                       {'org_id': org['id']}]}
    if kind:
        q['category'] = kind
    rows = await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1) \
        .to_list(max(1, min(limit, 1000)))
    return [{
        'id': r.get('id'), 'at': iso(r.get('created_at')), 'actor': r.get('user_name', ''),
        'action': r.get('action', ''), 'detail': r.get('detail', ''),
        'kind': r.get('category', ''), 'trial': r.get('trial_id'),
        'status': r.get('status', ''),
    } for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# SITES (SMO hospital network)
# ═════════════════════════════════════════════════════════════════════════════
class SiteIn(BaseModel):
    name: str = Field(min_length=1)
    address: Optional[str] = ''


@router.get('/{org_id}/sites')
async def org_sites(ctx=Depends(org_admin_ctx)):
    return await db.org_sites.find({'org_id': ctx['org']['id']}, {'_id': 0}) \
        .sort('name', 1).to_list(500)


@router.post('/{org_id}/sites')
async def org_add_site(body: SiteIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    name = body.name.strip()
    if await db.org_sites.find_one({'org_id': org['id'], 'name': name}):
        raise HTTPException(400, 'This site is already in the network')
    doc = {'id': str(uuid.uuid4()), 'org_id': org['id'], 'name': name,
           'address': body.address or '', 'created_at': now(), 'created_by': user['id']}
    await db.org_sites.insert_one(doc)
    await write_audit(user, 'org.site_add',
                      f"Added site \"{name}\" to {org['name']}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})


@router.delete('/{org_id}/sites/{site_id}')
async def org_remove_site(site_id: str, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    site = await db.org_sites.find_one({'id': site_id, 'org_id': org['id']}, {'_id': 0})
    if not site:
        raise HTTPException(404, 'Site not found in this organization')
    await db.org_sites.delete_one({'id': site_id})
    await write_audit(user, 'org.site_remove',
                      f"Removed site \"{site.get('name')}\" from {org['name']}",
                      target_id=site_id, org_id=org['id'])
    return {'ok': True, 'id': site_id}


# ═════════════════════════════════════════════════════════════════════════════
# TRIALS (access-keyed: full for created/granted, restricted = schedule-only;
# subjects ALWAYS masked — org consoles never see patient PII)
# ═════════════════════════════════════════════════════════════════════════════
async def _org_trial_ids_with_grant(org_id: str) -> set:
    rows = await db.org_trial_access.find(
        {'org_id': org_id, 'granted': True}, {'_id': 0, 'trial_id': 1}).to_list(500)
    return {r['trial_id'] for r in rows}


@router.get('/{org_id}/trials')
async def org_trials(ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    member_ids = set(await _org_member_ids(org))
    granted = await _org_trial_ids_with_grant(org['id'])

    # candidate trials: owned by the org (sponsor_name / creator) or worked by
    # its staff (a patient at this org's site), or explicitly granted.
    owned = await db.trials.find(
        {'$or': [{'sponsor_name': org['name']},
                 {'created_by': {'$in': list(member_ids)}}]}, {'_id': 0}).to_list(500)
    related_ids = set()
    async for p in db.patients.find(
            {'$or': [{'pi_id': {'$in': list(member_ids)}},
                     {'crc_id': {'$in': list(member_ids)}}]},
            {'_id': 0, 'trial_id': 1}):
        if p.get('trial_id'):
            related_ids.add(p['trial_id'])
    extra_ids = (related_ids | granted) - {t['id'] for t in owned}
    extra = await db.trials.find({'id': {'$in': list(extra_ids)}}, {'_id': 0}).to_list(500)

    out = []
    for t in owned + extra:
        full = (t.get('sponsor_name') == org['name']
                or t.get('created_by') in member_ids
                or t['id'] in granted)
        row = {
            'id': t['id'], 'title': t.get('title'), 'protocol_id': t.get('protocol_id'),
            'phase': t.get('phase'), 'condition': t.get('condition'),
            'status': t.get('status', 'active'),
            'accessLevel': 'full' if full else 'restricted',
            'createdBy': t.get('created_by'),
            'enrolled': await db.patients.count_documents({'trial_id': t['id']}),
        }
        visits = await db.visits.find({'trial_id': t['id']}, {'_id': 0}) \
            .sort('visit_number', 1).to_list(200)
        row['schedule'] = [{'visit_number': v.get('visit_number'), 'name': v.get('name'),
                            'day_offset': v.get('day_offset'),
                            'window_days': v.get('window_days')} for v in visits]
        if full:
            patients = await db.patients.find({'trial_id': t['id']}, {'_id': 0}).to_list(1000)
            row['subjects'] = [_masked_subject(p) for p in patients]
        out.append(row)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# TRIAL ACCESS REQUESTS — /api/trials/{id}/access-requests (+grant)
# ═════════════════════════════════════════════════════════════════════════════
class AccessRequestIn(BaseModel):
    org_id: str
    reason: Optional[str] = ''


@trial_access_router.post('/{trial_id}/access-requests')
async def trial_request_access(trial_id: str, body: AccessRequestIn,
                               user=Depends(current_user)):
    """An org admin asks for FULL access to a trial their staff only has
    restricted (schedule-only) visibility of."""
    org = await _get_org_or_404(body.org_id)
    if user['role'] != 'admin':
        if not user.get('org_admin'):
            raise HTTPException(403, 'Org-admin access required')
        if not _same_org(user, org):
            raise HTTPException(403, 'You may only request access for your own organization')
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    pending = await db.trial_access_requests.find_one(
        {'trial_id': trial_id, 'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'An access request is already pending')
    doc = {
        'id': str(uuid.uuid4()), 'trial_id': trial_id, 'org_id': org['id'],
        'org_name': org['name'], 'requested_by': user['id'],
        'requester_name': user.get('full_name', ''), 'reason': body.reason or '',
        'status': 'pending', 'created_at': now(),
    }
    await db.trial_access_requests.insert_one(doc)
    await write_audit(user, 'org.trial_access_request',
                      f"Requested full access to trial {trial.get('protocol_id', trial_id)} "
                      f"for {org['name']}", target_id=doc['id'],
                      trial_id=trial_id, org_id=org['id'])
    return serialize({**doc})


@trial_access_router.post('/{trial_id}/access-requests/{request_id}/grant')
async def trial_grant_access(trial_id: str, request_id: str, user=Depends(current_user)):
    """Granted by a platform admin OR an org-admin of the trial-owning org."""
    req = await db.trial_access_requests.find_one({'id': request_id}, {'_id': 0})
    if not req or req.get('trial_id') != trial_id:
        raise HTTPException(404, 'Access request not found')
    if req.get('status') != 'pending':
        raise HTTPException(400, f"This request is already {req.get('status')}")
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if user['role'] != 'admin':
        owns = user.get('org_admin') and (
            (user.get('organization') or '').strip() == (trial.get('sponsor_name') or '').strip()
            or trial.get('created_by') == user['id'])
        if not owns:
            raise HTTPException(403, 'Only the trial-owning organization admin '
                                     'or a platform admin can grant access')
    n = now()
    await db.org_trial_access.update_one(
        {'org_id': req['org_id'], 'trial_id': trial_id},
        {'$set': {'granted': True, 'granted_by': user['id'], 'granted_at': n},
         '$setOnInsert': {'id': str(uuid.uuid4())}},
        upsert=True)
    await db.trial_access_requests.update_one({'id': request_id}, {'$set': {
        'status': 'granted', 'granted_by': user['id'], 'granted_at': n}})
    await write_audit(user, 'org.trial_access_grant',
                      f"Granted {req.get('org_name')} full access to trial "
                      f"{trial.get('protocol_id', trial_id)}",
                      target_id=request_id, trial_id=trial_id, org_id=req['org_id'])
    return {'ok': True, 'id': request_id, 'status': 'granted'}


# ═════════════════════════════════════════════════════════════════════════════
# TRIAL-CREATION DELEGATION (gate for new/edit/delete trial in org consoles)
# ═════════════════════════════════════════════════════════════════════════════
class OrgDelegationRequestIn(BaseModel):
    reason: str = Field(min_length=10)


@router.get('/{org_id}/delegation-status')
async def org_delegation_status(ctx=Depends(org_admin_ctx)):
    org = ctx['org']
    latest = await db.org_delegation_requests.find_one(
        {'org_id': org['id']}, {'_id': 0}, sort=[('created_at', -1)])
    return {'delegated': bool(org.get('trial_creation_delegated')),
            'request': latest}


@router.post('/{org_id}/delegation-requests')
async def org_request_delegation(body: OrgDelegationRequestIn, ctx=Depends(org_admin_ctx)):
    org, user = ctx['org'], ctx['user']
    if org.get('trial_creation_delegated'):
        raise HTTPException(400, 'Trial creation is already delegated to this organization')
    pending = await db.org_delegation_requests.find_one(
        {'org_id': org['id'], 'status': 'pending'})
    if pending:
        raise HTTPException(400, 'A delegation request is already pending')
    doc = {
        'id': str(uuid.uuid4()), 'org_id': org['id'], 'org_name': org['name'],
        'requested_by': user['id'], 'requester_name': user.get('full_name', ''),
        'reason': body.reason, 'status': 'pending', 'created_at': now(),
    }
    await db.org_delegation_requests.insert_one(doc)
    await write_audit(user, 'org.delegation_request',
                      f"Requested trial-creation delegation for {org['name']} — {body.reason}",
                      target_id=doc['id'], org_id=org['id'])
    return serialize({**doc})
