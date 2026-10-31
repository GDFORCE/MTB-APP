"""Protocol fields beyond the kit's original schema, as normalize checks them, and the review
questions it settles without a person. Constructed models only; no network or database."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from typing import Any

import pytest
from fastapi import HTTPException

import server  # noqa: F401 - kit_routes imports back from server, so server loads first
import kit_routes
from app.kit.normalize import build_template_model, normalize


def visit(vid: str, anchor: str = "screening", offset: int = 0, **extra) -> dict:
    return {"id": vid, "code": vid.upper(), "name": vid, "anchor": anchor, "offset_days": offset, **extra}


def model(**extra) -> dict:
    base = {
        "anchors": [{"id": "screening", "label": "Screening", "kind": "start"},
                    {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening",
                     "offset_days": 14, "is_day1": True}],
        "questions": [{"id": "arm", "label": "Arm", "text": "Arm?", "kind": "choice", "scope": "participant",
                       "options": [{"value": "A", "label": "A"}, {"value": "B", "label": "B"}]},
                      {"id": "age", "label": "Age", "text": "Age?", "kind": "number", "scope": "participant"}],
        "visits": [visit("scr"), visit("d1", "day1")],
    }
    base.update(extra)
    return base


def test_a_date_with_a_range_is_entered_per_participant_not_counted():
    raw = model()
    raw["anchors"][1]["range"] = {"relative_to": "screening", "min_days": 1, "max_days": 28}
    day1 = normalize(raw)["anchors"][1]
    assert day1["kind"] == "ask" and day1["range"] == {"relative_to": "screening", "min_days": 1, "max_days": 28}
    # The typical gap stays only as the planned timing used to order visits.
    assert day1["estimates"] == [{"when": [], "relative_to": "screening", "offset_days": 14}]


def test_a_review_question_that_only_picks_a_gap_becomes_a_range():
    raw = model(review_questions=[{"id": "gap", "text": "Days from screening to Day 1?", "recommended": "14", "options": [
        {"value": str(d), "label": f"{d} days", "patches": [{"target": "anchor", "id": "day1", "field": "offset_days", "value": d}]}
        for d in (7, 14, 28)]}])
    out = normalize(raw)
    assert out["review_questions"] == []
    assert out["anchors"][1]["range"] == {"relative_to": "screening", "min_days": 7, "max_days": 28}
    assert any("entered for each participant" in n for n in out["notes"])


def test_display_only_and_no_effect_review_questions_are_settled_without_a_person():
    raw = model(review_questions=[
        {"id": "ahead", "text": "How many cycles to show?", "options": [
            {"value": "4", "label": "4", "patches": [{"target": "visit", "id": "cyc", "field": "repeat.count", "value": 4}]},
            {"value": "8", "label": "8", "patches": [{"target": "visit", "id": "cyc", "field": "repeat.count", "value": 8}]}]},
        {"id": "same", "text": "Footnote b?", "options": [{"value": "x", "label": "X", "patches": []},
                                                          {"value": "y", "label": "Y", "patches": []}]},
        {"id": "win", "text": "Which window?", "options": [
            {"value": "3", "label": "3", "patches": [{"target": "visit", "id": "d1", "field": "window_after", "value": 3}]},
            {"value": "7", "label": "7", "patches": [{"target": "visit", "id": "d1", "field": "window_after", "value": 7}]}]},
    ])
    raw["visits"].append(visit("cyc", "day1", 21, repeat={"every_days": 21, "open_ended": True, "count": 6}))
    assert [q["id"] for q in normalize(raw)["review_questions"]] == ["win"]


def test_repeat_stages_limits_delays_and_every_nth_assessments_are_kept_and_checked():
    raw = model()
    raw["visits"].append(visit("scan", "day1", 56, repeat={
        "every_days": 0, "stages": [{"every_days": 84}, {"every_days": 56, "until_day": 365}, {"every_days": -3}],
        "max_days": 730, "max_delay_days": 21, "delay_effect": "Discontinue treatment.", "count": 0,
        "overrides": [{"every": 3, "from": 3, "add": ["CT"]}, {"n": "bad"}]}))
    r = normalize(raw)["visits"][-1]["repeat"]
    assert r["stages"] == [{"every_days": 56, "until_day": 365}, {"every_days": 84, "until_day": None}]
    assert r["every_days"] == 56 and r["max_days"] == 730 and r["max_delay_days"] == 21
    assert r["count"] is None  # no number stated: never made up
    assert r["overrides"] == [{"n": [], "add": [{"name": "CT", "if": []}], "remove": [], "note": None,
                               "every": 3, "from": 3, "to": None}]


def test_procedure_need_conditions_gaps_and_preparation():
    raw = model()
    raw["visits"][1].update({
        "procedures": [{"name": "ECG", "need": "if_indicated"}, {"name": "Labs", "reuse_within_days": 3},
                       {"name": "Geriatric scale", "if": [{"question": "age", "op": ">=", "value": 65}]},
                       {"name": "PK", "if": [{"any": [{"question": "arm", "in": ["A"]}, {"not": {"question": "age", "op": "<", "value": 18}}]}]}],
        "min_gap": {"days": 14, "after": "nope"}, "prep": {"fasting_hours": 8, "notes": ["Hold metformin", ""]},
    })
    v = normalize(raw)["visits"][1]
    assert [p.get("need") for p in v["procedures"]] == ["if_indicated", None, None, None]
    assert v["procedures"][1]["reuse_within_days"] == 3
    assert v["procedures"][2]["if"] == [{"question": "age", "op": ">=", "value": 65}]
    assert v["min_gap"] == {"days": 14, "after": None}
    assert v["prep"] == {"fasting_hours": 8, "notes": ["Hold metformin"]}


def test_a_condition_on_a_question_nobody_asks_is_reported_once():
    raw = model()
    raw["visits"][1]["include_if"] = [{"question": "ghost", "in": ["yes"]}]
    notes = [n for n in normalize(normalize(raw))["notes"] if "ghost" in n]
    assert len(notes) == 1 and "D1" in notes[0]


def test_crossover_answers_and_pauses_on_rules_are_checked():
    raw = model(
        anchors=model()["anchors"] + [{"id": "resume", "label": "Treatment resumed", "kind": "ask"}],
        events=[{"id": "pd", "label": "Progression"}, {"id": "hold", "label": "Treatment held"}],
        rules=[{"id": "x", "label": "Crossover", "when": {"event": "pd"}, "set_answers": {"arm": "B", "age": "x", "ghost": "1"}},
               {"id": "y", "label": "Hold", "when": {"event": "hold"},
                "pause": {"visits": ["d1", "nope"], "resume": "resume", "mode": "skip"}},
               {"id": "z", "label": "Bad", "when": {"event": "pd"}, "set_answers": {"arm": "C"}}])
    rules = {r["id"]: r for r in normalize(raw)["rules"]}
    assert rules["x"]["set_answers"] == {"arm": "B", "age": "x"}
    assert rules["y"]["pause"] == {"visits": ["d1"], "resume": "resume", "mode": "skip"}
    assert rules["z"]["set_answers"] is None


def test_rule_conditions_accept_or_not_and_comparisons_but_not_unknown_questions():
    raw = model(events=[{"id": "pd", "label": "Progression"}], rules=[
        {"id": "ok", "label": "Older", "when": {"event": "pd"}, "if": [{"any": [{"question": "age", "op": ">=", "value": 65},
                                                                             {"not": {"question": "arm", "in": ["A"]}}]}]},
        {"id": "bad", "label": "Ghost", "when": {"event": "pd"}, "if": [{"question": "ghost", "op": ">", "value": 1}]}])
    out = normalize(raw)
    assert [r["id"] for r in out["rules"]] == ["ok"]
    assert any("Ghost" in n for n in out["notes"])


def test_normalize_is_stable_when_run_again():
    raw = model(review_questions=[{"id": "gap", "text": "Gap?", "options": [
        {"value": "7", "label": "7", "patches": [{"target": "anchor", "id": "day1", "field": "offset_days", "value": 7}]},
        {"value": "21", "label": "21", "patches": [{"target": "anchor", "id": "day1", "field": "offset_days", "value": 21}]}]}])
    once = normalize(raw)
    assert normalize(copy.deepcopy(once)) == once
    assert build_template_model(once, {}) == once


def _matches(doc: dict, query: dict) -> bool:
    for key, want in query.items():
        value: Any = doc
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(want, dict) and "$exists" in want:
            if (value is not None) != want["$exists"]:
                return False
        elif isinstance(want, dict) and "$ne" in want:
            if value == want["$ne"]:
                return False
        elif value != want:
            return False
    return True


def _set(doc: dict, changes: dict) -> None:
    for key, value in changes.items():
        target = doc
        *path, last = key.split(".")
        for part in path:
            target = target.setdefault(part, {})
        target[last] = value


class _Cursor:
    def __init__(self, docs):
        self._it = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._it))
        except StopIteration:
            raise StopAsyncIteration from None


class _Collection:
    def __init__(self, docs=()):
        self.docs: list[dict] = [copy.deepcopy(d) for d in docs]

    async def find_one(self, query, projection=None, **kwargs):
        return next((copy.deepcopy(d) for d in self.docs if _matches(d, query)), None)

    def find(self, query, projection=None):
        return _Cursor([d for d in self.docs if _matches(d, query)])

    async def replace_one(self, query, doc, upsert=False):
        self.docs = [d for d in self.docs if not _matches(d, query)] + [copy.deepcopy(doc)]

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                _set(d, update["$set"])
                return

    async def update_many(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                _set(d, update["$set"])


def _routes(monkeypatch, patients=(), invitations=()):
    class Db:
        trial_kit_models = _Collection()
        trial_kit_model_history = _Collection()
        trials = _Collection([{"id": "t1", "title": "Trial"}])

    Db.patients = _Collection(patients)
    Db.invitations = _Collection(invitations)
    stored: dict = {}

    async def allowed(user, trial_id):
        return {"id": trial_id}

    async def audit(*args, **kwargs):
        return None

    async def patient_for(user, patient_id):
        return await Db.patients.find_one({"id": patient_id})

    async def store(sha, data, file_name, trial_id):
        stored[sha] = (data, {"file_name": file_name, "content_type": "application/pdf"})

    async def read(sha):
        return stored.get(sha)

    monkeypatch.setattr(kit_routes, "db", Db)
    monkeypatch.setattr(kit_routes, "_trial_for", allowed)
    monkeypatch.setattr(kit_routes, "write_audit", audit)
    monkeypatch.setattr(kit_routes, "_require_patient", patient_for)
    monkeypatch.setattr(kit_routes, "_store_protocol_file", store)
    monkeypatch.setattr(kit_routes, "_read_protocol_file", read)
    return Db, stored


STAFF = {"id": "u1", "role": "pi"}


def test_saving_again_keeps_the_earlier_version_and_the_analysis_record(monkeypatch):
    Db, _ = _routes(monkeypatch)
    body = kit_routes.KitModelIn(baseModel=model(), fileName="p.pdf", fileSha256="A" * 64,
                                 warnings=["Cross-check skipped"], corrections=None)
    first = asyncio.run(kit_routes.save_kit_model("t1", body, STAFF))
    second = asyncio.run(kit_routes.save_kit_model("t1", body.model_copy(update={"fileSha256": "not-a-hash"}), STAFF))

    assert (first["version"], second["version"]) == (1, 2)
    current = Db.trial_kit_models.docs[0]
    assert current["version"] == 2 and current["file_sha256"] is None
    assert current["analysis_warnings"] == ["Cross-check skipped"] and current["corrections"] is None
    [old] = Db.trial_kit_model_history.docs
    assert old["version"] == 1 and old["file_sha256"] == "a" * 64 and old["replaced_by"] == "u1"


def test_the_protocol_file_is_stored_from_the_analysis_job_and_can_be_downloaded(monkeypatch):
    _, stored = _routes(monkeypatch)
    data = b"%PDF-1.7 protocol"
    job = kit_routes._new_job("u1", "p.pdf")
    job["file"] = data
    sha = hashlib.sha256(data).hexdigest()
    body = kit_routes.KitModelIn(baseModel=model(), fileName="p.pdf", fileSha256=sha, analysisJobId=job["id"])
    assert asyncio.run(kit_routes.save_kit_model("t1", body, STAFF))["file_stored"] is True
    assert stored[sha][0] == data
    response = asyncio.run(kit_routes.download_protocol_file("t1", None, STAFF))
    assert response.body == data and 'filename="p.pdf"' in response.headers["content-disposition"]
    # Someone else's job is never stored with this model.
    other = kit_routes._new_job("u2", "x.pdf")
    other["file"] = b"%PDF-other"
    body2 = body.model_copy(update={"analysisJobId": other["id"], "fileSha256": hashlib.sha256(b"%PDF-other").hexdigest()})
    assert asyncio.run(kit_routes.save_kit_model("t1", body2, STAFF))["file_stored"] is False
    with pytest.raises(HTTPException) as error:
        asyncio.run(kit_routes.download_protocol_file("t1", None, STAFF))
    assert error.value.status_code == 404


def test_patients_stay_on_their_version_until_staff_move_them(monkeypatch):
    record = {"startAnchor": "screening", "startDate": "2026-11-02", "answers": {"arm": "A", "age": "40"},
              "events": [], "actuals": {}, "doseTimes": {}}
    Db, _ = _routes(monkeypatch, patients=[
        {"id": "p1", "trial_id": "t1", "subject_id": "S1", "kit": record},
        {"id": "p2", "trial_id": "t1", "subject_id": "S2", "kit": record}],
        invitations=[{"id": "i1", "trial_id": "t1", "status": "pending", "patient_data": {"kit": record}}])
    asyncio.run(kit_routes.save_kit_model("t1", kit_routes.KitModelIn(baseModel=model()), STAFF))

    # Version 2 adds a participant question nobody has answered yet.
    v2 = model()
    v2["questions"].append({"id": "cohort", "label": "Cohort", "text": "Cohort?", "kind": "choice", "scope": "participant",
                            "options": [{"value": "1", "label": "1"}]})
    v2["visits"].append(visit("extra", "day1", 7))
    saved = asyncio.run(kit_routes.save_kit_model("t1", kit_routes.KitModelIn(baseModel=v2), STAFF))
    assert [p["kit_model_version"] for p in Db.patients.docs] == [1, 1]
    assert Db.invitations.docs[0]["patient_data"]["kit_model_version"] == 1
    assert saved["patients"]["on_earlier_version"] == 2 and saved["patients"]["can_move"] == 0
    assert "Cohort" in saved["patients"]["cannot_move"][0]["problem"]

    # The patient's schedule still reads version 1, and offers version 2 with what blocks the move.
    payload = asyncio.run(kit_routes.read_patient_kit("p1", STAFF))
    assert payload["model_version"] == 1 and len(payload["model"]["visits"]) == 2
    assert payload["latest"]["version"] == 2 and "Cohort" in payload["latest"]["problem"]
    with pytest.raises(HTTPException) as error:
        asyncio.run(kit_routes.move_patient_to_latest("p1", STAFF))
    assert error.value.status_code == 422
    # Saving the patient's record still checks it against their own version.
    asyncio.run(kit_routes.save_patient_kit("p1", record, STAFF))

    # Version 3 asks the cohort later (a decision), so the record fits and staff can move them.
    v3 = copy.deepcopy(v2)
    v3["questions"][-1]["scope"] = "decision"
    v3["questions"][-1]["ask_at"] = {"visit": "extra"}
    asyncio.run(kit_routes.save_kit_model("t1", kit_routes.KitModelIn(baseModel=v3), STAFF))
    assert asyncio.run(kit_routes.read_patient_kit("p1", STAFF))["latest"]["problem"] is None
    moved = asyncio.run(kit_routes.move_patient_to_latest("p1", STAFF))
    assert moved["model_version"] == 3 and moved["latest"] is None and len(moved["model"]["visits"]) == 3
    assert Db.patients.docs[0]["kit_model_version"] == 3 and Db.patients.docs[1]["kit_model_version"] == 1
    # Earlier versions stay readable, for the patient list.
    assert asyncio.run(kit_routes.read_kit_model("t1", 1, STAFF))["version"] == 1
