"""Local synthetic walkthrough of the Plane operations screen on the real exported app.

Covers (P10-02): report view reconciled to the committed schedule, reminder-preference form
(validation, save, reload), failed-message list gated by its own grant, pilot-cohort refusal in
plain language, and basic accessibility/responsive checks. People are created through the
product's own register + grants API; the pilot is the synthetic one the app seeds itself.
Never run against shared databases:

    python backend/scripts/p10_ui_journey.py http://127.0.0.1:8766 docs/evidence/p10
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
ORG = f"P10 UI Org {SUFFIX}"
PASSWORD = "Synthetic#Pass1234"
LOG: list[dict] = []
ERRORS: list[str] = []


def api(method, path, token=None, body=None):
    request = urllib.request.Request(
        f"{BASE}/api{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def register(role, name):
    status, body = api("POST", "/auth/register", body={
        "email": f"{role}.{SUFFIX}@p10-ui.example.com", "password": PASSWORD, "full_name": name,
        "role": role, "organization": ORG})
    assert status == 200, (status, body)
    return {"token": body["access_token"], "refresh": body["refresh_token"], "id": body["user"]["id"]}


def grant(admin, user, capability):
    status, body = api("POST", "/uctsm/authorization/grants", admin["token"], {
        "user_id": user["id"], "capability": capability, "reason": "Isolated synthetic P10 UI verification"})
    assert status in (200, 201), (status, body)


def open_as(playwright, person, path, width=1000):
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(viewport={"width": width, "height": 1500})
    context.add_init_script(
        "localStorage.setItem('access_token', %s); localStorage.setItem('refresh_token', %s);"
        % (json.dumps(person["token"]), json.dumps(person["refresh"])))
    page = context.new_page()
    page.on("pageerror", lambda error: ERRORS.append(str(error)))
    page.goto(BASE + path, wait_until="networkidle")
    return browser, page


def note(name, page: Page) -> str:
    filename = f"{len(LOG) + 1:02d}-{re.sub('[^a-z0-9]+', '-', name.lower())}.png"
    page.screenshot(path=str(OUT / filename), full_page=True)
    text = page.inner_text("body")
    LOG.append({"step": name, "screenshot": filename, "text": text[:12000]})
    (OUT / "journey.json").write_text(json.dumps(
        {"synthetic": True, "steps": LOG, "page_errors": ERRORS, "complete": False}, indent=2), encoding="utf-8")
    return text


def click(page, text, *, exact=False):
    page.get_by_text(text, exact=exact).first.click(timeout=30000)
    page.wait_for_load_state("networkidle")


def accessibility(page: Page, label: str):
    """Every field is reachable by its accessible name, buttons have text, nothing overflows."""
    unnamed = page.evaluate("""() => [...document.querySelectorAll('input, textarea')]
        .filter(e => !(e.getAttribute('aria-label') || e.getAttribute('placeholder') || (e.labels && e.labels.length)))
        .length""")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    empty_buttons = page.evaluate("""() => [...document.querySelectorAll('[role=button]')]
        .filter(e => !(e.innerText || e.getAttribute('aria-label')).trim()).length""")
    LOG.append({"step": f"accessibility: {label}", "unnamed_fields": unnamed, "horizontal_overflow_px": overflow,
                "buttons_without_name": empty_buttons})
    assert unnamed == 0, f"{unnamed} input(s) without an accessible name"
    assert overflow <= 1, f"horizontal overflow {overflow}px"
    assert empty_buttons == 0


def main():
    admin = register("sponsor", "Sam Sponsor")
    pi = register("pi", "Pia Investigator")
    lead = register("site", "Sue Siteadmin")
    grant(admin, pi, "demo.seed")
    grant(admin, lead, "outbox.replay")

    with sync_playwright() as playwright:
        browser, page = open_as(playwright, pi, "/clinical/plane-schedule")
        click(page, "Open the synthetic pilot")
        page.get_by_text("PILOT-P001").first.wait_for(timeout=30000)
        click(page, "Add the synthetic protocol amendment")
        click(page, "Compare protocol versions and match their visits")
        page.get_by_text("Approved amendments").first.wait_for(timeout=30000)
        found = re.search(r"trialId=([0-9a-f-]{36})", page.url)
        assert found, page.url
        trial = found.group(1)
        _, body = api("GET", f"/uctsm/mongo-plane/trials/{trial}/patients", pi["token"])
        patient = body["items"][0]["external_patient_id"]
        browser.close()

        # ---- schedule report ------------------------------------------------------------
        browser, page = open_as(playwright, pi, f"/clinical/plane-operations?trialId={trial}")
        page.get_by_text("Plane operations").first.wait_for(timeout=30000)
        note("Operations screen opens for the trial", page)
        accessibility(page, "operations screen, desktop")
        click(page, "Load report", exact=True)
        page.get_by_text(re.compile(r"\d+ patients, \d+ visits")).first.wait_for(timeout=30000)
        text = note("Schedule report loaded from the committed schedule", page)
        assert "PILOT-P001" in text or "patients" in text
        # the totals equal the rows from the API (the report and the calendar are one calculation)
        status, report = api("GET", f"/uctsm/mongo-plane/trials/{trial}/reports/schedule", pi["token"])
        assert status == 200, (status, report)
        visits = sum(len(p["rows"]) for p in report["patients"])
        assert re.search(rf"{len(report['patients'])} patients, {visits} visits", text), (visits, text[:400])

        # ---- reminder preferences: validation, save, reload ------------------------------------
        page.get_by_label("Patient id", exact=True).fill(patient)
        click(page, "Load", exact=True)
        text = note("Preferences: nothing recorded yet", page)
        assert "No preferences recorded yet." in text
        page.get_by_role("radio", name=re.compile("Consent granted")).click()
        page.get_by_label("Time zone", exact=True).fill("Asia/Kolkata")
        page.get_by_label("Delivery time", exact=True).fill("25:99")
        click(page, "Save preferences")
        text = note("An invalid delivery time is refused before anything is saved", page)
        assert "Saved (revision" not in text
        page.get_by_label("Delivery time", exact=True).fill("09:00")
        page.get_by_label("Days before the visit", exact=True).fill("1")
        click(page, "Save preferences")
        page.get_by_text(re.compile(r"Saved \(revision \d+\)")).first.wait_for(timeout=30000)
        note("Preferences saved", page)
        page.reload(wait_until="networkidle")
        page.get_by_label("Patient id", exact=True).fill(patient)
        click(page, "Load", exact=True)
        page.get_by_text("Loaded the saved preferences.").first.wait_for(timeout=30000)
        text = note("After a reload the saved preferences load back", page)
        _, saved = api("GET", f"/uctsm/mongo-plane/trials/{trial}/patients/{patient}/notification-preferences",
                       pi["token"])
        assert saved["preferences"]["consent"] == "GRANTED" and saved["preferences"]["delivery_time"] == "09:00"

        # ---- the cohort refusal is in plain language -------------------------------------------
        page.get_by_label("Cohort name", exact=True).fill("Synthetic pilot cohort")
        page.get_by_label("Legacy patient ids", exact=True).fill("legacy-does-not-exist")
        click(page, "Create shadow cohort")
        text = note("A cohort without the cutover right or unreconciled members is refused", page)
        assert "Shadow cohort created" not in text
        assert not re.search(r"\b(403|409|Traceback|undefined)\b", text), text[-600:]
        browser.close()

        # ---- failed messages need their own grant ---------------------------------------------
        browser, page = open_as(playwright, pi, f"/clinical/plane-operations?trialId={trial}")
        click(page, "Load failed messages")
        text = note("Without the replay grant the list is refused, no data shown", page)
        assert "No failed messages." not in text
        browser.close()
        browser, page = open_as(playwright, lead, f"/clinical/plane-operations?trialId={trial}")
        click(page, "Load failed messages")
        page.get_by_text("No failed messages.").first.wait_for(timeout=30000)
        note("With the replay grant the (empty) list loads", page)
        browser.close()

        # ---- phone width ---------------------------------------------------------------------
        browser, page = open_as(playwright, pi, f"/clinical/plane-operations?trialId={trial}", width=390)
        page.get_by_text("Plane operations").first.wait_for(timeout=30000)
        note("Phone width", page)
        accessibility(page, "operations screen, 390px")
        browser.close()

    assert not ERRORS, ERRORS
    report = json.loads((OUT / "journey.json").read_text(encoding="utf-8"))
    report["complete"] = True
    (OUT / "journey.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"P10 synthetic UI journey passed: {len(LOG)} entries; {OUT}")


if __name__ == "__main__":
    main()
