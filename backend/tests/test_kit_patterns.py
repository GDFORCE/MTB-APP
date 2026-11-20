"""Pattern rules and blockers (18 Nov 2026, app/kit/patterns.py).

A protocol rule no kit field can hold is written as a named pattern rule. Clean-up keeps every
one; one without a handler, or whose params its handler cannot use, is a blocker: the model is
saved and reviewable, but no patient can be enrolled or moved onto it. When a handler ships, the
admin re-check clears every trial waiting on that name at once.

Imports server through kit_routes, so run it with the local Mongo env vars set (see
MTB_IMPLEMENTATION_HANDOFF.txt). The routes run against an in-memory stand-in for the database.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

import server  # noqa: F401 - kit_routes imports back from server, so server loads first
import kit_routes
from app.kit import patterns
from app.kit.analyze import SAMPLES, check_quotes
from app.kit.ledger import unused_fields
from app.kit.normalize import build_template_model, default_review_answers, normalize
from app.kit.prompts import build_prompt

SYN06 = Path(__file__).resolve().parents[2] / "docs" / "protocol-benchmarks" / "synthetic" / "syn06-beyond-vocabulary"


def sample() -> dict:
    return json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"]


def with_rules(*rules: dict) -> dict:
    raw = sample()
    raw["pattern_rules"] = list(rules)
    return raw


def first_visit(raw: dict) -> str:
    return raw["visits"][0]["id"]


def saved(raw: dict) -> dict:
    base = normalize(copy.deepcopy(raw))
    return build_template_model(base, default_review_answers(base))


# ── clean-up and status ──────────────────────────────────────────────────
def test_names_are_normalized_to_upper_snake_case():
    assert patterns.pattern_name("calendar months") == "CALENDAR_MONTHS"
    assert patterns.pattern_name(" Treatment-delay after hold ") == "TREATMENT_DELAY_AFTER_HOLD"
    assert patterns.pattern_name("12 weeks rule") == "WEEKS_RULE"
    assert patterns.pattern_name("") == ""


def test_an_unknown_pattern_is_kept_as_a_blocker_never_dropped():
    raw = sample()
    raw["pattern_rules"] = [{"pattern": "consecutive missed visits withdraw", "label": "Two missed doses: withdrawn",
                             "visits": [first_visit(raw)], "params": {"consecutive": 2},
                             "quote": "If two consecutive visits are missed, the participant is withdrawn.", "page": 7}]
    model = saved(raw)
    [rule] = model["pattern_rules"]
    assert rule["pattern"] == "CONSECUTIVE_MISSED_VISITS_WITHDRAW" and rule["blocked"] == "no_handler"
    assert rule["params"] == {"consecutive": 2}  # kept as written, for when a handler exists
    [b] = patterns.blockers(model)
    assert b == {"rule_id": rule["id"], "pattern": "CONSECUTIVE_MISSED_VISITS_WITHDRAW", "label": "Two missed doses: withdrawn",
                 "quote": "If two consecutive visits are missed, the participant is withdrawn.", "page": 7, "quote_found": None,
                 "visits": [first_visit(raw)], "reason": "no_handler", "problem": None, "has_handler": False}
    assert "cannot calculate yet (CONSECUTIVE_MISSED_VISITS_WITHDRAW)" in patterns.enrol_problem([b])


def test_a_known_pattern_with_unusable_params_is_blocked_with_the_reason():
    raw = sample()
    vid = first_visit(raw)
    model = saved(with_rules(
        {"id": "a", "pattern": "CALENDAR_MONTHS", "visits": [vid], "params": {"months": 1}, "quote": "q", "page": 1},
        {"id": "b", "pattern": "WEEKDAY", "visits": ["no_such_visit"], "params": {"weekday": "monday", "direction": "on_or_after"}},
        {"id": "c", "pattern": "BUSINESS_DAYS", "visits": [vid], "params": {"business_days": 3, "within": True}},
    ))
    blocked = {r["id"]: r["blocked"] for r in model["pattern_rules"]}
    assert "missing_day" in blocked["a"]  # never defaulted: the protocol must say it
    assert "no_such_visit" in blocked["b"]
    assert "excludes_holidays" in blocked["c"]
    assert {b["reason"] for b in patterns.blockers(model)} == {"params"}


def test_a_handled_pattern_is_not_a_blocker_and_its_params_are_cleaned():
    raw = sample()
    model = saved(with_rules({"id": "w", "pattern": "weekday", "visits": [first_visit(raw)],
                              "params": {"weekday": "Monday", "direction": "on_or_after"}, "quote": "q", "page": 2}))
    [rule] = model["pattern_rules"]
    assert "blocked" not in rule and rule["params"] == {"weekday": "monday", "direction": "on_or_after"}
    assert patterns.blockers(model) == [] and patterns.enrol_problem([]) is None


def test_models_without_pattern_rules_are_unchanged():
    model = saved(sample())
    assert "pattern_rules" not in model and patterns.blockers(model) == []


def test_pattern_rules_are_a_known_model_field_and_their_quotes_are_looked_up():
    raw = with_rules({"pattern": "WEEKDAY", "visits": [], "quote": "the first Monday on or after Day 210", "page": 1})
    assert not [p for p in unused_fields(raw) if "pattern_rules" in p["text"]]
    model = normalize(copy.deepcopy(raw))
    check_quotes(model, ["Final visit: the first Monday on or after Day 210 at the site."])
    assert model["pattern_rules"][0]["quote_found"] is True


def test_the_prompt_lists_every_handler_and_asks_for_new_names():
    text = build_prompt("PROTOCOL")
    for name in patterns.PATTERNS:
        assert f"· {name}:" in text
    assert '"pattern_rules": [{' in text and "UPPER_SNAKE_CASE" in text
    assert "goes in pattern_rules (below), not here" in text


def test_the_benchmark_model_blocks_on_its_two_missed_visit_rules():
    model = json.loads((SYN06 / "hand-model.json").read_text(encoding="utf-8"))
    assert sorted(b["pattern"] for b in patterns.blockers(model)) == [
        "CONSECUTIVE_MISSED_VISITS_WITHDRAW", "MISSED_VISIT_TESTS_CARRIED_OVER"]
    assert {r["pattern"] for r in model["pattern_rules"] if not r.get("blocked")} == {"BUSINESS_DAYS", "CALENDAR_MONTHS", "WEEKDAY"}


# ── routes, on an in-memory database ─────────────────────────────────────
def _get(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list):
            if part.isdigit():
                cur = cur[int(part)] if int(part) < len(cur) else _MISSING
            else:
                vals = [x.get(part, _MISSING) for x in cur if isinstance(x, dict)]
                cur = [v for v in vals if v is not _MISSING] or _MISSING
        elif isinstance(cur, dict):
            cur = cur.get(part, _MISSING)
        else:
            return _MISSING
        if cur is _MISSING:
            return _MISSING
    return cur


_MISSING = object()


def _matches(doc: dict, query: dict) -> bool:
    for key, want in query.items():
        got = _get(doc, key)
        if isinstance(want, dict) and any(k.startswith("$") for k in want):
            for op, arg in want.items():
                if op == "$exists" and (got is not _MISSING) != arg:
                    return False
                if op == "$in" and got not in arg:
                    return False
                if op == "$ne" and got == arg:
                    return False
        elif isinstance(got, list):
            if want not in got:
                return False
        elif got != want:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find(self, query=None, projection=None, **_):
        return Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    async def find_one(self, query=None, projection=None, **_):
        return next((copy.deepcopy(d) for d in self.docs if _matches(d, query or {})), None)

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def replace_one(self, query, doc, upsert=False):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                self.docs[i] = copy.deepcopy(doc)
                return
        if upsert:
            self.docs.append(copy.deepcopy(doc))

    async def update_one(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                d.update(copy.deepcopy(update.get("$set") or {}))
                return

    async def update_many(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                d.update(copy.deepcopy(update.get("$set") or {}))


class FakeDb:
    def __init__(self):
        self.trial_kit_models, self.trial_kit_model_history = Coll(), Coll()
        self.kit_study_effects = Coll()  # study-wide event effects merged into each calendar
        self.patients, self.invitations = Coll(), Coll()
        self.trials = Coll([{"id": "t1", "title": "Trial one", "protocol_id": "P-1"},
                            {"id": "t2", "title": "Trial two", "protocol_id": "P-2"}])


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDb()
    audits: list = []

    async def trial_for(user, trial_id):
        return {"id": trial_id}

    async def audit(user, action, detail, **ctx):
        audits.append((action, detail))

    async def require_patient(user, patient_id):
        return await fake.patients.find_one({"id": patient_id})

    monkeypatch.setattr(kit_routes, "db", fake)
    monkeypatch.setattr(kit_routes, "_trial_for", trial_for)
    monkeypatch.setattr(kit_routes, "write_audit", audit)
    monkeypatch.setattr(kit_routes, "_require_patient", require_patient)
    fake.audits = audits
    return fake


USER = {"id": "u1", "role": "sponsor"}
MISSED = {"id": "m", "pattern": "CONSECUTIVE_MISSED_VISITS_WITHDRAW", "label": "Two missed: withdrawn",
          "params": {"consecutive": 2}, "quote": "Two consecutive missed visits: withdrawn.", "page": 4}


def save(trial_id: str, raw: dict) -> dict:
    body = kit_routes.KitModelIn(baseModel=raw, siteAnswers={})
    return asyncio.run(kit_routes.save_kit_model(trial_id, body, USER))


def test_a_model_with_a_blocker_is_saved_but_cannot_enrol_or_take_patients(db):
    raw = with_rules({**MISSED, "visits": [first_visit(sample())]})
    out = save("t1", raw)
    assert [b["pattern"] for b in out["blockers"]] == ["CONSECUTIVE_MISSED_VISITS_WITHDRAW"]
    assert "cannot calculate yet" in out["enrol_problem"]
    stored = db.trial_kit_models.docs[0]
    assert stored["blockers"] == out["blockers"] and stored["model"]["pattern_rules"][0]["blocked"] == "no_handler"

    # A patient enrolled on an earlier, unblocked version cannot be moved onto this one.
    db.patients.docs.append({"id": "p1", "trial_id": "t1", "kit": {"startDate": "2026-11-02"}, "kit_model_version": 0})
    with pytest.raises(HTTPException) as error:
        asyncio.run(kit_routes.move_patient_to_latest("p1", {"id": "u2", "role": "pi"}))
    assert error.value.status_code == 409 and "cannot calculate yet" in error.value.detail


def test_the_admin_list_ranks_patterns_by_waiting_trials_with_the_protocol_words(db):
    vid = first_visit(sample())
    other = {"id": "d", "pattern": "TREATMENT_DELAY_AFTER_HOLD", "label": "Delay after a hold", "visits": [vid],
             "params": {}, "quote": "Delay the next cycle by the hold.", "page": 9}
    save("t1", with_rules({**MISSED, "visits": [vid]}, other))
    save("t2", with_rules({**MISSED, "visits": [vid], "quote": "Withdraw after two missed visits."}))
    out = asyncio.run(kit_routes.list_blockers())
    assert out["blocked_trials"] == 2
    assert [(g["pattern"], g["trials"], g["rules"]) for g in out["groups"]] == [
        ("CONSECUTIVE_MISSED_VISITS_WITHDRAW", 2, 2), ("TREATMENT_DELAY_AFTER_HOLD", 1, 1)]
    evidence = out["groups"][0]["evidence"]
    assert {(e["trial_title"], e["quote"], e["page"]) for e in evidence} == {
        ("Trial one", "Two consecutive missed visits: withdrawn.", 4), ("Trial two", "Withdraw after two missed visits.", 4)}
    assert {h["pattern"] for h in out["handlers"]} == set(patterns.PATTERNS)


def test_shipping_a_handler_and_rechecking_unblocks_every_waiting_trial_at_once(db, monkeypatch):
    vid = first_visit(sample())
    save("t1", with_rules({**MISSED, "visits": [vid]}))
    save("t2", with_rules({**MISSED, "visits": [vid]}, {"id": "x", "pattern": "SOMETHING_ELSE", "visits": [vid]}))

    # Nothing changed yet: the re-check leaves both blocked.
    out = asyncio.run(kit_routes.recheck_blockers(kit_routes.RecheckIn(), {"id": "admin"}))
    assert out["checked"] == 2 and out["unblocked"] == [] and out["still_blocked"] == 2

    # A developer adds the handler (here: a stand-in that accepts the stated count).
    def check(params, model, visits):
        return ({"consecutive": params["consecutive"]}, None) if isinstance(params.get("consecutive"), int) else (None, "no count")

    monkeypatch.setitem(patterns.PATTERNS, "CONSECUTIVE_MISSED_VISITS_WITHDRAW",
                        patterns.Pattern(title="Missed visits", prompt="", check=check))
    out = asyncio.run(kit_routes.recheck_blockers(kit_routes.RecheckIn(pattern="consecutive missed visits withdraw"), {"id": "admin"}))
    assert out["checked"] == 2 and out["unblocked"] == ["t1"] and out["still_blocked"] == 1
    t1 = next(d for d in db.trial_kit_models.docs if d["trial_id"] == "t1")
    assert t1["blockers"] == [] and "blocked" not in t1["model"]["pattern_rules"][0]
    assert "blocked" not in t1["base_model"]["pattern_rules"][0]
    t2 = next(d for d in db.trial_kit_models.docs if d["trial_id"] == "t2")
    assert [b["pattern"] for b in t2["blockers"]] == ["SOMETHING_ELSE"]  # still waits on its other rule
    assert db.audits[-1][0] == "kit.blockers_recheck"


def test_admin_routes_are_admin_only():
    deps = [d.dependency for d in kit_routes.admin_router.dependencies]
    assert len(deps) == 1
    with pytest.raises(HTTPException) as error:
        asyncio.run(deps[0](user={"role": "sponsor"}))
    assert error.value.status_code == 403
    paths = {r.path for r in kit_routes.admin_router.routes}
    assert paths == {"/api/admin/kit/blockers", "/api/admin/kit/blockers/recheck"}


def test_the_analysis_job_reports_blockers_with_the_model(monkeypatch):
    """Through the upload job (fake model replies): the AI's pattern rules come back cleaned, with
    the blockers and their count, and the quote is looked up in the PDF text."""
    from tests.test_kit_tables_route import _borderless_pdf

    monkeypatch.delenv("KIT_SAMPLE_MODEL", raising=False)
    raw = sample()
    raw["pattern_rules"] = [
        {"pattern": "missed visit tests carried over", "label": "Missed labs added to the next visit",
         "visits": [first_visit(raw)], "params": {}, "quote": "Narrative protocol text describing the study", "page": 1},
        {"pattern": "WEEKDAY", "label": "Final visit on a Monday", "visits": [first_visit(raw)],
         "params": {"weekday": "monday", "direction": "on_or_after"}, "quote": "not in this document", "page": 1}]

    class Client:
        model = "fake"
        on_status = None

        def json(self, prompt, **kwargs):
            if "Copy the table exactly" in prompt:
                return {"columns": [], "rows": []}
            if "MODEL VISITS:" in prompt:
                return {"corrections": []}
            if "MODEL EVENTS:" in prompt:
                return {"events": []}
            return copy.deepcopy(raw)

    monkeypatch.setattr(kit_routes, "OpenAIKitClient", Client)
    job = kit_routes._new_job("user-1", "protocol.pdf")
    result = kit_routes._run(job, _borderless_pdf(), "protocol.pdf")
    assert [b["pattern"] for b in result["blockers"]] == ["MISSED_VISIT_TESTS_CARRIED_OVER"]
    assert result["counts"]["blockers"] == 1
    rules = {r["pattern"]: r for r in result["baseModel"]["pattern_rules"]}
    assert rules["MISSED_VISIT_TESTS_CARRIED_OVER"]["quote_found"] is True
    assert rules["WEEKDAY"]["quote_found"] is False and "blocked" not in rules["WEEKDAY"]
    assert result["model"]["pattern_rules"][0]["blocked"] == "no_handler"
