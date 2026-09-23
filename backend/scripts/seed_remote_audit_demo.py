"""Seed a demonstrable Remote Audit: users, an audit, an approved geofence,
facilities, documents, a query and a CAPA.

    python scripts/seed_remote_audit_demo.py

Everything goes through the REAL HTTP API rather than direct database writes.
That is deliberate: a seed that inserts straight into Mongo can create states
the API would never allow — an audit with no participants, a geofence nobody
approved, a document that skipped quarantine — and then the demo shows a system
that cannot actually be reached by using it. Going through the API means if
this script succeeds, a person clicking the same buttons would succeed too.

Uses an in-process ASGI transport, so the API server does NOT need to be
running. It writes to whatever Mongo `.env` points at.

Re-runnable: every run creates a fresh, uniquely-suffixed org and audit rather
than mutating the last one, so a failed demo never leaves you debugging seed
data instead of the feature.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

import server  # noqa: E402

PASSWORD = "Password1!"
RUN = uuid.uuid4().hex[:6]
API = "/api"

# Delhi, near the geocode the geofence demo uses. Anywhere works — this only
# needs to be somewhere a circle geofence can be drawn around.
SITE_LAT, SITE_LON = 28.6139, 77.2090


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://seed", timeout=60.0)


def key() -> str:
    """Every remote-audit mutation requires an Idempotency-Key."""
    return uuid.uuid4().hex


def log(step: str, detail: str = "") -> None:
    # ASCII only: the Windows console is cp1252 and turns an em-dash into a
    # replacement glyph, which looks like a bug during a demo.
    print(f"  {step}{(' - ' + detail) if detail else ''}")


class SeedError(RuntimeError):
    pass


async def post(cli, path, *, headers=None, json=None, idempotent=False, expect=(200, 201, 202)):
    merged = dict(headers or {})
    if idempotent:
        merged["Idempotency-Key"] = key()
    response = await cli.post(f"{API}{path}", headers=merged, json=json)
    if response.status_code not in expect:
        raise SeedError(f"POST {path} -> {response.status_code}: {response.text[:400]}")
    return response.json()


async def register(cli, role: str, org: str, name: str):
    email = f"demo-{role}-{RUN}@example.com"
    payload = await post(cli, "/auth/register", json={
        "email": email, "password": PASSWORD, "full_name": name, "role": role, "organization": org,
    }, expect=(200,))
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}, email


async def make_admin(cli):
    """Platform admins can't self-register (server.py rejects role=admin), so
    this inserts one directly — the same pattern test_admin_api.py uses. It is
    the ONE place this script bypasses the API, and only because the API
    deliberately has no path for it.

    An admin is needed for exactly two things: granting the geofence-approver
    right on a site, and granting the template_admin capability. Both are
    deliberately not inferrable from any role (plan corrections #3 and #8).
    """
    email = f"demo-admin-{RUN}@example.com"
    await server.db.users.insert_one({
        "id": str(uuid.uuid4()), "email": email, "full_name": "Demo Platform Admin",
        "role": "admin", "organization": f"MTB-PLATFORM-{RUN}", "phone": "",
        "hashed_password": server.pwd_ctx.hash(PASSWORD), "security_question": "",
        "security_answer_hash": "", "avatar_initials": "PA", "created_at": server.now(),
        "is_online": False,
    })
    payload = await post(cli, "/auth/login", json={"email": email, "password": PASSWORD},
                         expect=(200,))
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}, email


async def step_up(cli, headers, scope: str) -> str:
    payload = await post(cli, "/remote-audit/step-up", headers=headers,
                         json={"action_scope": scope, "password": PASSWORD}, expect=(200,))
    return payload["token"]


async def seed() -> dict:
    async with client() as cli:
        print("\nSeeding a Remote Audit demo\n" + "=" * 46)

        # ── People ────────────────────────────────────────────────────────
        print("\n[1/8] Accounts")
        sponsor, sponsor_h, sponsor_email = await register(
            cli, "sponsor", f"DEMO-SPONSOR-{RUN}", "Dr Priya Sponsor")
        log("sponsor", sponsor_email)
        site, site_h, site_email = await register(
            cli, "site", f"DEMO-HOSPITAL-{RUN}", "Dr Arun Site")
        log("site", site_email)
        admin, admin_h, admin_email = await make_admin(cli)
        log("platform admin", admin_email)

        # ── The site and its geofence ─────────────────────────────────────
        print("\n[2/8] Site and geofence")
        site_record = await post(cli, "/remote-audit/sites", headers=site_h, json={
            "official_name": f"Demo General Hospital {RUN}",
            "address": {"formatted": "Connaught Place, New Delhi"},
            "timezone": "Asia/Kolkata",
        })
        log("site created", site_record["id"])

        proposal = await post(
            cli, f"/remote-audit/sites/{site_record['id']}/geofence-versions",
            headers=site_h, json={"lat": SITE_LAT, "lon": SITE_LON})
        geofence_version_id = proposal["geofence_version"]["id"]
        log("geofence proposed", f"source={proposal.get('source')}")

        # Geofence approval needs an EXPLICIT per-site grant. It is never
        # inferred from org ownership, because sites belong to the hospital's
        # org, not the sponsor's (plan correction #3).
        await post(cli, f"/remote-audit/sites/{site_record['id']}/approvers",
                   headers=admin_h, json={"user_id": sponsor["id"]})
        log("approver granted", "sponsor may now approve this site's geofence")

        token = await step_up(cli, sponsor_h, "geofence.approve")
        await post(cli, f"/remote-audit/sites/{site_record['id']}/geofence-versions/"
                        f"{geofence_version_id}/approve",
                   headers=sponsor_h, json={"step_up_token": token})
        log("geofence approved", "step-up token consumed")

        # ── The audit ─────────────────────────────────────────────────────
        print("\n[3/8] Audit and participants")
        audit = await post(cli, "/remote-audit/audits", headers=sponsor_h, json={
            "site_id": site_record["id"],
            "site_org_id": site["organization_id"],
            "jurisdiction": "IN",
            "governing_authority": "CDSCO",
        })
        log("audit created", audit["id"])

        # Each side assigns its own participants — the sponsor cannot appoint
        # staff inside the hospital's org, and vice versa.
        await post(cli, f"/remote-audit/audits/{audit['id']}/participants",
                   headers=sponsor_h, idempotent=True,
                   json={"user_id": sponsor["id"], "role_in_audit": "approver"})
        log("sponsor", "approver")
        await post(cli, f"/remote-audit/audits/{audit['id']}/participants",
                   headers=site_h, idempotent=True,
                   json={"user_id": site["id"], "role_in_audit": "capturer"})
        log("site user", "capturer")

        # ── Facilities and equipment ──────────────────────────────────────
        print("\n[4/8] Facilities and equipment")
        facility = await post(cli, f"/remote-audit/audits/{audit['id']}/facilities",
                              headers=site_h, json={
                                  "facility_type": "pharmacy", "name": "Investigational Pharmacy",
                                  "floor": "2", "location_text": "Block B, second floor",
                              })
        log("facility", facility["name"])
        equipment = await post(cli, f"/remote-audit/facilities/{facility['id']}/equipment",
                               headers=site_h, json={
                                   "category": "refrigerator", "quantity": 1,
                                   "serial_number": "FRIDGE-DEMO-001",
                               })
        log("equipment", equipment.get("category", "refrigerator"))

        lab = await post(cli, f"/remote-audit/audits/{audit['id']}/facilities",
                         headers=site_h, json={
                             "facility_type": "laboratory", "name": "Central Lab", "floor": "1",
                         })
        log("facility", lab["name"])

        # ── A template, so scoring is possible ────────────────────────────
        print("\n[5/8] Audit template")
        # template_admin is never implied by any role — it is granted
        # explicitly and the grant is itself audited (plan correction #8).
        await post(cli, "/remote-audit/capability-grants", headers=admin_h, json={
            "user_id": sponsor["id"], "capability": "remote_audit.template_admin",
        })
        log("template_admin granted", "explicitly, never inferred")

        template = await post(cli, "/remote-audit/templates", headers=sponsor_h, json={
            "jurisdiction": "IN",
            "scoring_policy_json": {
                "compliance": {"facilities": 40.0, "equipment": 35.0, "documents": 25.0},
            },
            "requirement_items": [],
        }, expect=(200,))
        activate_token = await step_up(cli, sponsor_h, "template.activate")
        template = await post(cli, f"/remote-audit/templates/{template['id']}/activate",
                              headers=sponsor_h, json={"step_up_token": activate_token},
                              expect=(200,))
        log("template activated", f"v{template.get('version')} (weights total 100%)")

        # ── A document, uploaded and submitted ────────────────────────────
        print("\n[6/8] Document")
        pdf = _make_certificate_pdf()
        document = None
        if pdf is None:
            log("skipped", "PyMuPDF unavailable, so no demo certificate was built")
        else:
            created = await post(cli, f"/remote-audit/audits/{audit['id']}/documents",
                                 headers=site_h, idempotent=True, json={
                                     "document_type": "calibration_certificate",
                                     "file_name": "fridge-calibration.pdf",
                                     "mime_claimed": "application/pdf",
                                     "size_claimed": len(pdf),
                                     "linked_entity_type": "equipment",
                                     "linked_entity_id": equipment["id"],
                                 })
            document = created
            upload = await cli.post(
                f"{API}/remote-audit/documents/{created['id']}/upload-bytes",
                headers=site_h,
                files={"file": ("fridge-calibration.pdf", pdf, "application/pdf")})
            if upload.status_code != 200:
                raise SeedError(f"upload-bytes -> {upload.status_code}: {upload.text[:300]}")
            await post(cli, f"/remote-audit/documents/{created['id']}/submit",
                       headers=site_h, idempotent=True)
            log("document submitted", created["id"])
            log("  ...", "it stays QUARANTINED until the worker picks it up")

        # ── A query and a CAPA, so those screens have content ─────────────
        print("\n[7/8] Query and CAPA")
        query = await post(cli, f"/remote-audit/audits/{audit['id']}/queries",
                           headers=sponsor_h, idempotent=True, json={
                               "title": "Temperature log has a three-day gap",
                               "detail": "The pharmacy fridge log shows no readings for 3-5 March.",
                               "criticality": "major",
                               "linked_entity_type": "equipment",
                               "linked_entity_id": equipment["id"],
                               "due_date": (date.today() + timedelta(days=14)).isoformat(),
                           })
        log("query raised", query["title"])

        await post(cli, f"/remote-audit/queries/{query['id']}/responses",
                   headers=site_h, idempotent=True, json={
                       "body": "The logger lost power during a planned outage. "
                               "Manual readings for those days are attached.",
                   })
        log("site responded", "query is now RESPONDED and awaiting the reviewer")

        capa = await post(cli, f"/remote-audit/audits/{audit['id']}/capas",
                          headers=sponsor_h, idempotent=True, json={
                              "title": "Fit a UPS to the pharmacy fridge logger",
                              "description": "Prevent log gaps during planned power outages.",
                              "owner_user_id": site["id"],
                              "due_date": (date.today() + timedelta(days=30)).isoformat(),
                              "criticality": "major",
                              "query_id": query["id"],
                          })
        log("CAPA created", f"owner={site['full_name']}, due {capa['due_date']}")

        # An overdue one too, so the overdue path is visible in the demo.
        overdue = await post(cli, f"/remote-audit/audits/{audit['id']}/capas",
                             headers=sponsor_h, idempotent=True, json={
                                 "title": "Recalibrate the backup thermometer",
                                 "description": "Calibration lapsed last quarter.",
                                 "owner_user_id": site["id"],
                                 "due_date": (date.today() - timedelta(days=9)).isoformat(),
                                 "criticality": "minor",
                             })
        log("CAPA created", f"deliberately overdue (due {overdue['due_date']})")

        # ── A virtual session ─────────────────────────────────────────────
        print("\n[8/8] Virtual visit")
        session = await post(cli, f"/remote-audit/audits/{audit['id']}/virtual-sessions",
                             headers=sponsor_h, idempotent=True, json={
                                 "scheduled_start_utc":
                                     (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                                 "privacy_mode": "SERVER_NO_RECORDING",
                                 "consent_required": True,
                                 "purpose": "Walkthrough of the pharmacy and central lab.",
                             })
        log("session scheduled", "consent PENDING; nobody can join until it is recorded")

        return {
            "audit_id": audit["id"], "site_id": site_record["id"],
            "sponsor_email": sponsor_email, "site_email": site_email, "admin_email": admin_email,
            "query_id": query["id"], "capa_id": capa["id"],
            "document_id": document["id"] if document else None,
            "session_id": session["id"], "template_id": template["id"],
        }


def _make_certificate_pdf() -> bytes | None:
    """A small, real calibration certificate with a text layer, so the
    extraction path has something genuine to read rather than a blank page."""
    try:
        import fitz
    except ImportError:
        return None
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "CALIBRATION CERTIFICATE",
        "",
        "Certificate No: CAL-2026-00471",
        "Instrument: Pharmacy Refrigerator, serial FRIDGE-DEMO-001",
        "Calibration Date: 2026-03-12",
        "Due Date: 2027-03-12",
        "Calibrated By: Delhi Metrology Services Pvt Ltd",
        "Result: Pass",
    ]
    for i, line in enumerate(lines):
        page.insert_text((60, 80 + i * 22), line, fontsize=12 if i else 16)
    data = doc.tobytes()
    doc.close()
    return data


def main() -> int:
    try:
        result = asyncio.run(seed())
    except SeedError as exc:
        print(f"\nSeeding failed:\n  {exc}\n")
        return 1

    print("\n" + "=" * 46)
    print("Done. Sign in with either account, password: " + PASSWORD)
    print("=" * 46)
    print(f"\n  Sponsor (reviewer/approver) : {result['sponsor_email']}")
    print(f"  Site (capturer)             : {result['site_email']}")
    print(f"  Platform admin              : {result['admin_email']}")
    print(f"\n  Audit id : {result['audit_id']}")
    print(f"  Site id  : {result['site_id']}")

    print("\nWhat to show, in order:")
    print("  1. Sign in as the SPONSOR. The bottom tab is now 'Audits'; the")
    print("     Notifs bell moved into the header, it was not removed.")
    print("  2. Open the audit. Queries shows a thread the site has answered;")
    print("     CAPAs shows one live and one overdue.")
    print("  3. Score & decide: the two axes are separate, blockers render")
    print("     above the score, and signing needs a password step-up.")
    print("  4. Sign in as the SITE user to capture evidence or upload a document.")

    print("\nThe worker must be running for anything to get a verdict:")
    print("    python remote_audit_worker.py")
    print("\nMalware scanning is OFF in this .env")
    print("(REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false), so uploads are processed")
    print("WITHOUT being scanned. They reach the reviewer marked NOT_SCANNED,")
    print("carry a high-severity finding, and are forced to review. Nothing is")
    print("ever reported clean. Before this touches real site data, run ClamAV")
    print("and remove that line - see docs/remote-audit/.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
