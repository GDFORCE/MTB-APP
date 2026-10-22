"""Local synthetic P7 journey on the real exported app and running API.

The fixture is seeded through repository ports, not the production extractor.
All handling and approval decisions are clicks in the shipped UI. Never run on
shared databases. Configure local MONGO_URL, DB_NAME=mtb_pdf_ui_* and
UCTSM_MONGO_DB=uctsm_pdf_ui_* matching the running API, then:
    python backend/scripts/p7_ui_journey.py http://127.0.0.1:8767 <evidence-directory>
No tokens, passwords, provider keys or real patient information are exported.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse
import urllib.request
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from app.application.authoring import ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord
from app.application.pilot import build_pilot_schedule
from app.domain.schedule.timing import UnresolvedTiming
from app.domain.schedule.models import ScheduleStatus
from app.persistence.mongodb.client import build_client, get_database
from app.persistence.mongodb.migrate import run_migrations
from app.persistence.mongodb.settings import MongoSettings, require_local
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork


def main():
    base = sys.argv[1].rstrip("/")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("the browser journey requires a local API proxy")
    output = Path(sys.argv[2]); output.mkdir(parents=True, exist_ok=True)
    settings = require_local(MongoSettings.from_env())
    if not settings.database.startswith("uctsm_pdf_ui_") or not os.getenv("DB_NAME", "").startswith("mtb_pdf_ui_"):
        raise ValueError("use the explicitly isolated PDF UI databases")
    suffix = str(time.time_ns())
    organization = f"Synthetic PDF UI {suffix}"
    steps, page_errors = [], []

    def api(method, path, token=None, body=None):
        request = urllib.request.Request(base + "/api" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def register(role, name):
        return api("POST", "/auth/register", body={"email": f"{name}.{suffix}@pdf-ui.example.com",
            "password": "Synthetic#UI123456", "full_name": name, "role": role, "organization": organization})

    admin = register("sponsor", "Admin")
    editor = register("cro", "Editor")
    reviewer = register("cro", "Reviewer")
    for person in (editor, reviewer):
        for capability in ("schedule.correct", "schedule.approve", "schedule.evidence.read"):
            api("POST", "/uctsm/authorization/grants", admin["access_token"], {
                "user_id": person["user"]["id"], "capability": capability,
                "reason": "Isolated synthetic P7 UI verification"})
    client = build_client(settings)
    database = get_database(client, settings)
    run_migrations(database)
    uow = MongoUnitOfWork(client, database)
    org = UUID(editor["user"]["organization_id"])

    def seed(tx):
        trial = TrialRecord(organization_id=org, study_title="Synthetic manual-rule UI fixture",
                            metadata={"synthetic": True, "verification_only": True})
        protocol = ProtocolRecord(organization_id=org, trial_id=trial.id, protocol_number="PDF-P7-SYNTHETIC")
        version = ProtocolVersionRecord(organization_id=org, trial_id=trial.id, protocol_id=protocol.id,
            version_label="Synthetic UI 1", document_name="synthetic-ui.txt", document_uri="private://synthetic-ui.txt",
            document_hash="a" * 64, metadata={"synthetic": True})
        definition = ScheduleDefinitionRecord(organization_id=org, trial_id=trial.id, protocol_id=protocol.id,
            protocol_version_id=version.id, name="Synthetic manual review")
        tx.authoring.add_trial(trial); tx.authoring.add_protocol(protocol)
        tx.authoring.add_protocol_version(version); tx.authoring.add_definition(definition)
        schedule = build_pilot_schedule(version.id, definition.name)
        schedule.events[0].timing = UnresolvedTiming(reason="Synthetic external timing requires manual review")
        schedule.schedule_metadata.status = ScheduleStatus.DRAFT
        tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
        tx.schedules.transition(schedule.schedule_version_id, to_status="IN_REVIEW")
        return trial.id, schedule.schedule_version_id
    trial, version = uow.run(seed)

    def note(page, name):
        filename = f"{len(steps)+1:02d}-{re.sub('[^a-z0-9]+', '-', name.lower())}.png"
        page.screenshot(path=str(output / filename), full_page=True)
        steps.append({"step": name, "screenshot": filename, "text": page.inner_text("body")[:16000]})
        (output / "journey.json").write_text(json.dumps({"synthetic": True, "source": "repository-seeded fixture",
            "trial_id": str(trial), "schedule_version_id": str(version), "steps": steps,
            "page_errors": page_errors, "complete": False}, indent=2), encoding="utf-8")

    with sync_playwright() as playwright:
        def open_as(person):
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1000, "height": 1400})
            context.route("**/*", lambda route: route.continue_()
                if urlparse(route.request.url).hostname in {"localhost", "127.0.0.1"} else route.abort())
            context.add_init_script("localStorage.setItem('access_token', %s); localStorage.setItem('refresh_token', %s);"
                % (json.dumps(person["access_token"]), json.dumps(person["refresh_token"])))
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(f"{base}/clinical/amendment-review?trialId={trial}", wait_until="networkidle")
            page.get_by_text(re.compile(r"^Version 1.*IN_REVIEW")).first.click()
            page.get_by_text("Review the whole schedule", exact=True).wait_for()
            return browser, page

        browser, page = open_as(editor)
        page.get_by_text(re.compile(r"^Cycle 1 Day 1 \(C1D1\)")).first.click()
        assert page.get_by_text("Approve this schedule version", exact=True).count() == 0
        page.get_by_role("radio", name="Request a reviewed manual workflow").click()
        page.get_by_label("Handling reason", exact=True).fill("Synthetic timing cannot safely be automated")
        page.get_by_label("Manual workflow reference", exact=True).fill("Synthetic permitted manual workflow, page 1")
        page.get_by_role("checkbox", name=re.compile("Page 1")).first.click()
        page.get_by_role("checkbox", name="I accept responsibility for this request.").click()
        page.get_by_text("Record handling request", exact=True).click()
        page.get_by_text("Request recorded under your name.", exact=False).wait_for()
        note(page, "Editor requested manual handling; schedule approval blocked")
        browser.close()

        browser, page = open_as(reviewer)
        page.get_by_text(re.compile(r"^Cycle 1 Day 1 \(C1D1\)")).first.click()
        page.get_by_label("Independent review reason", exact=True).fill("Independently checked this synthetic manual rule and its source")
        page.get_by_text("Approve manual handling independently", exact=True).click()
        page.get_by_text("Independent decision recorded.", exact=False).wait_for()
        page.get_by_text("Approve this schedule version", exact=True).wait_for()
        assert "7 days after actual treatment date of Cycle 1 Day 1" in page.inner_text("body")
        assert "Tracks and movement rules" in page.inner_text("body")
        assert "Additional track id rule" not in page.inner_text("body")
        note(page, "Different reviewer approved manual handling; full schedule still unapproved")
        browser.close()

        def approve_table(page):
            page.get_by_label("Whole schedule approval reason", exact=True).fill("Reviewed all synthetic visits, rules and source evidence")
            page.get_by_role("checkbox", name=re.compile("I reviewed the entire table")).click()
            page.get_by_text("Approve this schedule version", exact=True).click()

        browser, page = open_as(editor)
        approve_table(page)
        page.get_by_text("Maker-checker:", exact=False).wait_for()
        note(page, "Material editor denied whole schedule approval")
        browser.close()

        browser, page = open_as(reviewer)
        approve_table(page)
        page.get_by_text("This approved or archived version is read-only.", exact=False).wait_for()
        note(page, "Independent reviewer approved the native schedule")
        page.reload(wait_until="networkidle")
        page.get_by_text(re.compile(r"^Version 1.*APPROVED")).first.click()
        page.get_by_text("Approved. This version is immutable.", exact=True).wait_for()
        note(page, "Reload preserves approved immutable manual schedule")
        browser.close()

    header = uow.read(lambda tx: tx.schedules.get_header(version))
    assert header.status == "APPROVED" and str(header.approved_by) == reviewer["user"]["id"]
    assert str(header.last_material_editor_id) == editor["user"]["id"]
    assert not page_errors, "browser raised a runtime exception"
    report = json.loads((output / "journey.json").read_text(encoding="utf-8"))
    report.update(complete=True, content_hash=header.content_hash)
    (output / "journey.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    client.close()
    print(f"P7 synthetic UI journey passed: {len(steps)} steps; {output}")


if __name__ == "__main__":
    main()
