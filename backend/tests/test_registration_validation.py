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
