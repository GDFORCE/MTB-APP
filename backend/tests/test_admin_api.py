"""Platform-admin API — Task 6.1 (SECURITY).

Covers the /api/admin surface: role guard (every group 403s non-admins),
user suspend/unlock/reset/force-logout, org create/patch/merge/name-requests,
master-data approve/edit-approve/reject, admin invitations, and (later groups)
tickets, alerts, notification monitoring, audit views, reports, delegations,
break-the-glass emergency access, broadcasts, and admin trial reads.

Same harness as test_authz_scoping.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, a single module-level event loop (Motor pins
its io_loop on first use — never asyncio.run here), module teardown cleanup.

Admins cannot self-register (POST /auth/register rejects role=admin), so the
admin actors are inserted directly into the users collection and then logged
in through the real /api/auth/login endpoint.
"""
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'

LOOP = asyncio.new_event_loop()

_org_ids = []
_extra_cleanup_ids = {'name_requests': [], 'submissions': [], 'trials': []}


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _login(email):
    async with make_client() as cli:
        r = await cli.post('/api/auth/login', json={'email': email, 'password': PASSWORD})
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


async def _make_admin(tag='a'):
    """Admins cannot self-register — insert directly, then real login."""
    email = f'adm-{RUN_ID}-{tag}-{uuid.uuid4().hex[:4]}@example.com'
    doc = {
        'id': str(uuid.uuid4()), 'email': email,
        'full_name': f'Adm {tag.upper()} {RUN_ID}', 'role': 'admin',
        'organization': 'MTB Health Technologies', 'phone': '+91 90000 00000',
        'hashed_password': server.pwd_ctx.hash(PASSWORD),
        'security_question': '', 'security_answer_hash': '',
        'avatar_initials': 'AD', 'created_at': server.now(), 'is_online': False,
    }
    await server.db.users.insert_one(doc)
    return await _login(email)


async def _register(role, org=None):
    email = f'adm-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': f'Test {role.upper()} {RUN_ID}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'{RUN_ID}'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.invitations.delete_many({'email': {'$regex': RUN_ID}})
        await db.org_name_requests.delete_many({'id': {'$in': _extra_cleanup_ids['name_requests']}})
        await db.master_data_submissions.delete_many({'value': {'$regex': RUN_ID}})
        await db.master_data_values.delete_many({'value': {'$regex': RUN_ID}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
        await db.support_tickets.delete_many({'subject': {'$regex': RUN_ID}})
        await db.system_alerts.delete_many({'description': {'$regex': RUN_ID}})
        await db.notification_deliveries.delete_many({'run_id': RUN_ID})
        await db.admin_delegations.delete_many({'reason': {'$regex': RUN_ID}})
        await db.emergency_requests.delete_many({'reason_text': {'$regex': RUN_ID}})
        await db.emergency_sessions.delete_many({'run_id': RUN_ID})
        await db.broadcast_messages.delete_many({'subject': {'$regex': RUN_ID}})
        await db.broadcast_replies.delete_many({'text': {'$regex': RUN_ID}})
        await db.notifications.delete_many({'title': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _extra_cleanup_ids['trials']}})
        await db.visits.delete_many({'trial_id': {'$in': _extra_cleanup_ids['trials']}})
        await db.patients.delete_many({'email': {'$regex': RUN_ID}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _extra_cleanup_ids['trials']}})
        # reports + their stored CSV blobs
        reports = await db.admin_reports.find({'run_id': RUN_ID}, {'_id': 0}).to_list(50)
        try:
            import storage as storage_mod
            st = storage_mod.get_storage()
            for rep in reports:
                await st.delete(rep['key'])
        except Exception:
            pass
        await db.admin_reports.delete_many({'run_id': RUN_ID})
        # restore the seeded active terms versions if the test superseded them
        await db.terms_versions.delete_many({'version': {'$regex': f'-{RUN_ID}$'}})
        for doc_type in ('ToS', 'Privacy'):
            newest_active = await db.terms_versions.find_one(
                {'type': doc_type, 'status': 'active'})
            if not newest_active:
                await db.terms_versions.update_one(
                    {'type': doc_type, 'version': '1.0'},
                    {'$set': {'status': 'active'}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def actors():
    async def build():
        admin1, admin1_h = await _make_admin('one')
        admin2, admin2_h = await _make_admin('two')
        pi, pi_h = await _register('pi', org=f'ADMORG-{RUN_ID} Hospital')
        patient, patient_h = await _register('patient')
        return {
            'admin1': (admin1, admin1_h), 'admin2': (admin2, admin2_h),
            'pi': (pi, pi_h), 'patient': (patient, patient_h),
        }
    return run(build())


# ── Role guard: EVERY admin group 403s a non-admin ───────────────────────────
GUARDED_GET_PATHS = [
    '/api/admin/users',
    '/api/admin/users/export',
    '/api/admin/organizations',
    '/api/admin/organizations/duplicates',
    '/api/admin/organizations/name-requests',
    '/api/admin/master-data/submissions',
    '/api/admin/master-data/values',
    '/api/admin/invitations',
]


class TestAdminRoleGuard:
    def test_non_admin_gets_403_everywhere(self, actors):
        async def flow():
            async with make_client() as cli:
                for path in GUARDED_GET_PATHS:
                    for _, headers in (actors['pi'], actors['patient']):
                        r = await cli.get(path, headers=headers)
                        assert r.status_code == 403, f'{path}: {r.status_code} {r.text}'
        run(flow())

    def test_unauthenticated_gets_401(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/admin/users')
                assert r.status_code == 401, r.text
        run(flow())

    def test_admin_passes(self, actors):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/admin/users', headers=actors['admin1'][1])
                assert r.status_code == 200, r.text
                assert isinstance(r.json(), list)
        run(flow())


# ── Users ────────────────────────────────────────────────────────────────────
class TestAdminUsers:
    def test_list_masks_patient_pii(self, actors):
        patient = actors['patient'][0]
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/admin/users',
                                  headers=actors['admin1'][1],
                                  params={'search': patient['email']})
                assert r.status_code == 200, r.text
                rows = [u for u in r.json() if u['id'] == patient['id']]
                assert rows, 'patient not found in admin user list'
                masked = rows[0]
                assert masked['full_name'] != patient['full_name']
                assert '***' in masked['full_name']
                assert masked['email'] != patient['email']
                assert patient['full_name'] not in str(masked)
        run(flow())

    def test_create_user_with_invite(self, actors):
        async def flow():
            async with make_client() as cli:
                email = f'adm-{RUN_ID}-created@example.com'
                r = await cli.post('/api/admin/users', headers=actors['admin1'][1], json={
                    'email': email, 'full_name': f'Created User {RUN_ID}',
                    'role': 'crc', 'organization': f'ADMORG-{RUN_ID} Hospital'})
                assert r.status_code == 200, r.text
                j = r.json()
                assert j['user']['status'] == 'Pending Verification'
                assert j['invitation'] and j['invitation']['invite_link']
                assert j['temp_password']
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.user_create', 'target_id': j['user']['id']})
                assert audit, 'user creation must be audited'
        run(flow())

    def test_suspend_blocks_session_and_login_then_activate(self, actors):
        async def flow():
            async with make_client() as cli:
                victim, victim_h = await _register('crc', org=f'ADMORG-{RUN_ID} Hospital')
                r = await cli.patch(f"/api/admin/users/{victim['id']}/status",
                                    headers=actors['admin1'][1],
                                    json={'status': 'Suspended', 'reason': 'policy violation'})
                assert r.status_code == 200, r.text
                # existing session is dead (403 suspended)
                r2 = await cli.get('/api/auth/me', headers=victim_h)
                assert r2.status_code == 403, r2.text
                # and a fresh login is refused
                r3 = await cli.post('/api/auth/login', json={
                    'email': victim['email'], 'password': PASSWORD})
                assert r3.status_code == 403, r3.text
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.user_status', 'target_id': victim['id']})
                assert audit, 'status change must be audited'
                # re-activate → login works again
                r4 = await cli.patch(f"/api/admin/users/{victim['id']}/status",
                                     headers=actors['admin1'][1], json={'status': 'Active'})
                assert r4.status_code == 200, r4.text
                r5 = await cli.post('/api/auth/login', json={
                    'email': victim['email'], 'password': PASSWORD})
                assert r5.status_code == 200, r5.text
        run(flow())

    def test_unlock_validations_and_flow(self, actors):
        async def flow():
            async with make_client() as cli:
                victim, _ = await _register('crc', org=f'ADMORG-{RUN_ID} Hospital')
                await server.db.users.update_one({'id': victim['id']}, {'$set': {
                    'status': 'Locked',
                    'lock_info': {'lockedAt': server.now().isoformat(),
                                  'failedAttempts': 5, 'lastIp': '1.2.3.4'}}})
                h = actors['admin1'][1]
                # <2 identity checks → rejected
                r1 = await cli.post(f"/api/admin/users/{victim['id']}/unlock", headers=h,
                                    json={'identity_checks': ['email'],
                                          'reason': 'verified with the user on a call'})
                assert r1.status_code in (400, 422), r1.text
                # reason too short → rejected
                r2 = await cli.post(f"/api/admin/users/{victim['id']}/unlock", headers=h,
                                    json={'identity_checks': ['email', 'phone'],
                                          'reason': 'short'})
                assert r2.status_code == 422, r2.text
                # valid → unlocked
                r3 = await cli.post(f"/api/admin/users/{victim['id']}/unlock", headers=h,
                                    json={'identity_checks': ['email', 'phone'],
                                          'reason': 'verified identity on a recorded call',
                                          'force_password_reset': True})
                assert r3.status_code == 200, r3.text
                fresh = await server.db.users.find_one({'id': victim['id']}, {'_id': 0})
                assert fresh.get('status') == 'Active'
                assert 'lock_info' not in fresh
                assert fresh.get('must_reset_password') is True
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.user_unlock', 'target_id': victim['id']})
                assert audit and audit.get('identity_checks') == ['email', 'phone']
        run(flow())

    def test_reset_password_and_force_logout(self, actors):
        async def flow():
            async with make_client() as cli:
                victim, victim_h = await _register('crc', org=f'ADMORG-{RUN_ID} Hospital')
                h = actors['admin1'][1]
                r = await cli.post(f"/api/admin/users/{victim['id']}/reset-password", headers=h)
                assert r.status_code == 200, r.text
                temp = r.json()['temp_password']
                # old password no longer works; temp password does
                bad = await cli.post('/api/auth/login', json={
                    'email': victim['email'], 'password': PASSWORD})
                assert bad.status_code == 401, bad.text
                good = await cli.post('/api/auth/login', json={
                    'email': victim['email'], 'password': temp})
                assert good.status_code == 200, good.text
                fresh_h = {'Authorization': f"Bearer {good.json()['access_token']}"}
                # force-logout invalidates the fresh session token
                r2 = await cli.post(f"/api/admin/users/{victim['id']}/force-logout", headers=h)
                assert r2.status_code == 200, r2.text
                dead = await cli.get('/api/auth/me', headers=fresh_h)
                assert dead.status_code == 401, dead.text
        run(flow())

    def test_export_csv(self, actors):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/admin/users/export', headers=actors['admin1'][1])
                assert r.status_code == 200, r.text
                assert 'text/csv' in r.headers['content-type']
                assert r.text.splitlines()[0].startswith('id,name,email')
        run(flow())


# ── Organizations ────────────────────────────────────────────────────────────
class TestAdminOrgs:
    def _mk_org(self, cli_headers, name, otype='site'):
        async def create():
            async with make_client() as cli:
                r = await cli.post('/api/admin/organizations', headers=cli_headers,
                                   json={'name': name, 'type': otype,
                                         'address': 'Test Lane', 'contact': '+91 1'})
                assert r.status_code == 200, r.text
                org = r.json()
                _org_ids.append(org['id'])
                return org
        return run(create())

    def test_create_patch_and_counts(self, actors):
        h = actors['admin1'][1]
        org = self._mk_org(h, f'ADMORG-{RUN_ID} Alpha Clinic')
        async def flow():
            async with make_client() as cli:
                r = await cli.patch(f"/api/admin/organizations/{org['id']}", headers=h,
                                    json={'address': 'New Address 42', 'status': 'suspended'})
                assert r.status_code == 200, r.text
                assert r.json()['address'] == 'New Address 42'
                assert r.json()['status'] == 'suspended'
                lst = await cli.get('/api/admin/organizations', headers=h,
                                    params={'search': f'ADMORG-{RUN_ID} Alpha'})
                assert lst.status_code == 200
                row = [o for o in lst.json() if o['id'] == org['id']][0]
                assert 'users' in row and 'trials' in row
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.org_update', 'target_id': org['id']})
                assert audit, 'org update must be audited'
        run(flow())

    def test_merge_moves_users_and_tombstones_source(self, actors):
        h = actors['admin1'][1]
        source = self._mk_org(h, f'ADMORG-{RUN_ID} Merge Source')
        target = self._mk_org(h, f'ADMORG-{RUN_ID} Merge Target')
        async def flow():
            member, _ = await _register('crc', org=source['name'])
            async with make_client() as cli:
                # justification is mandatory (min 10 chars)
                bad = await cli.post(f"/api/admin/organizations/{source['id']}/merge",
                                     headers=h, json={'target_org_id': target['id'],
                                                      'justification': 'dup'})
                assert bad.status_code == 422, bad.text
                r = await cli.post(f"/api/admin/organizations/{source['id']}/merge",
                                   headers=h, json={
                                       'target_org_id': target['id'],
                                       'justification': 'Duplicate entry for the same hospital'})
                assert r.status_code == 200, r.text
                assert r.json()['moved_users'] >= 1
                moved = await server.db.users.find_one({'id': member['id']}, {'_id': 0})
                assert moved['organization'] == target['name']
                src = await server.db.organizations.find_one({'id': source['id']}, {'_id': 0})
                assert src['status'] == 'merged' and src['merged_into'] == target['id']
                # irreversible: merging again is refused
                again = await cli.post(f"/api/admin/organizations/{source['id']}/merge",
                                       headers=h, json={
                                           'target_org_id': target['id'],
                                           'justification': 'attempting a double merge'})
                assert again.status_code == 400, again.text
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.org_merge', 'target_id': source['id']})
                assert audit, 'merge must be audited'
        run(flow())

    def test_duplicates_detects_normalized_collisions(self, actors):
        h = actors['admin1'][1]
        self._mk_org(h, f'ADMORG-{RUN_ID} Dup Hospital')
        self._mk_org(h, f'admorg-{RUN_ID} dup  hospital')
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/admin/organizations/duplicates', headers=h)
                assert r.status_code == 200, r.text
                names = [o['name'] for g in r.json() for o in g['organizations']]
                assert f'ADMORG-{RUN_ID} Dup Hospital' in names
        run(flow())

    def test_name_request_approve_and_reject(self, actors):
        h = actors['admin1'][1]
        org = self._mk_org(h, f'ADMORG-{RUN_ID} Misspeled Hospitl')
        async def flow():
            db = server.db
            rid1, rid2 = str(uuid.uuid4()), str(uuid.uuid4())
            _extra_cleanup_ids['name_requests'] += [rid1, rid2]
            for rid in (rid1, rid2):
                await db.org_name_requests.insert_one({
                    'id': rid, 'org_id': org['id'], 'current_name': org['name'],
                    'requested_name': f'ADMORG-{RUN_ID} Corrected Hospital',
                    'requested_by': 'someone', 'status': 'pending',
                    'created_at': server.now()})
            async with make_client() as cli:
                final = f'ADMORG-{RUN_ID} Corrected Hospital'
                r = await cli.post(f'/api/admin/organizations/name-requests/{rid1}/approve',
                                   headers=h, json={'finalName': final})
                assert r.status_code == 200, r.text
                fresh = await db.organizations.find_one({'id': org['id']}, {'_id': 0})
                assert fresh['name'] == final
                r2 = await cli.post(f'/api/admin/organizations/name-requests/{rid2}/reject',
                                    headers=h, json={'reason': 'Name already corrected'})
                assert r2.status_code == 200, r2.text
                req2 = await db.org_name_requests.find_one({'id': rid2}, {'_id': 0})
                assert req2['status'] == 'rejected'
        run(flow())


# ── Master data ──────────────────────────────────────────────────────────────
class TestAdminMasterData:
    async def _mk_submission(self, value):
        sid = str(uuid.uuid4())
        _extra_cleanup_ids['submissions'].append(sid)
        await server.db.master_data_submissions.insert_one({
            'id': sid, 'fieldType': 'designation', 'value': value,
            'submittedBy': f'Test {RUN_ID}', 'org': f'ADMORG-{RUN_ID} Hospital',
            'dateSubmitted': server.now(), 'status': 'pending',
            'actionBy': None, 'rejectReason': ''})
        return sid

    def test_approve_adds_global_value(self, actors):
        h = actors['admin1'][1]
        async def flow():
            sid = await self._mk_submission(f'Trial Coordinator {RUN_ID}')
            async with make_client() as cli:
                r = await cli.post(f'/api/admin/master-data/submissions/{sid}/approve',
                                   headers=h, json={})
                assert r.status_code == 200, r.text
                vals = await cli.get('/api/admin/master-data/values', headers=h,
                                     params={'fieldType': 'designation'})
                assert any(v['value'] == f'Trial Coordinator {RUN_ID}' for v in vals.json())
                # double-approve refused
                again = await cli.post(f'/api/admin/master-data/submissions/{sid}/approve',
                                       headers=h, json={})
                assert again.status_code == 400, again.text
                audit = await server.db.audit_logs.find_one(
                    {'action': 'admin.master_data_approve', 'target_id': sid})
                assert audit, 'approve must be audited'
        run(flow())

    def test_edit_and_approve_uses_corrected_value(self, actors):
        h = actors['admin1'][1]
        async def flow():
            sid = await self._mk_submission(f'reserch fellow {RUN_ID}')
            corrected = f'Research Fellow {RUN_ID}'
            async with make_client() as cli:
                r = await cli.post(f'/api/admin/master-data/submissions/{sid}/approve',
                                   headers=h, json={'value': corrected})
                assert r.status_code == 200, r.text
                assert r.json()['value'] == corrected
                sub = await server.db.master_data_submissions.find_one({'id': sid}, {'_id': 0})
                assert sub['value'] == corrected and sub['status'] == 'approved'
        run(flow())

    def test_reject_requires_reason(self, actors):
        h = actors['admin1'][1]
        async def flow():
            sid = await self._mk_submission(f'Wellness Guru {RUN_ID}')
            async with make_client() as cli:
                bad = await cli.post(f'/api/admin/master-data/submissions/{sid}/reject',
                                     headers=h, json={'reason': ''})
                assert bad.status_code == 422, bad.text
                r = await cli.post(f'/api/admin/master-data/submissions/{sid}/reject',
                                   headers=h, json={'reason': 'Not a clinical designation'})
                assert r.status_code == 200, r.text
                sub = await server.db.master_data_submissions.find_one({'id': sid}, {'_id': 0})
                assert sub['status'] == 'rejected'
                assert sub['rejectReason'] == 'Not a clinical designation'
        run(flow())


# ── Admin invitations ────────────────────────────────────────────────────────
class TestAdminInvitations:
    def test_create_resend_cancel(self, actors):
        h = actors['admin1'][1]
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/admin/invitations', headers=h, json={
                    'email': f'adm-{RUN_ID}-invitee@example.com',
                    'full_name': f'Invitee {RUN_ID}', 'role': 'crc',
                    'organization': f'ADMORG-{RUN_ID} Hospital'})
                assert r.status_code == 200, r.text
                inv = r.json()
                assert inv['invite_link']
                lst = await cli.get('/api/admin/invitations', headers=h)
                assert any(i['id'] == inv['id'] for i in lst.json())
                r2 = await cli.post(f"/api/admin/invitations/{inv['id']}/resend", headers=h)
                assert r2.status_code == 200, r2.text
                r3 = await cli.post(f"/api/admin/invitations/{inv['id']}/cancel", headers=h)
                assert r3.status_code == 200, r3.text
                fresh = await server.db.invitations.find_one({'id': inv['id']}, {'_id': 0})
                assert fresh['status'] == 'cancelled'
                for action in ('admin.invitation_create', 'admin.invitation_resend',
                               'admin.invitation_cancel'):
                    audit = await server.db.audit_logs.find_one(
                        {'action': action, 'target_id': inv['id']})
                    assert audit, f'{action} must be audited'
        run(flow())
