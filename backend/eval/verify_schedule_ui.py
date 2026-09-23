"""Check the running Expo web UI with captured synthetic data and mocked API I/O.

Run from backend: python eval/verify_schedule_ui.py
Requires Python Playwright and Edge (or its installed Chromium). No real user,
trial, or approval records are created. Screenshots go to docs/audits/ui-review.
"""
from __future__ import annotations

import json
from copy import deepcopy
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.domain.schedule.models import UniversalSchedule
from app.domain.schedule.projection import project_schedule


def main():
    deadline = time.monotonic() + 55
    while True:
        try:
            with urlopen("http://127.0.0.1:8081/status", timeout=2) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Start Expo on port 8081 before running the UI check.")
        time.sleep(0.5)
    captured = json.loads((ROOT / "docs/audits/uctsm-live-smoke.json").read_text())[0]
    schedule = captured["schedule"]
    schedule["schedule_metadata"]["status"] = "DRAFT"
    # Both groups must be legible when a visit expands. This activity is a UI
    # fixture addition, not a claim about the captured protocol's extraction.
    from uuid import uuid4
    schedule["events"][0]["activities"].append({
        "id": str(uuid4()), "display_name": "Informed consent", "activity_type": "ADMINISTRATIVE",
        "code": "CONSENT", "protocol_label": "Informed consent", "requiredness": "REQUIRED",
    })
    projections = [item.model_dump(mode="json") for item in project_schedule(UniversalSchedule.model_validate(schedule))]
    version = schedule["schedule_version_id"]
    output = ROOT / "docs/audits/ui-review"
    output.mkdir(exist_ok=True)
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        context.add_init_script("localStorage.setItem('access_token', 'isolated-ui-fixture');")

        def api(route):
            path = urlparse(route.request.url).path
            if route.request.method == "OPTIONS":
                route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
                return
            requests.append({"method": route.request.method, "path": path})
            if path.endswith("/auth/me"):
                data = {"id": "ui-fixture", "full_name": "Schedule Reviewer", "role": "sponsor", "email": "fixture@example.invalid"}
            elif path.endswith("/projection"):
                data = projections
            elif path.endswith("/approve-table"):
                assert route.request.post_data_json["reason"] == "Checked all visits and source notes."
                schedule["schedule_metadata"]["status"] = "APPROVED"
                data = {"status": "APPROVED"}
            elif "/protocols/lookup/" in path:
                data = {"found": False}
            elif path.endswith("/protocols/extract"):
                data = {"details": {"title": "Synthetic UI trial", "phase": "Phase 2", "indications": ["Test indication"]},
                    "extractions": [{"extraction_id": "ui-preview", "preview": {
                        "schedule": schedule, "projections": projections, "issues": []}}]}
            elif path.endswith(version):
                data = schedule
            else:
                data = []
            route.fulfill(json=data, headers={"Access-Control-Allow-Origin": "*"})

        context.route("**/api/**", api)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://localhost:8081/sponsor/protocol-schedule?scheduleVersionId={version}", timeout=120000, wait_until="domcontentloaded")
        print("Opened schedule route", flush=True)
        try:
            expect(page.get_by_text("Tap a visit for activities, notes and source details.", exact=True)).to_be_visible(timeout=60000)
        except Exception:
            page.screenshot(path=str(output / "load-failure.png"), full_page=True)
            print(json.dumps({"url": page.url, "body": page.locator("body").inner_text()[:5000], "errors": errors, "requests": requests}), flush=True)
            raise
        expect(page.get_by_text("Confirm table and approve", exact=True)).to_have_count(0)
        page.screenshot(path=str(output / "phone-table.png"), full_page=True)
        first = page.get_by_test_id(f"schedule-visit-{schedule['events'][0]['id']}")
        first.click()
        details = page.get_by_test_id(f"schedule-details-{schedule['events'][0]['id']}")
        expect(details.get_by_text("Clinical", exact=True)).to_be_visible()
        expect(details.get_by_text("Administrative", exact=True)).to_be_visible()
        expect(details.get_by_text("Informed consent", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "phone-activities.png"), full_page=True)
        details.get_by_text("View protocol source", exact=True).click()
        expect(page.get_by_text("Protocol evidence", exact=True)).to_be_visible()
        page.get_by_text("Close", exact=True).click()
        first.click()
        page.get_by_text("Review and approve", exact=True).click()
        confirm = page.get_by_role("button", name="Confirm table and approve", exact=True)
        expect(confirm).to_be_disabled()
        page.get_by_placeholder("Add a short note about your review").fill("Checked all visits and source notes.")
        expect(confirm).to_be_enabled()
        bounds = confirm.bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390, bounds
        page.screenshot(path=str(output / "phone-approval.png"), full_page=True)
        confirm.click()
        expect(page.get_by_text("Schedule approved. It can now be used to calculate patient visits.", exact=True)).to_be_visible()
        expect(page.get_by_text("Review and approve", exact=True)).to_have_count(0)
        assert len([item for item in requests if item["path"].endswith("/approve-table")]) == 1
        # An uncompiled variable cadence must stay readable and block approval.
        original_timing = deepcopy(schedule["events"][0]["timing"])
        schedule["schedule_metadata"]["status"] = "DRAFT"
        wording = "Every 1-2 weeks for 4 weeks, then every 4-8 weeks until 6 months."
        schedule["events"][0]["timing"] = {"type": "UNRESOLVED", "reason": "Variable cadence needs clarification",
            "source_reference": {"statements": [{"text": wording, "page": 19}]}}
        schedule["validation_issues"] = [{"id": str(uuid4()), "entity_id": schedule["events"][0]["id"],
            "issue_code": "AMBIGUOUS_TIMING", "severity": "ERROR", "status": "OPEN", "blocking": True,
            "message": "Variable cadence needs clarification", "details": {"origin": "DOMAIN"}}]
        projections[:] = [item.model_dump(mode="json") for item in project_schedule(UniversalSchedule.model_validate(schedule))]
        page.reload()
        page.get_by_test_id(f"schedule-visit-{schedule['events'][0]['id']}").click()
        expect(page.get_by_text(wording, exact=True)).to_be_visible()
        page.screenshot(path=str(output / "phone-source-wording.png"), full_page=True)
        page.get_by_text("Resolve items before approval", exact=True).click()
        page.get_by_placeholder("Add a short note about your review").fill("I read the source.")
        expect(page.get_by_role("button", name="Confirm table and approve", exact=True)).to_be_disabled()
        schedule["events"][0]["timing"] = original_timing
        schedule["validation_issues"] = []
        projections[:] = [item.model_dump(mode="json") for item in project_schedule(UniversalSchedule.model_validate(schedule))]
        # Archived versions must not offer an approval action.
        schedule["schedule_metadata"]["status"] = "ARCHIVED"
        page.reload()
        expect(page.get_by_text("Archived", exact=True)).to_be_visible()
        expect(page.get_by_text("Review and approve", exact=True)).to_have_count(0)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=str(output / "desktop-table.png"), full_page=True)
        # Upload must expose the same table before any trial or schedule save.
        schedule["schedule_metadata"]["status"] = "DRAFT"
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("http://localhost:8081/sponsor/add-trial", wait_until="domcontentloaded")
        page.get_by_test_id("confirm-create-trial").click()
        page.get_by_test_id("trial-proto").fill("UI-SMOKE")
        page.get_by_test_id("protocol-lookup").click()
        with page.expect_file_chooser() as chooser:
            page.get_by_test_id("upload-protocol").click()
        chooser.value.set_files(str(ROOT / "backend/eval/workflow-smoke-protocol.txt"))
        expect(page.get_by_text("Tap a visit to see activities, notes and protocol source.", exact=True)).to_be_visible()
        expect(page.get_by_text("Save schedule and continue", exact=True)).to_be_visible()
        expect(page.get_by_test_id("upload-protocol")).not_to_be_visible()
        expect(page.get_by_test_id("trial-title")).to_have_count(0)
        assert not any(item["method"] == "POST" and item["path"].endswith("/trials") for item in requests)
        page.screenshot(path=str(output / "phone-extraction-preview.png"), full_page=True)
        assert not errors, errors
        (output / "checks.json").write_text(json.dumps({"passed": True, "api_mode": "mocked", "requests": requests, "page_errors": errors}, indent=2))
        browser.close()
    print("UI checks passed: upload preview before save, phone table, grouped activities, source evidence, approval, unresolved source wording and approval block, archived version.")


if __name__ == "__main__":
    main()
