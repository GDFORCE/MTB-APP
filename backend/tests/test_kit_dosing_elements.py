"""Elements added 30 Oct 2026 from CG03-EOC, CRD/09 and BP11-301: a check's dosing table (give /
reduce this time / skip / hold), repeats until a check is met, a test's own time limit, dose levels
that remove visits, "lasting N days", "event logged before Week 12" and "or as soon as possible".
What normalize keeps or drops and what a patient's record may hold. Constructed models only."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import server  # noqa: F401 - kit_routes imports back from server, so server loads first
from app.kit.normalize import normalize
from kit_routes import clean_participant


def visit(vid: str, anchor: str = "day1", offset: int = 0, **extra) -> dict:
    return {"id": vid, "code": vid.upper(), "name": vid, "anchor": anchor, "offset_days": offset, **extra}


ANC = {"measure": "anc", "op": ">=", "value": 1500}
TABLE = [
    {"if": [{"measure": "anc", "op": ">=", "value": 1500}], "do": "give"},
    {"if": [{"measure": "anc", "op": ">=", "value": 1000}], "do": "reduce", "percent": 50, "dose": "gem"},
    {"if": [{"measure": "anc", "op": "<", "value": 1000}], "do": "skip", "note": "omitted, not made up"},
]


def raw_model(d8_check: dict | None = None, **extra) -> dict:
    base = {
        "anchors": [{"id": "screening", "label": "Screening", "kind": "start"},
                    {"id": "day1", "label": "Day 1", "kind": "relative", "relative_to": "screening", "offset_days": 14, "is_day1": True}],
        "measures": [{"id": "anc", "label": "ANC", "unit": "/mcL", "kind": "value", "worse": "lower"},
                     {"id": "crcl", "label": "CrCl", "unit": "ml/min", "kind": "value", "worse": "lower"}],
        "doses": [{"id": "gem", "label": "Gemcitabine", "start": "1000", "visits": ["d1", "d8"], "levels": [
            {"id": "1000", "label": "1000 mg/m2"}, {"id": "800", "label": "800 mg/m2"},
            {"id": "800d1", "label": "800 mg/m2 Day 1 only", "drop_visits": ["d8", "nope"]}]}],
        "visits": [
            visit("scr", "screening", procedures=[{"name": "Pregnancy test", "within_before": {"days": 3}},
                                                 {"name": "Labs", "within_before": {"hours": 72}}, {"name": "ECG", "within_before": {"days": -1}}]),
            visit("d1", repeat={"every_days": 21, "chain": True, "count": 6}),
            visit("d8", follows="d1", offset=7, check={"text": "ANC ok for Day 8?", "actions": TABLE, **(d8_check or {})}),
        ],
    }
    base.update(extra)
    return base


def by_id(items, vid):
    return next(v for v in items if v["id"] == vid)


def test_a_dosing_table_is_kept_row_by_row():
    out = normalize(raw_model())
    ck = by_id(out["visits"], "d8")["check"]
    assert [a["do"] for a in ck["actions"]] == ["give", "reduce", "skip"]
    assert ck["actions"][1] == {"do": "reduce", "if": [{"measure": "anc", "op": ">=", "value": 1000, "of": "latest"}],
                                "note": None, "percent": 50, "dose": "gem"}
    assert ck["measures"] == ["anc"]  # the values the table needs are asked at the check


@pytest.mark.parametrize("bad", [
    {"if": [{"measure": "plt", "op": "<", "value": 75000}], "do": "skip"},  # unknown value
    {"if": [], "do": "skip"},                                                # no limits: would act every time
    {"if": [ANC], "do": "reduce", "percent": 120},                           # not a reduction
    {"if": [ANC], "do": "delay"},                                            # not an outcome
])
def test_a_table_with_an_unreadable_row_is_dropped_whole(bad):
    out = normalize(raw_model({"actions": TABLE + [bad]}))
    assert "actions" not in by_id(out["visits"], "d8")["check"]
    assert any("outcome table on the D8 check" in n for n in out["notes"])


def test_a_check_without_a_table_has_no_actions_key():
    out = normalize(raw_model({"actions": [], "limits": [ANC]}))
    assert "actions" not in by_id(out["visits"], "d8")["check"]


def test_a_tests_own_time_limit_and_dose_levels_that_drop_visits():
    out = normalize(raw_model())
    procs = by_id(out["visits"], "scr")["procedures"]
    assert [p.get("within_before") for p in procs] == [{"days": 3}, {"hours": 72}, None]
    levels = out["doses"][0]["levels"]
    assert [lv.get("drop_visits") for lv in levels] == [None, None, ["d8"]]  # unknown visits are left out


def test_lasting_n_days_only_on_the_value_entered_now():
    out = normalize(raw_model({"actions": [], "limits": [{"measure": "anc", "op": "<", "value": 500, "for_days": 7}]}))
    assert by_id(out["visits"], "d8")["check"]["limits"] == [{"measure": "anc", "op": "<", "value": 500, "of": "latest", "for_days": 7}]
    bad = normalize(raw_model({"actions": [], "limits": [{"measure": "anc", "op": "<", "value": 500, "for_days": 0}]}))
    assert by_id(bad["visits"], "d8")["check"]["limits"] == []  # unreadable: staff answer by hand


def test_repeat_until_met_needs_a_check_and_works_on_event_visits():
    recheck = visit("crcl_re", offset=7, repeat={"every_days": 7, "until_check": True},
                    check={"text": "CrCl ≥ 50?", "limits": [{"measure": "crcl", "op": ">=", "value": 50}], "max_wait_days": 28,
                           "on_timeout": "stop", "dose_changes": [{"if": [ANC], "dose": "gem", "step": -1}]})
    out = normalize(raw_model(
        visits=raw_model()["visits"] + [visit("fu", repeat={"every_days": 7, "until_check": True})],
        events=[{"id": "cis_hold", "label": "Cisplatin held for CrCl < 50", "visits": [recheck]},
                {"id": "ae", "label": "AE", "visits": [visit("ae_fu", check={"text": "Resolved?"})]}]))
    assert "until_check" not in by_id(out["visits"], "fu")["repeat"]  # nothing to be met
    ev = by_id(out["events"], "cis_hold")["visits"][0]
    assert ev["repeat"]["until_check"] is True and ev["check"]["limits"][0]["measure"] == "crcl"
    assert ev["check"]["dose_changes"] == []  # doses run on scheduled visits
    assert any("Dose changes on the CRCL_RE check were left out" in n for n in out["notes"])
    assert by_id(out["events"], "ae")["visits"][0]["check"] is None  # a check on a one-off event visit is not run
    assert "until_check" not in by_id(out["visits"], "d1")["repeat"]


def test_event_before_a_visit_and_as_soon_as_possible():
    out = normalize(raw_model(
        visits=raw_model()["visits"] + [
            visit("w12", offset=83), visit("w24", offset=167, procedures=[
                {"name": "Diary", "if": [{"not": {"event": "stop_tx", "happened": True, "before": {"visit": "w12"}}}]},
                {"name": "DLQI", "if": [{"event": "stop_tx", "happened": True, "before": {"anchor": "day1"}}]},
                {"name": "Bad", "if": [{"event": "stop_tx", "before": "w12"}]}]),
            visit("et", offset=200, asap_if_past=True), visit("eos", offset=280, asap_if_past=False)],
        events=[{"id": "stop_tx", "label": "Treatment stopped"}]))
    w24 = by_id(out["visits"], "w24")["procedures"]
    assert w24[0]["if"] == [{"not": {"event": "stop_tx", "happened": True, "before": {"visit": "w12", "n": None}}}]
    assert w24[1]["if"] == [{"event": "stop_tx", "happened": True, "before": {"anchor": "day1"}}]
    assert w24[2]["if"] == [{"event": "stop_tx", "before": "w12"}]  # unusable: kept as written, so it shows as depending on it
    assert by_id(out["visits"], "et")["asap_if_past"] is True and "asap_if_past" not in by_id(out["visits"], "eos")
    unknown = normalize(raw_model(visits=raw_model()["visits"] + [visit("w24", procedures=[
        {"name": "Diary", "if": [{"event": "stop_tx", "happened": True, "before": {"visit": "w99"}}]}])],
        events=[{"id": "stop_tx", "label": "Treatment stopped"}]))
    assert any("“w99”" in n and "can never be met" in n for n in unknown["notes"])


def refused(model, raw) -> str:
    with pytest.raises(HTTPException) as error:
        clean_participant(model, raw)
    assert error.value.status_code == 422
    return error.value.detail


def record(**extra) -> dict:
    return {"startDate": "2026-11-02", "answers": {}, **extra}


def test_staff_may_choose_skip_or_reduce_only_when_the_table_has_that_row():
    model = normalize(raw_model())
    out = clean_participant(model, record(checks={"d8#1": [{"date": "2026-11-23", "answer": "skip"}],
                                                  "d8#2": [{"date": "2026-12-14", "answer": "reduce"}]}))
    assert out["checks"]["d8#1"] == [{"date": "2026-11-23", "answer": "skip"}]
    no_reduce = normalize(raw_model({"actions": [TABLE[0], TABLE[2]]}))
    assert "Check answer: Yes, No or Skip," in refused(no_reduce, record(checks={"d8#1": [{"date": "2026-11-23", "answer": "reduce"}]}))
    plain = normalize(raw_model({"actions": [], "limits": [ANC]}))
    assert "Check answer: Yes or No," in refused(plain, record(checks={"d8#1": [{"date": "2026-11-23", "answer": "skip"}]}))


def test_a_recheck_an_event_added_is_answered_on_its_own_key():
    recheck = visit("crcl_re", offset=7, repeat={"every_days": 7, "until_check": True},
                    check={"text": "CrCl ≥ 50?", "limits": [{"measure": "crcl", "op": ">=", "value": 50}]})
    model = normalize(raw_model(events=[{"id": "cis_hold", "label": "Cisplatin held", "visits": [recheck]}]))
    out = clean_participant(model, record(
        events=[{"key": "h1", "type": "cis_hold", "date": "2026-12-01"}],
        checks={"ev.h1.crcl_re#1": [{"date": "2026-12-08", "values": {"crcl": "42"}}]}))
    assert out["checks"] == {"ev.h1.crcl_re#1": [{"date": "2026-12-08", "values": {"crcl": 42}}]}
    assert "has no check" in refused(model, record(checks={"d1#1": [{"date": "2026-12-08", "answer": "yes"}]}))
