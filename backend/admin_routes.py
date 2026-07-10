"""Platform-admin API — Task 6.1.

Every route on this router is admin-only: the router carries a router-level
dependency that 403s any caller whose role is not ``admin`` (fail-closed —
adding a route here can never accidentally ship unguarded). Every mutation
writes an audit row via ``server.write_audit``.

Groups (see docs/superpowers/audits/2026-07-07-admin-api-audit.md §CONSOLIDATED):
users, organizations, master-data, terms, tickets, audit, alerts,
notification-monitoring, reports, delegations, emergency (break-the-glass),
invitations, messages (broadcasts), admin trials.

Patient PII rules:
- user lists / exports pseudonymize patient names + contact details
- trial reads return AGGREGATES + masked subjects (SUBJ-xxx + initials) unless
  the caller holds an ACTIVE break-the-glass session — and every unmasked read
  during a session is itself audited with the session id.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field

import storage as file_storage
from server import (
    INVITE_TTL_DAYS,
    ORG_TYPES,
    Role,
    _invitation_status,
    _invite_link,
    _parse_ymd,
    current_user,
    db,
    iso,
    now,
    pwd_ctx,
    require_roles,
    serialize,
    write_audit,
)

# Router-level guard: EVERY admin route 403s non-admin callers.
router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_roles('admin'))])

USER_PROJECTION = {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0}


# ── PII masking helpers ──────────────────────────────────────────────────────
def _mask_name(name: str) -> str:
    parts = [p for p in (name or '').split() if p]
    return ' '.join(f'{p[0].upper()}***' for p in parts) or 'U***'


def _mask_email(email: str) -> str:
    email = email or ''
    if '@' not in email:
        return '***' if email else ''
    local, dom = email.split('@', 1)
    return f'{local[:1]}***@{dom}'


def _mask_phone(phone: str) -> str:
    p = (phone or '').strip()
    if not p:
        return ''
    if len(p) < 6:
        return '***'
    return p[:3] + '*' * (len(p) - 5) + p[-2:]


def _pseudonymize_patient(u: dict) -> dict:
    """Patients are pseudonymized in admin lists/exports (regulated app)."""
    if u.get('role') == 'patient':
        u = dict(u)
        u['full_name'] = _mask_name(u.get('full_name', ''))
        u['email'] = _mask_email(u.get('email', ''))
        u['phone'] = _mask_phone(u.get('phone', ''))
        u['pseudonymized'] = True
    return u


def _masked_subject(p: dict) -> dict:
    """Trial subject rows carry NO PII: stable pseudo-label + initials only."""
    return {
        'subject': f"SUBJ-{(p.get('id') or '')[-3:]}",
        'initials': p.get('avatar_initials', ''),
        'status': p.get('status', ''),
        'enrolled_date': p.get('enrolled_date', ''),
    }


def _user_status(u: dict) -> str:
    if u.get('status'):
        return u['status']
    if u.get('lock_info'):
        return 'Locked'
    return 'Active'


async def _find_or_404(coll, doc_id: str, what: str) -> dict:
    doc = await coll.find_one({'id': doc_id}, {'_id': 0})
    if not doc:
        raise HTTPException(404, f'{what} not found')
    return doc


# ═════════════════════════════════════════════════════════════════════════════
# USERS
# ═════════════════════════════════════════════════════════════════════════════
class AdminUserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1)
    role: Role
    phone: Optional[str] = ''
    organization: Optional[str] = ''
    send_invite: bool = True


class UserStatusIn(BaseModel):
    status: Literal['Active', 'Suspended']
    reason: Optional[str] = ''


class UnlockIn(BaseModel):
    identity_checks: List[str] = Field(min_length=2)
    reason: str = Field(min_length=10)
    force_password_reset: bool = False


@router.get('/users')
async def admin_list_users(search: Optional[str] = None, role: Optional[str] = None,
                           status: Optional[str] = None, limit: int = Query(500, le=2000)):
    q: Dict = {}
    if role:
        q['role'] = role
    if search:
        rx = {'$regex': search, '$options': 'i'}
        q['$or'] = [{'full_name': rx}, {'email': rx}, {'organization': rx}]
    rows = await db.users.find(q, USER_PROJECTION).sort('created_at', -1).to_list(limit)
    out = []
    for u in rows:
        u['status'] = _user_status(u)
        if status and u['status'] != status:
            continue
        out.append(_pseudonymize_patient(u))
    return out


@router.get('/users/export')
async def admin_export_users(admin=Depends(current_user)):
    """CSV export of the user directory (patients pseudonymized)."""
    rows = await db.users.find({}, USER_PROJECTION).sort('created_at', -1).to_list(5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['id', 'name', 'email', 'phone', 'role', 'organization', 'status', 'created_at'])
    for u in rows:
        u = _pseudonymize_patient(u)
        w.writerow([u.get('id'), u.get('full_name'), u.get('email'), u.get('phone'),
                    u.get('role'), u.get('organization'), _user_status(u), iso(u.get('created_at'))])
    await write_audit(admin, 'admin.users_export', f'Exported {len(rows)} users to CSV')
    return Response(content=buf.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="users.csv"'})


@router.post('/users')
async def admin_create_user(body: AdminUserCreate, admin=Depends(current_user)):
    email = body.email.lower()
    if await db.users.find_one({'email': email}):
        raise HTTPException(400, 'Email already registered')
    temp_password = f'Temp-{uuid.uuid4().hex[:8]}!A1'
    doc = {
        'id': str(uuid.uuid4()), 'email': email, 'full_name': body.full_name.strip(),
        'role': body.role, 'phone': body.phone or '', 'organization': body.organization or '',
        'hashed_password': pwd_ctx.hash(temp_password),
        'security_question': '', 'security_answer_hash': '',
        'avatar_initials': ''.join(w[0].upper() for w in body.full_name.split()[:2]) or 'U',
        'status': 'Pending Verification', 'must_reset_password': True,
        'created_at': now(), 'is_online': False, 'created_by_admin': admin['id'],
    }
    await db.users.insert_one(doc)
    invitation = None
    if body.send_invite:
        inv = {
            'id': str(uuid.uuid4()), 'token': uuid.uuid4().hex, 'email': email,
            'phone': body.phone or '', 'full_name': body.full_name, 'role': body.role,
            'trial_id': None, 'invited_by': admin['id'],
            'org': (body.organization or '').strip(), 'site': '',
            'status': 'pending', 'created_at': now(),
            'expires_at': now() + timedelta(days=INVITE_TTL_DAYS), 'resend_count': 0,
        }
        await db.invitations.insert_one(inv)
        invitation = {**serialize(inv), 'invite_link': _invite_link(inv['token'])}
    await write_audit(admin, 'admin.user_create',
                      f"Created user {email} ({body.role})", target_id=doc['id'])
    return {'user': serialize({**doc}), 'invitation': invitation, 'temp_password': temp_password}


@router.get('/users/{user_id}')
async def admin_get_user(user_id: str):
    u = await db.users.find_one({'id': user_id}, USER_PROJECTION)
    if not u:
        raise HTTPException(404, 'User not found')
    u['status'] = _user_status(u)
    return _pseudonymize_patient(u)


@router.patch('/users/{user_id}/status')
async def admin_set_user_status(user_id: str, body: UserStatusIn, admin=Depends(current_user)):
    u = await _find_or_404(db.users, user_id, 'User')
    updates: Dict = {'status': body.status}
    if body.status == 'Suspended':
        updates['force_logout_at'] = now()   # kill active sessions immediately
    await db.users.update_one({'id': user_id}, {'$set': updates})
    await write_audit(admin, 'admin.user_status',
                      f"Set {u.get('email')} status to {body.status}"
                      + (f" — {body.reason}" if body.reason else ''),
                      target_id=user_id)
    return {'ok': True, 'id': user_id, 'status': body.status}


@router.post('/users/{user_id}/unlock')
async def admin_unlock_user(user_id: str, body: UnlockIn, admin=Depends(current_user)):
    """Unlock requires ≥2 completed identity checks and a reason ≥10 chars
    (regulatory traceability). Optionally forces a password reset."""
    u = await _find_or_404(db.users, user_id, 'User')
    checks = [c.strip() for c in body.identity_checks if c and c.strip()]
    if len(checks) < 2:
        raise HTTPException(400, 'At least 2 identity checks are required to unlock')
    updates: Dict = {'status': 'Active', 'failed_attempts': 0}
    if body.force_password_reset:
        updates['must_reset_password'] = True
    await db.users.update_one({'id': user_id},
                              {'$set': updates, '$unset': {'lock_info': ''}})
    await write_audit(admin, 'admin.user_unlock',
                      f"Unlocked {u.get('email')} — {body.reason}",
                      target_id=user_id, identity_checks=checks,
                      force_password_reset=body.force_password_reset)
    return {'ok': True, 'id': user_id, 'status': 'Active'}


@router.post('/users/{user_id}/reset-password')
async def admin_reset_password(user_id: str, admin=Depends(current_user)):
    u = await _find_or_404(db.users, user_id, 'User')
    temp_password = f'Temp-{uuid.uuid4().hex[:8]}!A1'
    await db.users.update_one({'id': user_id}, {'$set': {
        'hashed_password': pwd_ctx.hash(temp_password),
        'must_reset_password': True, 'force_logout_at': now()}})
    await write_audit(admin, 'admin.user_reset_password',
                      f"Reset password for {u.get('email')}", target_id=user_id)
    return {'ok': True, 'id': user_id, 'temp_password': temp_password}


@router.post('/users/{user_id}/force-logout')
async def admin_force_logout(user_id: str, admin=Depends(current_user)):
    u = await _find_or_404(db.users, user_id, 'User')
    await db.users.update_one({'id': user_id}, {'$set': {'force_logout_at': now(), 'is_online': False}})
    await write_audit(admin, 'admin.user_force_logout',
                      f"Forced logout for {u.get('email')}", target_id=user_id)
    return {'ok': True, 'id': user_id}


# ═════════════════════════════════════════════════════════════════════════════
# ORGANIZATIONS
# ═════════════════════════════════════════════════════════════════════════════
class OrgCreate(BaseModel):
    name: str = Field(min_length=1)
    type: Literal['sponsor', 'cro', 'smo', 'site']
    address: Optional[str] = ''
    contact: Optional[str] = ''
    email: Optional[str] = ''
    website: Optional[str] = ''


class OrgPatch(BaseModel):
    name: Optional[str] = None
    type: Optional[Literal['sponsor', 'cro', 'smo', 'site']] = None
    address: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    status: Optional[Literal['active', 'suspended']] = None


class OrgMergeIn(BaseModel):
    target_org_id: str
    justification: str = Field(min_length=10)


class NameRequestApproveIn(BaseModel):
    finalName: str = Field(min_length=1)


class NameRequestRejectIn(BaseModel):
    reason: str = Field(min_length=1)


async def _org_counts(org: dict) -> dict:
    org = dict(org)
    org['users'] = await db.users.count_documents({'organization': org['name']})
    org['trials'] = await db.trials.count_documents({'sponsor_name': org['name']})
    return org


@router.get('/organizations')
async def admin_list_orgs(type: Optional[str] = None, search: Optional[str] = None,
                          status: Optional[str] = None):
    q: Dict = {}
    if type:
        q['type'] = type
    if status:
        q['status'] = status
    if search:
        q['name'] = {'$regex': search, '$options': 'i'}
    rows = await db.organizations.find(q, {'_id': 0}).sort('name', 1).to_list(1000)
    return [await _org_counts(o) for o in rows]


@router.get('/organizations/duplicates')
async def admin_org_duplicates():
    """Groups of organizations whose normalized names collide (merge candidates)."""
    rows = await db.organizations.find({'status': {'$ne': 'merged'}}, {'_id': 0}).to_list(2000)
    groups: Dict[str, List[dict]] = {}
    for o in rows:
        key = ''.join(ch for ch in (o.get('name') or '').lower() if ch.isalnum())
        groups.setdefault(key, []).append(o)
    return [{'key': k, 'organizations': v} for k, v in groups.items() if len(v) > 1]


@router.get('/organizations/name-requests')
async def admin_org_name_requests(status: Optional[str] = None):
    q: Dict = {}
    if status:
        q['status'] = status
    return await db.org_name_requests.find(q, {'_id': 0}).sort('created_at', -1).to_list(500)


@router.post('/organizations/name-requests/{request_id}/approve')
async def admin_approve_name_request(request_id: str, body: NameRequestApproveIn,
                                     admin=Depends(current_user)):
    req = await _find_or_404(db.org_name_requests, request_id, 'Name-correction request')
    if req.get('status') != 'pending':
        raise HTTPException(400, 'This request has already been actioned')
    org = await db.organizations.find_one({'id': req.get('org_id')}, {'_id': 0})
    final_name = body.finalName.strip()
    if org:
        old_name = org['name']
        await db.organizations.update_one({'id': org['id']}, {'$set': {'name': final_name}})
        # Keep membership consistent: users carry the org NAME string.
        await db.users.update_many({'organization': old_name},
                                   {'$set': {'organization': final_name}})
    await db.org_name_requests.update_one({'id': request_id}, {'$set': {
        'status': 'approved', 'finalName': final_name,
        'actioned_by': admin['full_name'], 'actioned_at': now()}})
    await write_audit(admin, 'admin.org_name_approve',
                      f"Approved org name correction → \"{final_name}\"",
                      target_id=request_id, org_id=req.get('org_id'))
    return {'ok': True, 'id': request_id, 'finalName': final_name}


@router.post('/organizations/name-requests/{request_id}/reject')
async def admin_reject_name_request(request_id: str, body: NameRequestRejectIn,
                                    admin=Depends(current_user)):
    req = await _find_or_404(db.org_name_requests, request_id, 'Name-correction request')
    if req.get('status') != 'pending':
        raise HTTPException(400, 'This request has already been actioned')
    await db.org_name_requests.update_one({'id': request_id}, {'$set': {
        'status': 'rejected', 'rejectReason': body.reason,
        'actioned_by': admin['full_name'], 'actioned_at': now()}})
    await write_audit(admin, 'admin.org_name_reject',
                      f"Rejected org name correction — {body.reason}", target_id=request_id)
    return {'ok': True, 'id': request_id, 'status': 'rejected'}


@router.post('/organizations')
async def admin_create_org(body: OrgCreate, admin=Depends(current_user)):
    name = body.name.strip()
    if await db.organizations.find_one({'name': name}):
        raise HTTPException(400, 'An organization with this name already exists')
    doc = {
        'id': str(uuid.uuid4()), 'name': name, 'type': body.type,
        'address': body.address or '', 'contact': body.contact or '',
        'email': body.email or '', 'website': body.website or '',
        'status': 'active', 'created_at': now(), 'created_by': admin['id'],
    }
    await db.organizations.insert_one(doc)
    await write_audit(admin, 'admin.org_create', f'Created organization "{name}"',
                      target_id=doc['id'])
    return serialize({**doc})


@router.patch('/organizations/{org_id}')
async def admin_patch_org(org_id: str, body: OrgPatch, admin=Depends(current_user)):
    org = await _find_or_404(db.organizations, org_id, 'Organization')
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not updates:
        return org
    new_name = updates.get('name', '').strip() if 'name' in updates else None
    if new_name and new_name != org['name']:
        if await db.organizations.find_one({'name': new_name, 'id': {'$ne': org_id}}):
            raise HTTPException(400, 'An organization with this name already exists')
        updates['name'] = new_name
        await db.users.update_many({'organization': org['name']},
                                   {'$set': {'organization': new_name}})
    await db.organizations.update_one({'id': org_id}, {'$set': updates})
    await write_audit(admin, 'admin.org_update',
                      f"Updated organization \"{org['name']}\" ({', '.join(updates)})",
                      target_id=org_id, changes=updates)
    return await db.organizations.find_one({'id': org_id}, {'_id': 0})


@router.post('/organizations/{org_id}/merge')
async def admin_merge_orgs(org_id: str, body: OrgMergeIn, admin=Depends(current_user)):
    """Merge org {org_id} INTO target_org_id. Irreversible: users and trials are
    repointed to the target; the source is tombstoned as status='merged'."""
    source = await _find_or_404(db.organizations, org_id, 'Organization')
    target = await _find_or_404(db.organizations, body.target_org_id, 'Target organization')
    if source['id'] == target['id']:
        raise HTTPException(400, 'Cannot merge an organization into itself')
    if source.get('status') == 'merged':
        raise HTTPException(400, 'This organization has already been merged')
    moved_users = await db.users.update_many(
        {'organization': source['name']}, {'$set': {'organization': target['name']}})
    moved_trials = await db.trials.update_many(
        {'sponsor_name': source['name']}, {'$set': {'sponsor_name': target['name']}})
    await db.organizations.update_one({'id': source['id']}, {'$set': {
        'status': 'merged', 'merged_into': target['id'], 'merged_at': now(),
        'merge_justification': body.justification}})
    await write_audit(admin, 'admin.org_merge',
                      f"Merged \"{source['name']}\" into \"{target['name']}\" — {body.justification}",
                      target_id=source['id'], merged_into=target['id'],
                      moved_users=moved_users.modified_count,
                      moved_trials=moved_trials.modified_count)
    return {'ok': True, 'merged': source['id'], 'into': target['id'],
            'moved_users': moved_users.modified_count,
            'moved_trials': moved_trials.modified_count}


# ═════════════════════════════════════════════════════════════════════════════
# MASTER DATA ("Others: specify" queue + global values)
# ═════════════════════════════════════════════════════════════════════════════
class MasterDataApproveIn(BaseModel):
    value: Optional[str] = None   # edit-and-approve when provided


class MasterDataRejectIn(BaseModel):
    reason: str = Field(min_length=1)


@router.get('/master-data/submissions')
async def admin_master_data_submissions(status: Optional[str] = None,
                                        fieldType: Optional[str] = None):
    q: Dict = {}
    if status:
        q['status'] = status
    if fieldType:
        q['fieldType'] = fieldType
    return await db.master_data_submissions.find(q, {'_id': 0}) \
        .sort('dateSubmitted', -1).to_list(500)


@router.post('/master-data/submissions/{submission_id}/approve')
async def admin_approve_master_data(submission_id: str, body: MasterDataApproveIn,
                                    admin=Depends(current_user)):
    sub = await _find_or_404(db.master_data_submissions, submission_id, 'Submission')
    if sub.get('status') != 'pending':
        raise HTTPException(400, 'This submission has already been actioned')
    final_value = (body.value or sub['value']).strip()
    await db.master_data_submissions.update_one({'id': submission_id}, {'$set': {
        'status': 'approved', 'value': final_value,
        'actionBy': admin['full_name'], 'actioned_at': now()}})
    await db.master_data_values.update_one(
        {'fieldType': sub['fieldType'], 'value': final_value},
        {'$setOnInsert': {'id': str(uuid.uuid4()), 'added_by': admin['full_name'],
                          'added_at': now(), 'source_submission': submission_id}},
        upsert=True)
    await write_audit(admin, 'admin.master_data_approve',
                      f"Approved {sub['fieldType']} value \"{final_value}\""
                      + (' (edited)' if body.value else ''),
                      target_id=submission_id)
    return {'ok': True, 'id': submission_id, 'status': 'approved', 'value': final_value}


@router.post('/master-data/submissions/{submission_id}/reject')
async def admin_reject_master_data(submission_id: str, body: MasterDataRejectIn,
                                   admin=Depends(current_user)):
    sub = await _find_or_404(db.master_data_submissions, submission_id, 'Submission')
    if sub.get('status') != 'pending':
        raise HTTPException(400, 'This submission has already been actioned')
    await db.master_data_submissions.update_one({'id': submission_id}, {'$set': {
        'status': 'rejected', 'rejectReason': body.reason,
        'actionBy': admin['full_name'], 'actioned_at': now()}})
    await write_audit(admin, 'admin.master_data_reject',
                      f"Rejected {sub['fieldType']} value \"{sub['value']}\" — {body.reason}",
                      target_id=submission_id)
    return {'ok': True, 'id': submission_id, 'status': 'rejected'}


@router.get('/master-data/values')
async def admin_master_data_values(fieldType: Optional[str] = None):
    q: Dict = {}
    if fieldType:
        q['fieldType'] = fieldType
    return await db.master_data_values.find(q, {'_id': 0}).sort('value', 1).to_list(1000)


# ═════════════════════════════════════════════════════════════════════════════
# INVITATIONS (admin-wide — no org restriction, unlike the staff endpoints)
# ═════════════════════════════════════════════════════════════════════════════
class AdminInvitationIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    role: Role = 'patient'
    entityType: Optional[str] = ''
    organization: Optional[str] = ''
    site: Optional[str] = ''
    trial_id: Optional[str] = None


@router.get('/invitations')
async def admin_list_invitations(status: Optional[str] = None):
    rows = await db.invitations.find({}, {'_id': 0}).sort('created_at', -1).to_list(1000)
    out = []
    for inv in rows:
        inv['status'] = _invitation_status(inv)
        if status and inv['status'] != status:
            continue
        out.append(inv)
    return out


@router.post('/invitations')
async def admin_create_invitation(body: AdminInvitationIn, admin=Depends(current_user)):
    if not body.email and not body.phone:
        raise HTTPException(400, 'Email or phone required')
    token = uuid.uuid4().hex
    doc = {
        'id': str(uuid.uuid4()), 'token': token,
        'email': (body.email or '').lower(), 'phone': body.phone or '',
        'full_name': body.full_name or '', 'designation': body.designation or '',
        'role': body.role, 'entityType': body.entityType or '',
        'trial_id': body.trial_id, 'invited_by': admin['id'],
        'org': (body.organization or '').strip(), 'site': (body.site or '').strip(),
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS), 'resend_count': 0,
    }
    await db.invitations.insert_one(doc)
    await write_audit(admin, 'admin.invitation_create',
                      f"Invited {doc['email'] or doc['phone']} as {doc['role']}",
                      target_id=doc['id'])
    return {**serialize(doc), 'invite_link': _invite_link(token)}


@router.post('/invitations/{invitation_id}/resend')
async def admin_resend_invitation(invitation_id: str, admin=Depends(current_user)):
    inv = await _find_or_404(db.invitations, invitation_id, 'Invitation')
    if _invitation_status(inv) not in ('pending', 'expired'):
        raise HTTPException(400, 'Only pending or expired invitations can be resent')
    new_exp = now() + timedelta(days=INVITE_TTL_DAYS)
    await db.invitations.update_one({'id': invitation_id}, {
        '$set': {'status': 'pending', 'expires_at': new_exp, 'last_sent_at': now()},
        '$inc': {'resend_count': 1}})
    await write_audit(admin, 'admin.invitation_resend',
                      f"Resent invitation for {inv.get('email') or inv.get('phone')}",
                      target_id=invitation_id)
    return {'ok': True, 'invite_link': _invite_link(inv['token']), 'expires_at': iso(new_exp)}


@router.post('/invitations/{invitation_id}/cancel')
async def admin_cancel_invitation(invitation_id: str, admin=Depends(current_user)):
    inv = await _find_or_404(db.invitations, invitation_id, 'Invitation')
    if inv.get('status') == 'accepted':
        raise HTTPException(400, 'An accepted invitation cannot be cancelled')
    await db.invitations.update_one({'id': invitation_id},
                                    {'$set': {'status': 'cancelled', 'cancelled_at': now()}})
    await write_audit(admin, 'admin.invitation_cancel',
                      f"Cancelled invitation for {inv.get('email') or inv.get('phone')}",
                      target_id=invitation_id)
    return {'ok': True, 'status': 'cancelled'}


# ═════════════════════════════════════════════════════════════════════════════
# SUPPORT TICKETS (admin triage — the user-side endpoints stay untouched)
# ═════════════════════════════════════════════════════════════════════════════
class TicketNoteIn(BaseModel):
    text: str = Field(min_length=1)


class TicketPatch(BaseModel):
    status: Optional[Literal['Open', 'In Progress', 'Resolved', 'Closed']] = None
    priority: Optional[Literal['low', 'medium', 'high', 'urgent']] = None


async def _enrich_ticket(t: dict) -> dict:
    t = dict(t)
    u = await db.users.find_one({'id': t.get('user_id')}, USER_PROJECTION)
    if u:
        u = _pseudonymize_patient(u)
        t['user'] = {'id': u['id'], 'name': u.get('full_name', ''),
                     'email': u.get('email', ''), 'role': u.get('role', '')}
        t['userType'] = u.get('role', '')
    return t


@router.get('/tickets')
async def admin_list_tickets(status: Optional[str] = None, category: Optional[str] = None,
                             search: Optional[str] = None):
    q: Dict = {}
    if status:
        q['status'] = status
    if category:
        q['category'] = category
    if search:
        rx = {'$regex': search, '$options': 'i'}
        q['$or'] = [{'subject': rx}, {'description': rx}, {'ticket_id': rx}]
    rows = await db.support_tickets.find(q, {'_id': 0}).sort('created_at', -1).to_list(500)
    return [await _enrich_ticket(t) for t in rows]


@router.get('/tickets/{ticket_id}')
async def admin_get_ticket(ticket_id: str):
    t = await db.support_tickets.find_one(
        {'$or': [{'id': ticket_id}, {'ticket_id': ticket_id}]}, {'_id': 0})
    if not t:
        raise HTTPException(404, 'Ticket not found')
    return await _enrich_ticket(t)


@router.post('/tickets/{ticket_id}/notes')
async def admin_add_ticket_note(ticket_id: str, body: TicketNoteIn, admin=Depends(current_user)):
    t = await _find_or_404(db.support_tickets, ticket_id, 'Ticket')
    note = {'by': admin['full_name'], 'by_id': admin['id'], 'at': now(), 'text': body.text}
    await db.support_tickets.update_one({'id': ticket_id}, {'$push': {'notes': note}})
    # The ticket owner sees the response in their notifications.
    if t.get('user_id'):
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': t['user_id'],
            'title': f"Support update · {t.get('ticket_id', ticket_id)}",
            'body': body.text, 'kind': 'support', 'read': False, 'created_at': now()})
    await write_audit(admin, 'admin.ticket_note',
                      f"Added note to {t.get('ticket_id', ticket_id)}", target_id=ticket_id)
    return {'ok': True, 'id': ticket_id, 'note': {**note, 'at': iso(note['at'])}}


@router.patch('/tickets/{ticket_id}')
async def admin_patch_ticket(ticket_id: str, body: TicketPatch, admin=Depends(current_user)):
    t = await _find_or_404(db.support_tickets, ticket_id, 'Ticket')
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return await _enrich_ticket(t)
    updates['updated_at'] = now()
    await db.support_tickets.update_one({'id': ticket_id}, {'$set': updates})
    if 'status' in updates and t.get('user_id'):
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': t['user_id'],
            'title': f"Ticket {updates['status'].lower()} · {t.get('ticket_id', ticket_id)}",
            'body': f"Your support ticket is now {updates['status']}.",
            'kind': 'support', 'read': False, 'created_at': now()})
    await write_audit(admin, 'admin.ticket_update',
                      f"Updated {t.get('ticket_id', ticket_id)} "
                      f"({', '.join(f'{k}={v}' for k, v in updates.items() if k != 'updated_at')})",
                      target_id=ticket_id)
    fresh = await db.support_tickets.find_one({'id': ticket_id}, {'_id': 0})
    return await _enrich_ticket(fresh)


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM ALERTS
# ═════════════════════════════════════════════════════════════════════════════
class AlertResolveIn(BaseModel):
    note: Optional[str] = ''


@router.get('/alerts')
async def admin_list_alerts(status: Optional[str] = None, severity: Optional[str] = None):
    q: Dict = {}
    if status:
        q['status'] = status
    if severity:
        q['severity'] = severity
    return await db.system_alerts.find(q, {'_id': 0}).sort('timestamp', -1).to_list(500)


@router.post('/alerts/{alert_id}/retry')
async def admin_retry_alert(alert_id: str, admin=Depends(current_user)):
    alert = await _find_or_404(db.system_alerts, alert_id, 'Alert')
    await db.system_alerts.update_one({'id': alert_id}, {
        '$set': {'last_retry_at': now()}, '$inc': {'retries': 1}})
    await write_audit(admin, 'admin.alert_retry',
                      f"Retried failed operation for alert: {alert.get('type')}",
                      target_id=alert_id)
    return {'ok': True, 'id': alert_id}


@router.post('/alerts/{alert_id}/notify-user')
async def admin_alert_notify_user(alert_id: str, admin=Depends(current_user)):
    alert = await _find_or_404(db.system_alerts, alert_id, 'Alert')
    affected = (alert.get('affected') or '').strip().lower()
    target = await db.users.find_one({'email': affected}, {'_id': 0, 'id': 1})
    if not target:
        raise HTTPException(404, 'Affected user not found')
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()), 'user_id': target['id'],
        'title': 'Action needed on your account',
        'body': alert.get('description', 'Our team flagged an issue affecting your account.'),
        'kind': 'system', 'read': False, 'created_at': now()})
    await db.system_alerts.update_one({'id': alert_id}, {'$set': {'user_notified_at': now()}})
    await write_audit(admin, 'admin.alert_notify_user',
                      f"Notified {affected} about alert: {alert.get('type')}",
                      target_id=alert_id)
    return {'ok': True, 'id': alert_id, 'notified': affected}


@router.post('/alerts/{alert_id}/escalate')
async def admin_escalate_alert(alert_id: str, admin=Depends(current_user)):
    alert = await _find_or_404(db.system_alerts, alert_id, 'Alert')
    await db.system_alerts.update_one({'id': alert_id}, {'$set': {
        'severity': 'critical', 'escalated': True, 'escalated_at': now(),
        'escalated_by': admin['full_name']}})
    await write_audit(admin, 'admin.alert_escalate',
                      f"Escalated alert: {alert.get('type')}", target_id=alert_id)
    return {'ok': True, 'id': alert_id, 'severity': 'critical'}


@router.post('/alerts/{alert_id}/resolve')
async def admin_resolve_alert(alert_id: str, body: AlertResolveIn, admin=Depends(current_user)):
    alert = await _find_or_404(db.system_alerts, alert_id, 'Alert')
    if alert.get('status') == 'resolved':
        raise HTTPException(400, 'Alert is already resolved')
    await db.system_alerts.update_one({'id': alert_id}, {'$set': {
        'status': 'resolved', 'resolved_at': now(), 'resolved_by': admin['full_name'],
        'resolution_note': body.note or ''}})
    await write_audit(admin, 'admin.alert_resolve',
                      f"Resolved alert: {alert.get('type')}"
                      + (f" — {body.note}" if body.note else ''),
                      target_id=alert_id)
    return {'ok': True, 'id': alert_id, 'status': 'resolved'}


# ═════════════════════════════════════════════════════════════════════════════
# NOTIFICATION MONITORING (delivery log + stats + reminder settings)
# ═════════════════════════════════════════════════════════════════════════════
NOTIF_SETTINGS_KEY = 'notification_settings'
DEFAULT_NOTIF_SETTINGS = {
    'visitReminderHours': 24,
    'medicationReminderMins': 30,
    'channels': {'push': True, 'sms': True, 'email': True},
}


class NotifSettingsPatch(BaseModel):
    visitReminderHours: Optional[int] = Field(None, ge=1, le=168)
    medicationReminderMins: Optional[int] = Field(None, ge=1, le=1440)
    channels: Optional[Dict[str, bool]] = None


def _mask_recipient(rec: str) -> str:
    rec = (rec or '').strip()
    return _mask_email(rec) if '@' in rec else _mask_phone(rec)


@router.get('/notifications/stats')
async def admin_notification_stats():
    total = await db.notification_deliveries.count_documents({})
    by_status: Dict[str, int] = {}
    for st in ('Delivered', 'Failed', 'Pending'):
        by_status[st.lower()] = await db.notification_deliveries.count_documents({'status': st})
    by_channel: Dict[str, int] = {}
    for ch in ('Push', 'SMS', 'Email'):
        by_channel[ch.lower()] = await db.notification_deliveries.count_documents({'channel': ch})
    failures_24h = await db.notification_deliveries.count_documents({
        'status': 'Failed', 'sentAt': {'$gte': now() - timedelta(hours=24)}})
    return {'total': total, 'by_status': by_status, 'by_channel': by_channel,
            'failures_24h': failures_24h}


@router.get('/notifications/log')
async def admin_notification_log(status: Optional[str] = None, channel: Optional[str] = None,
                                 limit: int = Query(200, le=1000)):
    q: Dict = {}
    if status:
        q['status'] = status
    if channel:
        q['channel'] = channel
    rows = await db.notification_deliveries.find(q, {'_id': 0}).sort('sentAt', -1).to_list(limit)
    for r in rows:
        r['recipient'] = _mask_recipient(r.get('recipient', ''))
    return rows


@router.get('/notifications/settings')
async def admin_get_notification_settings():
    doc = await db.app_content.find_one({'key': NOTIF_SETTINGS_KEY}, {'_id': 0, 'key': 0})
    return doc or dict(DEFAULT_NOTIF_SETTINGS)


@router.patch('/notifications/settings')
async def admin_patch_notification_settings(body: NotifSettingsPatch, admin=Depends(current_user)):
    current = await db.app_content.find_one({'key': NOTIF_SETTINGS_KEY}, {'_id': 0, 'key': 0}) \
        or dict(DEFAULT_NOTIF_SETTINGS)
    updates = body.model_dump(exclude_none=True)
    if 'channels' in updates:
        merged = {**current.get('channels', {}), **updates['channels']}
        unknown = set(merged) - set(DEFAULT_NOTIF_SETTINGS['channels'])
        if unknown:
            raise HTTPException(400, f"Unknown channels: {', '.join(sorted(unknown))}")
        updates['channels'] = merged
    merged_doc = {**current, **updates}
    await db.app_content.update_one({'key': NOTIF_SETTINGS_KEY},
                                    {'$set': merged_doc}, upsert=True)
    await write_audit(admin, 'admin.notification_settings',
                      f"Updated notification settings ({', '.join(updates)})",
                      changes=updates)
    return merged_doc


@router.post('/notifications/{delivery_id}/retry')
async def admin_retry_notification(delivery_id: str, admin=Depends(current_user)):
    d = await _find_or_404(db.notification_deliveries, delivery_id, 'Delivery record')
    if d.get('status') != 'Failed':
        raise HTTPException(400, 'Only failed deliveries can be retried')
    await db.notification_deliveries.update_one({'id': delivery_id}, {
        '$set': {'status': 'Pending', 'retried_at': now(), 'error': ''},
        '$inc': {'retries': 1}})
    await write_audit(admin, 'admin.notification_retry',
                      f"Retried {d.get('channel', '')} delivery to "
                      f"{_mask_recipient(d.get('recipient', ''))}",
                      target_id=delivery_id)
    return {'ok': True, 'id': delivery_id, 'status': 'Pending'}


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT LOG (admin scope: unrestricted view + summary + security alerts + export)
# ═════════════════════════════════════════════════════════════════════════════
def _audit_query(category: Optional[str], from_: Optional[str], to: Optional[str],
                 user_id: Optional[str], org: Optional[str], status: Optional[str]) -> Dict:
    f = _parse_ymd(from_, 'from')
    t = _parse_ymd(to, 'to')
    if f is not None and t is not None and t < f:
        raise HTTPException(400, 'to must be on or after from')
    q: Dict = {}
    if category:
        q['category'] = category
    if user_id:
        q['user_id'] = user_id
    if org:
        q['org'] = org
    if status:
        q['status'] = status
    if f is not None or t is not None:
        rng: Dict = {}
        if f is not None:
            rng['$gte'] = datetime(f.year, f.month, f.day, tzinfo=timezone.utc)
        if t is not None:
            rng['$lt'] = datetime(t.year, t.month, t.day, tzinfo=timezone.utc) + timedelta(days=1)
        q['created_at'] = rng
    return q


@router.get('/audit-logs')
async def admin_audit_logs(category: Optional[str] = None,
                           from_: Optional[str] = Query(None, alias='from'),
                           to: Optional[str] = None, user_id: Optional[str] = None,
                           org: Optional[str] = None, status: Optional[str] = None,
                           limit: int = Query(300, le=2000)):
    q = _audit_query(category, from_, to, user_id, org, status)
    return await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1).to_list(limit)


@router.get('/audit-logs/summary')
async def admin_audit_summary():
    total = await db.audit_logs.count_documents({})
    last_24h = await db.audit_logs.count_documents(
        {'created_at': {'$gte': now() - timedelta(hours=24)}})
    failures_24h = await db.audit_logs.count_documents(
        {'status': 'failure', 'created_at': {'$gte': now() - timedelta(hours=24)}})
    by_category = {g['_id'] or 'other': g['n'] async for g in db.audit_logs.aggregate(
        [{'$group': {'_id': '$category', 'n': {'$sum': 1}}}])}
    return {'total': total, 'last_24h': last_24h, 'failures_24h': failures_24h,
            'by_category': by_category}


@router.get('/audit-logs/security-alerts')
async def admin_audit_security_alerts(threshold: int = Query(3, ge=2, le=20)):
    """Failed-login patterns from the audit trail: any (user, ip) with >=
    `threshold` failures in the last 24h is a security signal."""
    since = now() - timedelta(hours=24)
    rows = await db.audit_logs.find(
        {'category': 'login', 'status': 'failure', 'created_at': {'$gte': since}},
        {'_id': 0}).to_list(5000)
    buckets: Dict[tuple, dict] = {}
    for r in rows:
        key = (r.get('user_id') or r.get('user_name') or 'unknown', r.get('ip', ''))
        b = buckets.setdefault(key, {'user_id': r.get('user_id'),
                                     'user_name': r.get('user_name', ''),
                                     'ip': r.get('ip', ''), 'count': 0,
                                     'last_at': r.get('created_at')})
        b['count'] += 1
        if r.get('created_at') and (not b['last_at'] or r['created_at'] > b['last_at']):
            b['last_at'] = r['created_at']
    alerts = [b for b in buckets.values() if b['count'] >= threshold]
    for a in alerts:
        a['last_at'] = iso(a['last_at'])
        a['pattern'] = 'repeated_failed_login'
    return sorted(alerts, key=lambda a: -a['count'])


@router.get('/audit-logs/export')
async def admin_audit_export(category: Optional[str] = None,
                             from_: Optional[str] = Query(None, alias='from'),
                             to: Optional[str] = None, user_id: Optional[str] = None,
                             org: Optional[str] = None, status: Optional[str] = None,
                             admin=Depends(current_user)):
    q = _audit_query(category, from_, to, user_id, org, status)
    rows = await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1).to_list(5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['time', 'user', 'role', 'org', 'category', 'action', 'detail',
                'status', 'ip', 'device'])
    for r in rows:
        w.writerow([iso(r.get('created_at')), r.get('user_name'), r.get('role'),
                    r.get('org'), r.get('category'), r.get('action'), r.get('detail'),
                    r.get('status'), r.get('ip'), r.get('device')])
    await write_audit(admin, 'admin.audit_export', f'Exported {len(rows)} audit rows to CSV')
    return Response(content=buf.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="audit-logs.csv"'})


# ═════════════════════════════════════════════════════════════════════════════
# ADMIN TRIALS (read-only aggregates; subjects masked unless an active BTG
# session — every unmasked read is audited with the session id)
# ═════════════════════════════════════════════════════════════════════════════
async def _active_btg_session(user_id: str) -> Optional[dict]:
    """The caller's newest ACTIVE break-the-glass session, enforcing the 2h TTL:
    an expired session is tombstoned on sight and never grants access."""
    s = await db.emergency_sessions.find_one(
        {'user_id': user_id, 'status': 'active'}, {'_id': 0},
        sort=[('started_at', -1)])
    if not s:
        return None
    if s['expires_at'] <= now():
        await db.emergency_sessions.update_one(
            {'id': s['id']}, {'$set': {'status': 'expired', 'ended_at': s['expires_at']}})
        return None
    return s


async def _trial_aggregates(trial: dict) -> dict:
    tid = trial['id']
    enrolled = await db.patients.count_documents({'trial_id': tid})
    completed = await db.visit_instances.count_documents(
        {'trial_id': tid, 'status': 'completed'})
    upcoming = await db.visit_instances.count_documents(
        {'trial_id': tid, 'status': 'upcoming'})
    missed = await db.visit_instances.count_documents(
        {'trial_id': tid, 'status': 'missed'})
    creator = await db.users.find_one({'id': trial.get('created_by')},
                                      {'_id': 0, 'full_name': 1})
    return {
        'id': tid, 'title': trial.get('title'), 'protocol_id': trial.get('protocol_id'),
        'phase': trial.get('phase'), 'condition': trial.get('condition'),
        'sponsor': trial.get('sponsor_name', ''), 'status': trial.get('status', 'active'),
        'patients': enrolled, 'targetEnrollment': trial.get('target_enrollment'),
        'scheduleVersion': trial.get('schedule_version', 1),
        'schedule_status': trial.get('schedule_status', ''),
        'visits': {'completed': completed, 'upcoming': upcoming, 'missed': missed},
        'lastModified': iso(trial.get('updated_at') or trial.get('created_at')),
        'modifiedBy': (creator or {}).get('full_name', ''),
    }


@router.get('/trials')
async def admin_list_trials():
    """Read-only trial monitoring: metadata + enrollment aggregates ONLY —
    no subject rows, no patient PII."""
    trials = await db.trials.find({}, {'_id': 0}).sort('created_at', -1).to_list(500)
    return [await _trial_aggregates(t) for t in trials]


@router.get('/trials/{trial_id}')
async def admin_get_trial(trial_id: str, admin=Depends(current_user)):
    """Trial detail: aggregates + subject list. Subjects are ALWAYS masked
    (SUBJ-xxx + initials) unless the caller holds an active break-the-glass
    session, in which case identified data is returned AND the read itself is
    written to the audit trail with the session id."""
    trial = await _find_or_404(db.trials, trial_id, 'Trial')
    out = await _trial_aggregates(trial)
    patients = await db.patients.find({'trial_id': trial_id}, {'_id': 0}).to_list(1000)
    session = await _active_btg_session(admin['id'])
    if session:
        out['subjects'] = [{
            'subject': f"SUBJ-{(p.get('id') or '')[-3:]}",
            'full_name': p.get('full_name', ''), 'email': p.get('email', ''),
            'status': p.get('status', ''), 'enrolled_date': p.get('enrolled_date', ''),
        } for p in patients]
        out['unmasked'] = True
        out['btg_session_id'] = session['id']
        await write_audit(admin, 'emergency.read',
                          f"Break-the-glass read of identified subjects for trial "
                          f"{trial.get('protocol_id', trial_id)}",
                          category='emergency', target_id=trial_id,
                          btg_session_id=session['id'], trial_id=trial_id)
    else:
        out['subjects'] = [_masked_subject(p) for p in patients]
        out['unmasked'] = False
    return out
