"""Registration normalization and fail-closed backend validation."""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

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


def test_phone_normalization_accepts_any_country_calling_code():
    """Registration is open to every country, not only +91."""
    valid = {
        # Bare numbers keep the historical Indian interpretation.
        '9876543210': '+919876543210',
        '+91 98765-43210': '+919876543210',
        '0091 9876543210': '+919876543210',
        '+1 415 555 2671': '+14155552671',
        '+44 7911 123456': '+447911123456',
        # A national trunk "0" is dropped before the calling code is applied.
        '+44 07911 123456': '+447911123456',
        '+971 50 123 4567': '+971501234567',
        '+65 8123 4567': '+6581234567',
        '+81 90 1234 5678': '+819012345678',
        '+27 82 123 4567': '+27821234567',
        # NANP territories carry a four-digit code and a seven-digit local part.
        '+1684 622 1234': '+16846221234',
    }
    for raw, expected in valid.items():
        assert server.normalize_phone(raw) == expected, raw

    for raw in ['12345', '+999 1', '+1', '+1 555 1234', '+44 1']:
        with pytest.raises(HTTPException) as excinfo:
            server.normalize_phone(raw)
        assert excinfo.value.status_code == 400, raw

    assert server.normalize_phone('') is None
    assert server.normalize_phone(None) is None


def test_registration_contact_availability_reports_field_duplicates():
    user_id = str(uuid.uuid4())
    email = f'availability-{RUN_ID}@example.com'
    phone = f'+9198{int(RUN_ID, 16) % 100_000_000:08d}'

    async def flow():
        await server.db.users.insert_one({
            'id': user_id,
            'email': email,
            'phone': phone,
            'full_name': 'Availability Test',
            'role': 'patient',
        })
        try:
            async with make_client() as cli:
                duplicate = await cli.post('/api/auth/register/check-availability', json={
                    'email': email.upper(),
                    'phone': phone,
                })
                available = await cli.post('/api/auth/register/check-availability', json={
                    'email': f'new-{RUN_ID}@example.com',
                    'phone': '+919700000001',
                })
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()['email']['available'] is False
            assert duplicate.json()['phone']['available'] is False
            assert available.status_code == 200, available.text
            assert available.json()['email']['available'] is True
            assert available.json()['phone']['available'] is True
        finally:
            await server.db.users.delete_one({'id': user_id})

    run(flow())


def test_registration_start_accepts_a_foreign_phone_number(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        async with make_client() as cli:
            response = await cli.post('/api/auth/register/start', json={
                'full_name': 'Overseas Patient',
                'role': 'patient',
                'email': f'overseas-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+44 7911 123456',
                'profile': {'dob': '1990-01-01', 'gender': 'Female'},
                'security_questions': [],
            })
        assert response.status_code == 200, response.text
        registration_id = response.json()['registration_id']
        try:
            pending = await server.db.pending_registrations.find_one(
                {'id': registration_id}, {'_id': 0, 'phone': 1})
            assert pending['phone'] == '+447911123456'
        finally:
            await server.db.pending_registrations.delete_one({'id': registration_id})

    run(flow())


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


def test_site_registration_maps_selected_site_role(monkeypatch):
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
                    ('Administrative', 'site'),
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


def test_smo_self_registration_requires_and_stores_hospitals(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        registration_id = None
        async with make_client() as cli:
            missing = await cli.post('/api/auth/register/start', json={
                'full_name': 'SMO User',
                'role': 'smo',
                'email': f'smo-missing-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+919700000011',
                'organization': f'SMO Missing {uuid.uuid4().hex[:8]}',
                'profile': {'designation': 'SMO Manager'},
            })
            valid = await cli.post('/api/auth/register/start', json={
                'full_name': 'SMO Administrative User',
                'role': 'smo',
                'email': f'smo-valid-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+919700000012',
                'organization': f'SMO Valid {uuid.uuid4().hex[:8]}',
                'profile': {
                    'designation': 'SMO Manager',
                    'hospitals': [{
                        'name': 'Apollo Hospitals Mumbai',
                        'address': 'Bandra West, Mumbai',
                        'type': 'private',
                        'role': 'administrative',
                    }],
                },
            })
        assert missing.status_code == 400, missing.text
        assert 'at least one hospital' in missing.json()['detail']
        assert valid.status_code == 200, valid.text
        registration_id = valid.json()['registration_id']
        pending = await server.db.pending_registrations.find_one(
            {'id': registration_id}, {'_id': 0, 'profile': 1})
        assert pending['profile']['hospitals'] == [{
            'name': 'Apollo Hospitals Mumbai',
            'address': 'Bandra West, Mumbai',
            'type': 'Private',
            'role': 'Administrative',
        }]
        await server.db.pending_registrations.delete_one({'id': registration_id})

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
