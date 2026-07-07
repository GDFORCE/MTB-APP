"""Foundation tests — Task 1.1.

Covers: write_audit helper, organizations (auto-upsert + public list/search),
notification unread-count / read-all, and the invitation lifecycle
(create -> list -> resolve -> accept / resend / cancel), with audit rows.

These tests run in-process against the FastAPI app via httpx.ASGITransport,
hitting the real (Atlas) database configured in backend/.env. All test data
carries a unique per-run marker (RUN_ID) and is deleted in module teardown.

NOTE: Motor pins its io_loop on first use, so every coroutine here runs on the
single module-level LOOP (never asyncio.run, which would create/close loops).
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402  (loads .env, builds app + db handle)

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_HOSPITAL = f'TESTORG-{RUN_ID} General Hospital'
ORG_PHARMA = f'TESTORG-{RUN_ID} Pharma'

LOOP = asyncio.new_event_loop()

# ids of invitations we create, so teardown can purge their audit rows too
_created_invitation_ids = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None):
    """Create a fresh user via the public register endpoint; return (user, headers)."""
    email = f'test-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
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
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.invitations.delete_many({'email': {'$regex': RUN_ID}})
        await db.notifications.delete_many({'title': {'$regex': RUN_ID}})
        await db.audit_logs.delete_many({'$or': [
            {'user_name': {'$regex': RUN_ID}},
            {'target_id': {'$in': _created_invitation_ids}},
        ]})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_HOSPITAL))


# ── write_audit helper ───────────────────────────────────────────────────────
class TestWriteAudit:
    def test_helper_writes_standard_row(self):
        async def flow():
            user = {'id': f'test-{RUN_ID}-uid', 'full_name': f'Audit Actor {RUN_ID}',
                    'role': 'pi', 'organization': ORG_HOSPITAL}
            aid = await server.write_audit(user, 'visit.patch', 'Updated visit v-1',
                                           target_id='v-1', changes={'status': 'completed'})
            row = await server.db.audit_logs.find_one({'id': aid}, {'_id': 0})
            assert row, 'audit row not written'
            for key in ('id', 'user_id', 'user_name', 'role', 'org', 'action',
                        'category', 'detail', 'ip', 'device', 'status', 'created_at'):
                assert key in row, f'missing audit field {key}'
            assert row['user_id'] == user['id']
            assert row['user_name'] == user['full_name']
            assert row['org'] == ORG_HOSPITAL
            assert row['action'] == 'visit.patch'
            assert row['category'] == 'visit'
            assert row['status'] == 'success'
            assert row['target_id'] == 'v-1'          # extra ctx preserved
            assert row['changes'] == {'status': 'completed'}
        run(flow())

    def test_helper_tolerates_anonymous_actor(self):
        async def flow():
            aid = await server.write_audit(None, 'invitation.accept',
                                           f'anon accept {RUN_ID}', target_id=f'test-{RUN_ID}-anon')
            _created_invitation_ids.append(f'test-{RUN_ID}-anon')
            row = await server.db.audit_logs.find_one({'id': aid}, {'_id': 0})
            assert row and row['user_id'] is None and row['status'] == 'success'
        run(flow())


# ── Organizations ────────────────────────────────────────────────────────────
class TestOrganizations:
    def test_register_autoupserts_org_and_public_search_finds_it(self, pi):
        async def flow():
            async with make_client() as cli:  # NO auth header — endpoint is public
                r = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
            assert r.status_code == 200, r.text
            orgs = r.json()
            names = [o['name'] for o in orgs]
            assert ORG_HOSPITAL in names
            org = next(o for o in orgs if o['name'] == ORG_HOSPITAL)
            assert org['type'] == 'site'         # pi role maps to a site org
            assert org['status'] == 'active'
            assert '_id' not in org
            uuid.UUID(org['id'])                 # uuid4 string id
        run(flow())

    def test_type_filter(self, pi):
        async def flow():
            await _register('sponsor', org=ORG_PHARMA)
            async with make_client() as cli:
                r_all = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
                r_sponsor = await cli.get('/api/organizations',
                                          params={'search': f'TESTORG-{RUN_ID}', 'type': 'sponsor'})
            assert {o['name'] for o in r_all.json()} == {ORG_HOSPITAL, ORG_PHARMA}
            sponsor_orgs = r_sponsor.json()
            assert [o['name'] for o in sponsor_orgs] == [ORG_PHARMA]
            assert sponsor_orgs[0]['type'] == 'sponsor'
        run(flow())

    def test_same_org_name_not_duplicated(self, pi):
        async def flow():
            await _register('crc', org=ORG_HOSPITAL)   # second user, same org string
            async with make_client() as cli:
                r = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
            matches = [o for o in r.json() if o['name'] == ORG_HOSPITAL]
            assert len(matches) == 1
        run(flow())


# ── Notification counts ──────────────────────────────────────────────────────
class TestNotificationCounts:
    def test_unread_count_and_read_all(self):
        async def flow():
            user, headers = await _register('patient')
            # seed two unread notifications directly (no create-notification endpoint)
            for i in range(2):
                await server.db.notifications.insert_one({
                    'id': str(uuid.uuid4()), 'user_id': user['id'],
                    'title': f'TESTNOTIF-{RUN_ID} #{i}', 'body': 'x',
                    'kind': 'reminder', 'read': False, 'created_at': server.now(),
                })
            async with make_client() as cli:
                r = await cli.get('/api/notifications/unread-count', headers=headers)
                assert r.status_code == 200 and r.json() == {'count': 2}, r.text

                r2 = await cli.post('/api/notifications/read-all', headers=headers)
                assert r2.status_code == 200, r2.text

                r3 = await cli.get('/api/notifications/unread-count', headers=headers)
                assert r3.json() == {'count': 0}
            # mutation audited
            row = await server.db.audit_logs.find_one(
                {'user_id': user['id'], 'action': 'notifications.read_all'})
            assert row, 'read-all not audited'
        run(flow())

    def test_unread_count_requires_auth(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/notifications/unread-count')
            assert r.status_code == 401
        run(flow())


# ── Invitation lifecycle ─────────────────────────────────────────────────────
async def _invite(headers, role='crc'):
    async with make_client() as cli:
        r = await cli.post('/api/invitations', headers=headers, json={
            'email': f'test-{RUN_ID}-invitee-{uuid.uuid4().hex[:6]}@example.com',
            'full_name': f'Invitee {RUN_ID}', 'role': role,
        })
    assert r.status_code == 200, r.text
    inv = r.json()
    _created_invitation_ids.append(inv['id'])
    return inv


class TestInvitationLifecycle:
    def test_create_keeps_existing_shape_and_lists_for_own_org(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            # existing response contract intact
            assert inv['status'] == 'pending' and inv['token'] and inv['invite_link']
            assert inv['invited_by'] == pi_user['id']
            assert inv.get('expires_at'), 'lifecycle needs an expiry'
            # own-org list
            async with make_client() as cli:
                r = await cli.get('/api/invitations', headers=headers)
            assert r.status_code == 200
            assert any(i['id'] == inv['id'] for i in r.json())
            # create is audited
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.create', 'target_id': inv['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_public_resolve(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:   # public: no auth
                r = await cli.get(f"/api/invitations/{inv['token']}")
            assert r.status_code == 200, r.text
            j = r.json()
            assert set(j) >= {'org', 'site', 'role', 'inviter', 'email', 'status', 'expires_at'}
            assert j['org'] == ORG_HOSPITAL
            assert j['role'] == 'crc'
            assert j['inviter'] == pi_user['full_name']
            assert j['email'] == inv['email']
            assert j['status'] == 'pending'
            assert j['expires_at']
        run(flow())

    def test_resolve_unknown_token_404(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/invitations/{uuid.uuid4().hex}')
            assert r.status_code == 404
        run(flow())

    def test_accept_then_second_accept_rejected(self, pi):
        _, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:   # public accept
                r = await cli.post(f"/api/invitations/{inv['token']}/accept")
                assert r.status_code == 200, r.text
                assert r.json()['status'] == 'accepted'
                r2 = await cli.get(f"/api/invitations/{inv['token']}")
                assert r2.json()['status'] == 'accepted'
                r3 = await cli.post(f"/api/invitations/{inv['token']}/accept")
                assert r3.status_code == 400
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.accept', 'target_id': inv['id']})
            assert row, 'accept not audited'
        run(flow())

    def test_resend_extends_expiry_and_is_audited(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:
                r = await cli.post(f"/api/invitations/{inv['id']}/resend", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()['expires_at'] >= inv['expires_at']
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.resend', 'target_id': inv['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_cancel_blocks_accept_and_resend(self, pi):
        _, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:
                r = await cli.post(f"/api/invitations/{inv['id']}/cancel", headers=headers)
                assert r.status_code == 200, r.text
                r2 = await cli.get(f"/api/invitations/{inv['token']}")
                assert r2.json()['status'] == 'cancelled'
                r3 = await cli.post(f"/api/invitations/{inv['token']}/accept")
                assert r3.status_code == 400
                r4 = await cli.post(f"/api/invitations/{inv['id']}/resend", headers=headers)
                assert r4.status_code == 400
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.cancel', 'target_id': inv['id']})
            assert row, 'cancel not audited'
        run(flow())

    def test_patient_role_cannot_list_or_cancel(self, pi):
        _, pi_headers = pi
        async def flow():
            inv = await _invite(pi_headers)
            _, patient_headers = await _register('patient')
            async with make_client() as cli:
                r = await cli.get('/api/invitations', headers=patient_headers)
                assert r.status_code == 403
                r2 = await cli.post(f"/api/invitations/{inv['id']}/cancel", headers=patient_headers)
                assert r2.status_code == 403
        run(flow())
