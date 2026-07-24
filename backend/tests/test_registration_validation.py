"""Registration normalization and fail-closed backend validation."""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


def test_registration_normalizes_email_phone_dob_and_computed_age(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    email = f'TEST-{RUN_ID}@Example.COM'
    local_phone = f"9{int(RUN_ID, 16) % 1_000_000_000:09d}"
    phone = f'+91 {local_phone[:5]}-{local_phone[5:]}'
    server_today = server.now().date()
    dob = server_today.replace(year=server_today.year - 30)

    async def flow():
        async with make_client() as cli:
            response = await cli.post('/api/auth/register/start', json={
                'full_name': '  Patient Example  ',
                'role': 'patient',
                'email': email,
                'phone': phone,
                'profile': {
                    'dob': dob.isoformat(),
                    'age': 999,
                    'gender': 'Female',
                },
                'security_questions': [],
            })
        assert response.status_code == 200, response.text
        registration_id = response.json()['registration_id']
        pending = await server.db.pending_registrations.find_one(
            {'id': registration_id}, {'_id': 0})
        try:
            assert pending['email'] == email.lower()
            assert pending['phone'] == f'+91{local_phone}'
            assert pending['full_name'] == 'Patient Example'
            assert pending['profile']['dob'] == dob.isoformat()
            assert pending['profile']['age'] == 30
        finally:
            await server.db.pending_registrations.delete_one({'id': registration_id})

    run(flow())


def test_registration_rejects_invalid_phone_dob_future_and_age(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    base = {
        'full_name': 'Patient Example',
        'role': 'patient',
        'email': f'test-{RUN_ID}@example.com',
        'phone': '+919876543210',
        'profile': {'dob': '1990-01-01', 'gender': 'Female'},
    }

    async def flow():
        cases = [
            ({**base, 'phone': '12345'}, 'mobile number'),
            ({**base, 'profile': {'dob': '2024-02-30'}}, 'real date'),
            ({
                **base,
                'profile': {'dob': (date.today() + timedelta(days=1)).isoformat()},
            }, 'future'),
            ({**base, 'profile': {'dob': '1800-01-01'}}, 'between 0 and 120'),
            ({**base, 'profile': {}}, 'required'),
        ]
        async with make_client() as cli:
            for payload, message in cases:
                response = await cli.post('/api/auth/register/start', json=payload)
                assert response.status_code == 400, response.text
                assert message in response.json()['detail'].lower()

    run(flow())


def test_site_registration_creates_pi_or_crc_role_from_selected_site_role(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        created = []
        try:
            async with make_client() as cli:
                for index, (selected, expected) in enumerate([
                    ('PI', 'pi'),
                    ('Research Team', 'crc'),
                ]):
                    response = await cli.post('/api/auth/register/start', json={
                        'full_name': f'Site Member {index}',
                        'role': 'site',
                        'email': f'site-{RUN_ID}-{index}@example.com',
                        'phone': f'+91987654{index:04d}',
                        'organization': f'Site {RUN_ID}',
                        'profile': {'role': selected},
                    })
                    assert response.status_code == 200, response.text
                    registration_id = response.json()['registration_id']
                    created.append(registration_id)
                    pending = await server.db.pending_registrations.find_one(
                        {'id': registration_id}, {'_id': 0, 'role': 1})
                    assert pending['role'] == expected
        finally:
            await server.db.pending_registrations.delete_many(
                {'id': {'$in': created}})

    run(flow())


def test_first_registrant_is_org_admin_and_invitee_is_regular_member():
    org_name = f'Ownership {RUN_ID} {uuid.uuid4().hex[:6]}'
    created_user_ids = []

    async def flow():
        organization = None
        try:
            owner = await server._finalize_registration({
                'full_name': 'Organization Owner',
                'role': 'pi',
                'email': f'owner-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '',
                'organization': org_name,
                'hashed_password': server.pwd_ctx.hash('Password1!'),
                'profile': {'designation': 'Principal Investigator'},
                'creates_organization': True,
                'organization_type': 'site',
                'email_verified': True,
                'phone_verified': False,
            })
            created_user_ids.append(owner['user']['id'])
            assert owner['user']['org_admin'] is True

            organization = await server.find_organization_by_name(org_name)
            assert organization and organization['type'] == 'site'

            member = await server._finalize_registration({
                'full_name': 'Invited CRC',
                'role': 'crc',
                'email': f'member-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '',
                'organization': org_name,
                'hashed_password': server.pwd_ctx.hash('Password1!'),
                'profile': {'designation': 'CRC'},
                'invitation_id': str(uuid.uuid4()),
                'email_verified': True,
                'phone_verified': False,
            })
            created_user_ids.append(member['user']['id'])
            assert member['user']['org_admin'] is False
        finally:
            await server.db.users.delete_many({'id': {'$in': created_user_ids}})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.audit_logs.delete_many(
                    {'target_id': organization['id']})

    run(flow())


def test_normal_registration_rejects_existing_organization(monkeypatch):
    org_name = f'Existing {RUN_ID} {uuid.uuid4().hex[:6]}'

    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        organization = None
        try:
            organization, created = await server.ensure_organization(org_name, 'site')
            assert created is True
            phone = f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Uninvited Member',
                    'role': 'pi',
                    'email': f'uninvited-{uuid.uuid4().hex[:8]}@example.com',
                    'phone': phone,
                    'organization': org_name,
                    'profile': {'designation': 'PI'},
                })
            assert response.status_code == 409, response.text
            assert 'invite' in response.json()['detail'].lower()
        finally:
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.audit_logs.delete_many(
                    {'target_id': organization['id']})

    run(flow())
