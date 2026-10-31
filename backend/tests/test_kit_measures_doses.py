"""Values measured at visit checks and dose levels: what normalize keeps or drops, and what a
patient's record may hold. Constructed models only; no network or database."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import server  # noqa: F401 - kit_routes imports back from server, so server loads first
from app.kit.normalize import normalize
from kit_routes import clean_participant


def visit(vid: str, anchor: str = "day1", offset: int = 0, **extra) -> dict:
    return {"id": vid, "code": vid.upper(), "name": vid, "anchor": anchor, "offset_days": offset, **extra}


ANC_LOW = {"if": [{"measure": "anc", "of": "worst_here", "op": "<", "value": 0.5}], "dose": "dx", "step": -1}


def raw_model(check: dict | None = None, **extra) -> dict:
    base = {
        "anchors": [{"id": "screening", "label": "Screening", "kind": "start"},
                    {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening",
                     "offset_days": 14, "is_day1": True}],
        "measures": [{"id": "anc", "label": "ANC", "unit": "× 10^9/L", "kind": "value", "worse": "lower"},
                     {"id": "cr", "label": "Creatinine", "kind": "value", "baseline": "Screening value"},
                     {"id": "neut", "label": "Neuropathy", "kind": "grade"}],
        "doses": [{"id": "dx", "label": "Drug X", "levels": [{"id": "0", "label": "100 mg"}, {"id": "-1", "label": "75 mg"}],
                   "start": "0", "visits": ["cyc"], "below_lowest": "stop"}],
        "visits": [visit("scr", "screening"), visit("cyc", repeat={"every_days": 21, "chain": True, "count": 4}, check={
            "text": "ANC ≥ 1.5?", "limits": [{"measure": "anc", "op": ">=", "value": 1.5}], "dose_changes": [ANC_LOW],
            **(check or {})})],
    }
    base.update(extra)
    return base


def test_measures_doses_and_check_limits_are_kept():
    out = normalize(raw_model())
    assert [m["worse"] for m in out["measures"]] == ["lower", None, "higher"]  # a grade is worse as it goes up
    assert out["doses"][0]["levels"] == [{"id": "0", "label": "100 mg"}, {"id": "-1", "label": "75 mg"}]
    ck = out["visits"][1]["check"]
    assert ck["measures"] == ["anc"]
    assert ck["limits"] == [{"measure": "anc", "op": ">=", "value": 1.5, "of": "latest"}]
    assert ck["dose_changes"] == [{"dose": "dx", "step": -1, "to": None, "stop": False, "occurrence": None, "and_later": False,
                                   "note": None, "if": [{"measure": "anc", "op": "<", "value": 0.5, "of": "worst_here"}]}]


def test_a_limit_on_an_unknown_value_falls_back_to_a_staff_answer():
    out = normalize(raw_model({"limits": [{"measure": "anc", "op": ">=", "value": 1.5}, {"measure": "plt", "op": ">=", "value": 100}]}))
    assert out["visits"][1]["check"]["limits"] == []  # never fewer limits than the protocol has
    assert any("answer it Yes or No by hand" in n for n in out["notes"])


def test_worst_needs_a_direction_and_unconditional_dose_changes_are_dropped():
    out = normalize(raw_model({"dose_changes": [
        {"if": [{"measure": "cr", "of": "worst_so_far", "op": ">", "value": 2}], "dose": "dx", "step": -1},  # cr has no "worse"
        {"if": [], "dose": "dx", "step": -1},
        {"if": [{"held_days": {"op": ">", "value": 7}}], "dose": "dx", "step": -1},
        {"if": [{"measure": "anc", "op": "<", "value": 1}], "dose": "dx", "to": "-9"},  # no such level, no step
    ]}))
    assert [c["if"] for c in out["visits"][1]["check"]["dose_changes"]] == [[{"held_days": {"op": ">", "value": 7}}]]
    assert sum("dose change on the CYC check was left out" in n for n in out["notes"]) == 3


def test_baseline_limits_and_rule_dose_changes():
    out = normalize(raw_model({"limits": [{"measure": "cr", "op": "<=", "baseline_factor": 1.5}]}, events=[
        {"id": "ae", "label": "Adverse event", "questions": [{"id": "grade", "label": "Grade", "text": "Grade?", "kind": "choice",
                                                               "options": [{"value": "3", "label": "3"}]}]}],
        rules=[{"id": "ae3", "label": "Grade 3 AE", "when": {"event": "ae"}, "if": [{"event_question": "grade", "in": ["3"]}],
                "dose_changes": [{"dose": "dx", "step": -1}, {"dose": "nope", "step": -1}]}]))
    assert out["visits"][1]["check"]["limits"] == [{"measure": "cr", "op": "<=", "baseline_factor": 1.5, "of": "latest"}]
    assert out["rules"][0]["dose_changes"] == [{"dose": "dx", "step": -1, "to": None, "stop": False}]
    assert any("unknown dose or level" in n for n in out["notes"])


def test_a_dose_without_known_visits_is_left_out_and_an_unstated_start_stays_unknown():
    out = normalize(raw_model(doses=[
        {"id": "dx", "label": "Drug X", "levels": ["100 mg", "75 mg"], "visits": ["cyc"]},
        {"id": "dy", "label": "Drug Y", "levels": ["10 mg"], "visits": ["nowhere"]},
    ]))
    assert [d["id"] for d in out["doses"]] == ["dx"]
    assert out["doses"][0]["start"] is None and out["doses"][0]["below_lowest"] == "warn"
    assert any("starting dose level of Drug X is not stated" in n for n in out["notes"])
    assert any("Dose levels for Drug Y were left out" in n for n in out["notes"])


@pytest.fixture()
def model() -> dict:
    return normalize(raw_model())


def refused(model, raw) -> str:
    with pytest.raises(HTTPException) as error:
        clean_participant(model, raw)
    assert error.value.status_code == 422
    return error.value.detail


def record(**extra) -> dict:
    return {"startDate": "2026-11-02", "answers": {}, **extra}


def test_values_baselines_and_dose_levels_are_kept(model):
    out = clean_participant(model, record(
        checks={"cyc#2": [{"date": "2026-12-08", "values": {"anc": "0.8"}}, {"date": "2026-12-01", "answer": "no", "values": {"anc": ""}}]},
        baselines={"cr": 1.1, "anc": ""}, doseLevels={"cyc#3": {"dx": "-1"}}))
    assert out["checks"] == {"cyc#2": [{"date": "2026-12-01", "answer": "no"}, {"date": "2026-12-08", "values": {"anc": 0.8}}]}
    assert out["baselines"] == {"cr": 1.1}
    assert out["doseLevels"] == {"cyc#3": {"dx": "-1"}}


@pytest.mark.parametrize("raw, message", [
    ({"checks": {"cyc#2": [{"date": "2026-12-08", "values": {"plt": 90}}]}}, "not measured at this check"),
    ({"checks": {"cyc#2": [{"date": "2026-12-08", "values": {"anc": "low"}}]}}, "enter a number for ANC"),
    ({"checks": {"cyc#2": [{"date": "2026-12-08"}]}}, "Yes or No, or the measured values"),
    ({"baselines": {"neut": 2.5}}, "grade from 0 to 5"),
    ({"baselines": {"plt": 100}}, "Unknown measure"),
    ({"doseLevels": {"cyc#2": {"dx": "-5"}}}, "has no level"),
    ({"doseLevels": {"scr#1": {"dx": "-1"}}}, "is not given at scr#1"),
])
def test_bad_values_and_levels_are_refused(model, raw, message):
    assert message in refused(model, record(**raw))


def test_a_rule_may_stop_visits_an_event_added():
    out = normalize(raw_model(events=[
        {"id": "pd", "label": "Progression", "visits": [{"id": "saf", "code": "SAF", "name": "Safety", "offset_days": 28}]},
        {"id": "nt", "label": "New therapy"},
    ], rules=[{"id": "nt_stop", "label": "New therapy", "when": {"event": "nt"}, "stop_visits": ["saf", "cyc", "nowhere"]}]))
    assert out["rules"][0]["stop_visits"] == ["saf", "cyc"]


def test_last_dose_date_and_visits_kept_after_withdrawal():
    out = normalize(raw_model(
        anchors=[{"id": "screening", "label": "Screening", "kind": "start"},
                 {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening", "offset_days": 14, "is_day1": True},
                 {"id": "last_dose", "label": "Last dose", "kind": "last_visit", "last_of": ["cyc", "call", "nowhere"]},
                 {"id": "orphan", "label": "Last of nothing", "kind": "last_visit", "last_of": ["nowhere"]}],
        visits=[visit("scr", "screening"), visit("cyc"), visit("call", "last_dose", 30)],
        events=[{"id": "wd", "label": "Withdrawal", "ends_schedule": True, "keeps_visits": ["call", "nowhere"]},
                {"id": "death", "label": "Death", "ends_schedule": True}]))
    anchors = {a["id"]: a for a in out["anchors"]}
    # Never the last of itself; unknown visits dropped; nothing left -> entered by the site.
    assert anchors["last_dose"]["kind"] == "last_visit" and anchors["last_dose"]["last_of"] == ["cyc"]
    assert anchors["orphan"]["kind"] == "ask"
    assert any("Last of nothing" in n for n in out["notes"])
    assert out["events"][0]["keeps_visits"] == ["call"] and "keeps_visits" not in out["events"][1]


def test_daily_dosing_flag_is_kept_only_on_a_last_dose_date():
    # APD334-202: tablets daily at home, so a logged early stop is the last dose (schedule.ts setLast).
    out = normalize(raw_model(
        anchors=[{"id": "screening", "label": "Screening", "kind": "start"},
                 {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening", "offset_days": 14,
                  "is_day1": True, "stop_is_last_dose": True},
                 {"id": "last_dose", "label": "Last dose", "kind": "last_visit", "last_of": ["cyc"], "stop_is_last_dose": "yes"},
                 {"id": "last_plain", "label": "Last visit", "kind": "last_visit", "last_of": ["cyc"], "stop_is_last_dose": False}],
        visits=[visit("scr", "screening"), visit("cyc")]))
    anchors = {a["id"]: a for a in out["anchors"]}
    assert anchors["last_dose"]["stop_is_last_dose"] is True
    assert "stop_is_last_dose" not in anchors["last_plain"]
    assert "stop_is_last_dose" not in anchors["day1"]


def test_the_last_dose_date_cannot_be_entered():
    m = normalize(raw_model(anchors=[{"id": "screening", "label": "Screening", "kind": "start"},
                                     {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening",
                                      "offset_days": 14, "is_day1": True},
                                     {"id": "last_dose", "label": "Last dose", "kind": "last_visit", "last_of": ["cyc"]}]))
    assert "Unknown protocol date" in refused(m, record(answers={"@last_dose": "2026-12-01"}))


def test_check_in_hours_before_a_dose_are_kept_only_when_usable():
    out = normalize(raw_model(visits=[
        visit("scr", "screening"), visit("d0", "day1", -1, arrive_before={"visit": "d1", "hours": 12}),
        visit("d1"), visit("bad", arrive_before={"visit": "nowhere", "hours": 12}),
        visit("self", arrive_before={"visit": "self", "hours": 12}), visit("zero", arrive_before={"visit": "d1", "hours": 0})]))
    by = {v["id"]: v for v in out["visits"]}
    assert by["d0"]["arrive_before"] == {"visit": "d1", "hours": 12}
    assert all("arrive_before" not in by[k] for k in ("d1", "bad", "self", "zero"))
    # The check-in time is recorded like a dose time, on the check-in visit.
    out_rec = clean_participant(out, record(doseTimes={"d0#1": "20:30"}))
    assert out_rec["doseTimes"] == {"d0#1": "20:30"}


def test_a_screening_finding_asked_as_a_number_decision_gates_an_assessment():
    out = normalize(raw_model(
        questions=[{"id": "eos_uln", "label": "Eosinophils", "text": "Eosinophil count (x ULN)?", "kind": "number",
                    "scope": "decision", "ask_at": {"visit": "scr"}}],
        visits=[visit("scr", "screening", procedures=[{"name": "Stool O&P", "if": [{"question": "eos_uln", "op": ">", "value": 2}]}]),
                visit("cyc")]))
    assert out["questions"][0]["scope"] == "decision"
    assert out["visits"][0]["procedures"][0]["if"] == [{"question": "eos_uln", "op": ">", "value": 2}]
    assert clean_participant(out, record(answers={"eos_uln": "2.5"}))["answers"] == {"eos_uln": "2.5"}
    assert "is not an answer" in refused(out, record(answers={"eos_uln": "high"}))
