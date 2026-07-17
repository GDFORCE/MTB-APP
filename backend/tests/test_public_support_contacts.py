"""Public support and exact organization-contact data contracts."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG_NAME = f"CONTACT-{RUN_ID} Hospital"
ORG_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())
STAFF_ID = str(uuid.uuid4())
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


@pytest.fixture(scope="module", autouse=True)
def world():
    async def build():
        await server.db.organizations.insert_one({
            "id": ORG_ID,
            "name": ORG_NAME,
            "type": "site",
            "status": "active",
            "email": "public-office@example.com",
            "contact": "+91-1800-000-000",
            "created_at": server.now(),
        })
        await server.db.users.insert_many([
            {
                "id": STAFF_ID,
                "full_name": "Private Staff Member",
                "email": f"private-{RUN_ID}@example.com",
                "phone": "+91-99999-11111",
                "role": "crc",
                "organization": ORG_NAME,
                "org_admin": False,
                "status": "Active",
                "created_at": server.now(),
            },
            {
                "id": ADMIN_ID,
                "full_name": "Registered Contact Admin",
                "email": f"admin-{RUN_ID}@example.com",
                "phone": "+91-99999-22222",
                "role": "pi",
                "organization": ORG_NAME,
                "org_admin": True,
                "status": "Active",
                "profile": {"designation": "Site Platform Administrator"},
                "created_at": server.now(),
            },
        ])

    run(build())
    yield

    async def clean():
        await server.db.users.delete_many({"id": {"$in": [ADMIN_ID, STAFF_ID]}})
        await server.db.organizations.delete_one({"id": ORG_ID})

    run(clean())
    LOOP.close()


def test_support_contact_is_public_and_uses_config_contract():
    async def flow():
        async with client() as cli:
            response = await cli.get("/api/support/contact")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["name"] == "MTB Platform Support"
        assert payload["email"]
        assert payload["phone"]
        assert payload["hours"]
        assert payload["channels"] == {"email": True, "phone": True}
        assert "key" not in payload

    run(flow())


def test_exact_organization_contact_returns_only_designated_admin():
    async def flow():
        async with client() as cli:
            response = await cli.get(
                f"/api/organizations/{ORG_ID}/platform-contact")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["organization"] == {
            "id": ORG_ID, "name": ORG_NAME, "type": "site"
        }
        contact = payload["platform_contact"]
        assert contact == {
            "name": "Registered Contact Admin",
            "designation": "Site Platform Administrator",
            "email": f"admin-{RUN_ID}@example.com",
            "phone": "+91-99999-22222",
        }
        assert f"private-{RUN_ID}" not in response.text
        assert "hashed_password" not in response.text

    run(flow())


def test_unknown_organization_contact_is_404():
    async def flow():
        async with client() as cli:
            response = await cli.get(
                f"/api/organizations/{uuid.uuid4()}/platform-contact")
        assert response.status_code == 404, response.text

    run(flow())
