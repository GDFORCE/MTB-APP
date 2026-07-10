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
