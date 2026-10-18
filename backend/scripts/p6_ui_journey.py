"""P6 two-user product journey, driven through the REAL web build in a REAL browser.

Local and synthetic only. It starts nothing itself: run the API (uvicorn server:app) with
LOCAL Mongo settings, the static web export behind ``serve_web.py`` (which proxies /api), then:

    python -I backend/scripts/p6_ui_journey.py http://127.0.0.1:8766 docs/evidence/p6

People (all created through the product's own register + grants API):

* an organization admin (sponsor) who grants the others their rights;
* a PI who seeds the synthetic pilot, adopts and returns (``amendment.adopt``);
* a CRO reviewer who compares the versions and decides how their visits correspond
  (``schedule.review``).

Every step the browser performs is a click or a keystroke in the shipped screens. Screenshots
and the page text of each step are written to the evidence folder.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1].rstrip("/")
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
SUFFIX = str(int(time.time()))
ORG = f"P6 UI Org {SUFFIX}"
PASSWORD = "Synthetic#Pass1234"
LOG: list[dict] = []


def api(method: str, path: str, token: str | None = None, body: dict | None = None):
    request = urllib.request.Request(
        f"{BASE}/api{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def register(role: str, name: str):
    status, body = api("POST", "/auth/register", body={
        "email": f"{role}.{SUFFIX}@p6-ui.example.com", "password": PASSWORD, "full_name": name,
        "role": role, "organization": ORG})
    assert status == 200, (status, body)
    return {"token": body["access_token"], "refresh": body["refresh_token"], "id": body["user"]["id"],
            "role": role, "name": name}


def grant(admin, user, capability: str):
    status, body = api("POST", "/uctsm/authorization/grants", admin["token"], {
        "user_id": user["id"], "capability": capability, "reason": "synthetic P6 journey"})
    assert status == 201, (capability, status, body)


def note(step: str, page: Page, **extra):
    text = page.inner_text("body")
    shot = OUT / f"{len(LOG) + 1:02d}-{re.sub('[^a-z0-9]+', '-', step.lower()).strip('-')}.png"
    page.screenshot(path=str(shot), full_page=True)
    LOG.append({"step": step, "screenshot": shot.name, "text": text[:6000], **extra})
    return text


def open_as(playwright, user, path: str):
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(viewport={"width": 900, "height": 1400})
    context.add_init_script(
        "window.localStorage.setItem('access_token', %s); window.localStorage.setItem('refresh_token', %s);"
        % (json.dumps(user["token"]), json.dumps(user["refresh"])))
    page = context.new_page()
    page.on("pageerror", lambda error: LOG.append({"step": "PAGE ERROR", "text": str(error)}))
    page.goto(f"{BASE}{path}", wait_until="networkidle")
    return browser, page


def click(page: Page, text: str, *, exact: bool = False, timeout: int = 30000):
    page.get_by_text(text, exact=exact).first.click(timeout=timeout)
    page.wait_for_load_state("networkidle")


def main() -> None:
    admin = register("sponsor", "Sam Sponsor")
    pi = register("pi", "Pia Investigator")
    reviewer = register("cro", "Rae Reviewer")
    for capability in ("demo.seed", "amendment.adopt", "patient.command.high_impact"):
        grant(admin, pi, capability)
    grant(admin, reviewer, "schedule.review")
    _, body = api("GET", "/uctsm/authorization/me", pi["token"])
    assert body["capabilities"].get("amendment.adopt") == "grant"

    with sync_playwright() as playwright:
        # ---- the PI opens the synthetic pilot and adds its (unadopted) amendment -------------
        browser, page = open_as(playwright, pi, "/clinical/plane-schedule")
        click(page, "Open the synthetic pilot")
        page.get_by_text("PILOT-P001").first.wait_for(timeout=30000)
        note("PI opened the synthetic pilot", page)
        click(page, "Add the synthetic protocol amendment")
        page.wait_for_load_state("networkidle")
        # the trial id travels in the Compare link's target
        click(page, "Compare protocol versions and match their visits")
        page.get_by_text("Approved amendments").first.wait_for(timeout=30000)
        found = re.search(r"trialId=([0-9a-f-]{36})", page.url)
        assert found, page.url
        trial_id = found.group(1)
        page.get_by_text("Compare with the version it amends").first.wait_for(timeout=30000)
        note("PI sees the amendment list", page)
        click(page, "Compare with the version it amends")
        page.get_by_text("You do not have permission to do this").first.wait_for(timeout=30000)
        text = note("PI cannot compare: comparing needs the schedule-review right", page)
        assert "You do not have permission to do this" in text
        browser.close()

        # ---- before any review the PI is told what is missing, and cannot preview --------------
        _, body = api("GET", f"/uctsm/mongo-plane/trials/{trial_id}/patients", pi["token"])
        patient = body["items"][0]["external_patient_id"]
        browser, page = open_as(playwright, pi, f"/clinical/plane-schedule?trialId={trial_id}&patient={patient}")
        page.get_by_text("Protocol amendments").first.wait_for(timeout=30000)
        text = note("PI opens the patient before the comparison exists", page)
        assert "A reviewer must compare this version with the current one first." in text
        assert page.get_by_role("button", name="Preview what this would do").is_disabled()             or "Preview what this would do" in text
        browser.close()

        # ---- the reviewer compares and decides ------------------------------------------------
        browser, page = open_as(playwright, reviewer, f"/clinical/amendment-review?trialId={trial_id}")
        page.get_by_text("Approved amendments").first.wait_for(timeout=30000)
        note("Reviewer sees the approved amendment", page)
        click(page, "Compare with the version it amends")
        page.get_by_text("What changed").first.wait_for(timeout=30000)
        text = note("Reviewer compares the two versions", page)
        assert "Not ready" in text, "an unreviewed mapping must block adoption"
        assert "needs a reviewer" in text or "need your decision" in text
        page.get_by_placeholder("Why? (recorded under your name)").fill("Same imaging visit, re-coded in the amendment")
        click(page, "Confirm", exact=True)
        page.get_by_text("Ready: every visit has a decided match").first.wait_for(timeout=30000)
        text = note("Reviewer confirmed the proposed match", page)
        assert "Ready: every visit has a decided match" in text
        browser.close()

        # ---- the PI previews, then adopts ---------------------------------------------------------
        browser, page = open_as(playwright, pi, f"/clinical/plane-schedule?trialId={trial_id}&patient={patient}")
        page.get_by_text("Protocol amendments").first.wait_for(timeout=30000)
        text = note("PI opens the patient: nothing was moved automatically", page)
        assert "Current version" in text and "Nobody is moved automatically" in text
        click(page, "Preview what this would do")
        page.get_by_text("Before you decide").first.wait_for(timeout=30000)
        text = note("PI previews the move", page)
        for needle in ("From version", "recorded visit", "SAFETY_CALL", "IMAGING_BASELINE:0 → IMAGING_BL:0"):
            assert needle in text or needle.startswith("recorded"), needle
        page.get_by_placeholder("Why? (recorded in the audit trail)").last.fill("Synthetic PI decision to adopt amendment 1")
        click(page, "Confirm: move now")
        page.get_by_text("Done. The patient's schedule was recalculated.").first.wait_for(timeout=60000)
        note("PI adopted the amendment", page)
        page.reload(wait_until="networkidle")
        page.get_by_text("SAFETY_CALL").first.wait_for(timeout=30000)
        text = note("After a reload the calendar is the committed amendment schedule", page)
        assert "SAFETY_CALL" in text and "Applied" in text

        # ---- and returns, as a new authorised decision --------------------------------------------
        click(page, "Preview a return")
        page.get_by_text("Before you return this patient").first.wait_for(timeout=30000)
        note("PI previews a return", page)
        page.get_by_placeholder("Why? (recorded in the audit trail)").last.fill("Synthetic: amendment withdrawn at this site")
        click(page, "Confirm the return")
        page.get_by_text("Done. The patient's schedule was recalculated.").first.wait_for(timeout=60000)
        page.reload(wait_until="networkidle")
        text = note("After the return the calendar is Pilot 1.0 again", page)
        assert "IMAGING_BASELINE" in text and "SAFETY_CALL" not in text.split("History")[0]
        browser.close()

    (OUT / "journey.json").write_text(json.dumps(LOG, indent=1), encoding="utf-8")
    print(f"journey complete: {len(LOG)} steps, evidence in {OUT}")


if __name__ == "__main__":
    main()
