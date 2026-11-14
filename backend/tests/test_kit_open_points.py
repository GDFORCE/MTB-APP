"""Schedule fidelity (10 Nov 2026): nothing the AI wrote is dropped quietly, and what the model cannot
carry reaches the reviewer as an open point with the protocol's words and page.

No network: the model is a fake client. Covers the clean-up losses the protocol bench exposed (rules
dropped for the shape of a timing condition, a review question deleted because its answers changed
nothing), the fields added for conditional detail, and the open points themselves.
"""
from __future__ import annotations

import copy

from app.kit import analyze as kit_analyze
from app.kit.ledger import cleanup_points, diff_models, unused_fields
from app.kit.llm import AnalysisError
from app.kit.normalize import add_open_point, build_template_model, normalize
from app.kit.prompts import build_prompt, build_review_prompt


def visit(vid: str, anchor: str = "d1", offset: int = 0, **over) -> dict:
    return {"id": vid, "code": vid.upper(), "name": vid, "phase": "Treatment", "modality": "site", "anchor": anchor,
            "offset_days": offset, "window_before": 0, "window_after": 0, "procedures": [], **over}


def model(**over) -> dict:
    return {"protocol": {}, "arms": [], "questions": [], "review_questions": [], "notes": [], "events": [], "rules": [],
            "anchors": [{"id": "d1", "label": "Day 1", "kind": "start", "is_day1": True}],
            "visits": [visit("w0"), visit("w4", offset=28), visit("w8", offset=56)], **over}


def rule(rid: str, when: dict, **over) -> dict:
    return {"id": rid, "label": rid, "when": when, "stop_visits": [], "effect": f"{rid} effect", "source": "Section 8, p. 7", **over}


EVENT = {"id": "stop", "label": "Study drug stopped", "repeatable": False, "ends_schedule": False, "questions": [], "visits": []}


class FakeClient:
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def json(self, prompt, *, images=None, on_text=None):
        if not self.replies:
            raise AnalysisError("invalid_json", "no scripted reply")
        self.prompts.append(prompt)
        return copy.deepcopy(self.replies.pop(0))


# ── clean-up no longer loses these ───────────────────────────────────────
def test_timing_limits_written_beside_timing_are_read():
    """The shape the prompt used to show: {"timing": {...}, "max_days": -1}. It dropped four rules in the bench."""
    out = normalize(model(events=[EVENT], rules=[
        rule("before_w8", {"event": "stop"}, **{"if": [{"timing": {"visit": "w8", "n": None}, "min_days": None, "max_days": -1}]}),
        rule("from_w8", {"event": "stop"}, **{"if": [{"timing": {"visit": "w8", "min_days": 0}}]}),
    ]))
    assert [r["if"] for r in out["rules"]] == [
        [{"timing": {"visit": "w8", "n": None, "min_days": None, "max_days": -1}}],
        [{"timing": {"visit": "w8", "n": None, "min_days": 0, "max_days": None}}]]
    assert "open_points" not in out


def test_a_rule_that_cannot_be_used_is_kept_for_the_reviewer_in_its_own_words():
    out = normalize(model(events=[EVENT], rules=[
        rule("ghost", {"event": "no_such_event"}, label="Rescue therapy started", quote="Patients who start rescue therapy", page=12),
        rule("bad_if", {"event": "stop"}, label="Branch", **{"if": [{"question": "no_such_question", "in": ["x"]}]}),
        rule("good", {"event": "stop"}, stop_visits=["w8"]),
    ]))
    assert [r["id"] for r in out["rules"]] == ["good"]
    points = {p["id"]: p for p in out["open_points"]}
    ghost = points["rule_ghost"]
    assert ghost["kind"] == "cleanup" and "Rescue therapy started" in ghost["text"] and "no_such_event" in ghost["text"]
    assert "ghost effect" in ghost["text"] and ghost["quote"] == "Patients who start rescue therapy" and ghost["page"] == 12
    assert "Branch" in points["rule_bad_if"]["text"] and "stop" in points["rule_bad_if"]["affects"]
    # Stable: cleaning the cleaned model again changes nothing.
    again = normalize(copy.deepcopy(out))
    assert again["open_points"] == out["open_points"] and again["rules"] == out["rules"]


def test_a_review_question_the_app_cannot_apply_becomes_an_open_point_not_a_deleted_question():
    rq = {"id": "eos", "text": "Is the early EOS visit additional to Week 40?", "why": "Section 10.2.3.5, p. 56 vs Section 7.1, p. 32",
          "recommended": "both", "options": [{"value": "both", "label": "Both visits", "patches": []},
                                             {"value": "early", "label": "Early visit replaces Week 40", "patches": []}]}
    out = normalize(model(review_questions=[rq]))
    assert out["review_questions"] == []
    point = out["open_points"][0]
    assert point["kind"] == "ambiguous" and point["text"] == rq["text"] and "p. 56" in point["why"]
    assert "Both visits" in point["used"] and "Early visit replaces Week 40" in point["used"]


def test_a_review_answer_can_now_change_who_has_a_visit_and_what_a_rule_stops():
    rq = {"id": "eos", "text": "Does the early EOS visit replace Week 8?", "why": "p. 56", "recommended": "keep", "options": [
        {"value": "keep", "label": "Both", "patches": []},
        {"value": "replace", "label": "Replaces it", "patches": [
            {"target": "rule", "id": "stop_rule", "field": "stop_visits", "value": ["w8"]},
            {"target": "visit", "id": "w4", "field": "include_if", "value": [{"question": "arm", "in": ["A"]}]},
            {"target": "event", "id": "stop", "field": "ends_schedule", "value": True}]}]}
    arm = {"id": "arm", "label": "Arm", "text": "Which arm?", "kind": "choice", "options": [{"value": "A", "label": "A"}, {"value": "B", "label": "B"}]}
    base = normalize(model(questions=[arm], events=[EVENT], rules=[rule("stop_rule", {"event": "stop"})], review_questions=[rq]))
    assert [q["id"] for q in base["review_questions"]] == ["eos"]
    chosen = build_template_model(base, {"eos": "replace"})
    assert chosen["rules"][0]["stop_visits"] == ["w8"]
    assert chosen["visits"][1]["include_if"] == [{"question": "arm", "in": ["A"]}]
    assert chosen["events"][0]["ends_schedule"] is True
    assert build_template_model(base, {"eos": "keep"})["rules"][0]["stop_visits"] == []


# ── fields added for conditional detail ──────────────────────────────────
def test_no_window_stated_is_kept_apart_from_an_exact_day():
    out = normalize(model(visits=[
        visit("stated0"), visit("nulls", window_before=None, window_after=None),
        {k: v for k, v in visit("absent").items() if not k.startswith("window")}, visit("w4", offset=28, window_before=3, window_after=3)]))
    flags = {v["id"]: v.get("window_stated") for v in out["visits"]}
    # Only an explicit "not stated" is flagged; models saved before carry no flag and stay as they were.
    assert flags == {"stated0": None, "nulls": False, "absent": None, "w4": None}
    assert [v["window_before"] for v in out["visits"]] == [0, 0, 0, 3]
    assert normalize(copy.deepcopy(out))["visits"][1]["window_stated"] is False


def test_a_date_with_its_own_day_and_a_tolerance_stays_a_counted_date():
    anchors = lambda stated: [  # noqa: E731
        {"id": "d1", "label": "Day 0", "kind": "start"},
        {"id": "dose2", "label": "Second vaccination", "kind": "relative", "relative_to": "d1", "offset_days": 28,
         "range": {"relative_to": "d1", "min_days": 28, "max_days": 35}, **({"offset_stated": True} if stated else {})}]
    kept = normalize(model(anchors=anchors(True)))["anchors"][1]
    assert kept["kind"] == "relative" and kept["offset_stated"] is True and kept["range"]["max_days"] == 35
    # Only a range: entered per participant, as before.
    asked = normalize(model(anchors=anchors(False)))["anchors"][1]
    assert asked["kind"] == "ask" and "offset_stated" not in asked
    assert asked["estimates"] == [{"when": [], "relative_to": "d1", "offset_days": 28}]


def test_rules_can_stop_and_add_assessments_and_the_last_dose_can_name_its_assessment():
    out = normalize(model(
        anchors=[{"id": "d1", "label": "Day 1", "kind": "start"},
                 {"id": "last_dose", "label": "Last dose", "kind": "last_visit", "last_of": ["w0", "w4", "nope"], "last_with": ["Injection", ""]},
                 {"id": "other", "label": "Other", "kind": "ask", "last_with": ["Injection"]}],
        events=[EVENT],
        rules=[rule("stop_rule", {"event": "stop"}, quote="  no further injections  ", page="7",
                    stop_procedures=[{"visits": "all", "names": ["Injection"]}, {"visits": ["w8", "nope"], "names": ["DLQI", ""]},
                                     {"visits": ["nope"], "names": ["X"]}, {"visits": ["w4"], "names": []}],
                    add_procedures=[{"visits": ["w8"], "procedures": ["Off-treatment questionnaire", {"name": ""}]}])]))
    r = out["rules"][0]
    assert r["stop_procedures"] == [{"visits": "all", "names": ["Injection"]}, {"visits": ["w8"], "names": ["DLQI"]}]
    assert r["add_procedures"] == [{"visits": ["w8"], "procedures": [{"name": "Off-treatment questionnaire", "if": []}]}]
    assert r["quote"] == "no further injections" and r["page"] == 7
    last = out["anchors"][1]
    assert last["last_of"] == ["w0", "w4"] and last["last_with"] == ["Injection"]
    assert "last_with" not in out["anchors"][2]


def test_instructions_and_time_points_keep_their_conditions():
    pk = {"question": "pk", "in": "yes"}
    out = normalize(model(
        questions=[{"id": "pk", "label": "PK", "text": "PK sub-study?", "kind": "boolean"}],
        visits=[visit("w0", instructions=[
            {"text": " Dose after a high-fat breakfast ", "if": [{"question": "seq", "in": ["BA"]}], "kind": "prep", "source": "3.1"},
            {"text": "", "if": []}, "Questionnaires first", {"text": "x", "kind": "nonsense"}],
            timepoints=[{"label": "2 h", "offset_min": 120, "procedures": [{"name": "PK sample", "if": [pk]}], "if": [pk]},
                        {"label": "4 h", "offset_min": 240, "procedures": ["ECG"]}])]))
    v = out["visits"][0]
    assert v["instructions"] == [
        {"text": "Dose after a high-fat breakfast", "if": [{"question": "seq", "in": ["BA"]}], "kind": "prep", "source": "3.1"},
        {"text": "Questionnaires first", "if": []}, {"text": "x", "if": []}]
    assert v["timepoints"][0]["if"] == [{"question": "pk", "in": ["yes"]}]
    assert v["timepoints"][0]["procedures"][0]["if"] == [{"question": "pk", "in": ["yes"]}]
    assert "if" not in v["timepoints"][1]
    # The condition on a question nobody is asked is reported, as for assessments.
    assert "instructions" not in normalize(model())["visits"][0]


def test_event_visits_can_count_from_another_date_and_depend_on_the_events_answers():
    event = {**EVENT, "questions": [{"id": "attends", "label": "Attends", "text": "Will attend?", "kind": "boolean"}], "ends_schedule": True,
             "keeps_visits": ["eos", "other_ev_visit", "nope"],
             "visits": [visit("eos", count_from="last_dose", offset=28, include_if=[{"event_question": "attends", "in": "yes"}]),
                        visit("call", count_from="no_such_date")]}
    other = {"id": "ae", "label": "AE", "questions": [], "visits": [visit("other_ev_visit")]}
    out = normalize(model(anchors=[{"id": "d1", "label": "Day 1", "kind": "start"},
                                   {"id": "last_dose", "label": "Last dose", "kind": "last_visit", "last_of": ["w0", "w4"]}],
                          events=[event, other]))
    eos, call = out["events"][0]["visits"]
    assert eos["count_from"] == "last_dose" and "count_from" not in call
    assert eos["include_if"] == [{"event_question": "attends", "in": ["yes"]}]
    assert out["events"][0]["keeps_visits"] == ["eos", "other_ev_visit"]
    assert not any("can never be met" in n for n in out["notes"])


# ── the ledger: what clean-up changed, and fields the app has no use for ──
def test_the_ledger_reports_what_clean_up_changed_but_not_defaults():
    raw = model(
        visits=[visit("w0", follows_occurrence="same", procedures=[{"name": "ECG", "need": "required"}]),
                visit("w4", anchor="randomisation", offset=28, follows="ghost_visit", window_before=-3, window_after=3)],
        events=[{**EVENT, "keeps_visits": ["ghost_visit"], "invented": "x"}],
        rules=[rule("stop_rule", {"event": "stop"}, stop_visits=["w4", "ghost_visit"])])
    before = copy.deepcopy(raw)
    after = normalize(raw)
    texts = [p["text"] for p in cleanup_points(before, after)]
    joined = " | ".join(texts)
    # A visit moved to the start date because its date does not exist is the dangerous one.
    assert "visit W4 was to count from a date the model does not have (“randomisation”)" in joined
    assert "“follows”" in joined and "ghost_visit" in joined
    assert "“stop_visits”" in joined and "“keeps_visits”" in joined
    # Defaults and type clean-up are not reported: follows_occurrence "same", need "required", a signed window.
    assert not any(x in joined for x in ("follows_occurrence", "need", "window_before"))
    assert all(p["kind"] == "cleanup" for p in cleanup_points(before, after))
    assert diff_models(after, normalize(copy.deepcopy(after))) == []


def test_fields_the_app_has_no_use_for_are_reported():
    raw = model(visits=[visit("w0", fasting_required=True, repeat={"every_days": 7, "count": 2, "skip_weekends": True})],
                rules=[rule("r", {"anchor": "d1"}, delay_visits={"w4": 7})], study_calendar="business days")
    raw["anchors"][0]["weekday"] = "Monday"
    texts = " | ".join(p["text"] for p in unused_fields(raw))
    for key in ("fasting_required", "skip_weekends", "delay_visits", "study_calendar", "weekday"):
        assert f"“{key}”" in texts, key
    assert unused_fields(model()) == []


# ── the pipeline: every stage's open points, quotes looked up in the PDF ──
PAGES = ["[[Page 1]]\nVisits take place once every calendar month, on the same day of the month as Day 1.",
         "[[Page 2]]\nPatients who discontinue study drug continue to attend the scheduled visits without injections. "
         "ECGs are recorded at Screening and at Week 24."]


def test_open_points_from_every_stage_reach_the_result_with_their_quotes_checked():
    first = model(
        events=[EVENT],
        rules=[rule("stop_rule", {"event": "stop"}, stop_procedures=[{"visits": "all", "names": ["Injection"]}],
                    quote="Patients who discontinue study drug continue to attend the scheduled visits without injections.", page=1),
               rule("made_up", {"event": "stop"}, **{"if": [{"occurrence": 2}]}, quote="A second discontinuation ends the study for that patient.", page=2)],
        open_points=[
            {"kind": "cannot_represent", "text": "Monthly visits are on the same calendar day, not every 30 days.",
             "quote": "once every calendar month, on the same day of the month as Day 1", "page": 1, "affects": ["w4", "w8"], "used": "30 days per month"},
            {"kind": "not_stated", "text": "No window is given for Week 8.", "quote": "The Week 8 visit has no window whatsoever stated here", "page": 2}],
        weekday_rule="first Monday")
    check = {"corrections": [{"id": "w4", "window_after": 2, "reason": "Table 1"}, {"id": "ghost", "offset_days": 3, "reason": "Table 1 column 9"},
                             {"id": "w8", "window_after": 0, "reason": "already so"}],
             "questions_add": [{"id": "sex", "label": "Sex", "text": "Is the participant female?", "kind": "boolean"}],
             "open_points": [{"kind": "conflict", "text": "ECG: the table marks Week 12, the text says Week 24.",
                              "quote": "ECGs are recorded at Screening and at Week 24.", "page": 2, "affects": ["w8"], "used": "The table is followed."}]}
    events = {"events": [], "open_points": [{"kind": "ambiguous", "text": "Whether a patient who withdraws attends the last visit is not said.",
                                             "quote": "continue to attend the scheduled visits", "page": 2}]}
    client = FakeClient([first, check, events])
    out = kit_analyze.analyze_protocol({"text": "\n\n".join(PAGES), "tableText": PAGES[0], "tablePages": [1], "pageTexts": PAGES},
                                       client, on_stage=lambda *a: None)
    points = out["openPoints"]
    assert out["baseModel"]["open_points"] == points and out["model"]["open_points"] == points
    assert out["counts"]["openPoints"] == len(points)
    by_text = {p["text"]: p for p in points}
    calendar = by_text["Monthly visits are on the same calendar day, not every 30 days."]
    assert calendar["found"] is True and calendar["stage"] == "model" and calendar["affects"] == ["w4", "w8"]
    # A quote that is not in the PDF is marked, never trusted silently.
    assert by_text["No window is given for Week 8."]["found"] is False
    # Found on another page than the one given: accepted, with the real page.
    assert out["baseModel"]["rules"][0]["quote_found"] is True and out["baseModel"]["rules"][0]["page"] == 2
    kinds = {(p["kind"], p["stage"]) for p in points}
    assert {("cannot_represent", "model"), ("conflict", "tables"), ("ambiguous", "events"), ("cleanup", "cleanup")} <= kinds
    texts = " | ".join(p["text"] for p in points)
    assert "“weekday_rule”" in texts  # a field the app has no use for
    assert "ghost (no such visit)" in texts  # a table correction that could not be applied
    assert "already so" not in texts  # one that changed nothing is not a problem
    assert by_text["ECG: the table marks Week 12, the text says Week 24."]["found"] is True
    assert [q["id"] for q in out["baseModel"]["questions"]] == ["sex"]
    assert out["baseModel"]["visits"][1]["window_after"] == 2
    assert len({p["id"] for p in points}) == len(points)


def test_a_rule_whose_quote_is_not_in_the_pdf_is_flagged_but_still_applied():
    first = model(events=[EVENT], rules=[rule("stop_rule", {"event": "stop"}, stop_visits=["w8"], quote="Treatment is stopped for good after one event.", page=2)])
    out = kit_analyze.analyze_protocol({"text": "\n\n".join(PAGES), "tableText": PAGES[0], "tablePages": [1], "pageTexts": PAGES},
                                       FakeClient([first, {"corrections": []}]), on_stage=lambda *a: None)
    assert [r["id"] for r in out["baseModel"]["rules"]] == ["stop_rule"]
    point = next(p for p in out["openPoints"] if p["kind"] == "unverified")
    assert point["affects"] == ["stop_rule"] and point["found"] is False and "was not found in the PDF text" in point["text"]


def test_a_model_with_nothing_to_check_carries_no_open_points_and_ticks_are_kept():
    assert "open_points" not in normalize(model())
    st = normalize(model())
    add_open_point(st, {"kind": "conflict", "text": "A"})
    add_open_point(st, {"kind": "conflict", "text": "A"})  # the same point twice is one point
    add_open_point(st, {"kind": "nonsense", "text": "B", "page": "12", "checked": True, "affects": ["w4", "w4"]})
    assert [(p["id"], p["kind"], p["page"], p.get("checked"), p["affects"]) for p in st["open_points"]] == [
        ("op1", "conflict", None, None, []), ("op2", "cannot_represent", 12, True, ["w4"])]
    assert normalize(copy.deepcopy(st))["open_points"] == st["open_points"]


def test_the_prompts_ask_for_conditions_as_fields_and_for_open_points():
    prompt = build_prompt("[[Page 1]]\nx")
    for needle in ('"open_points"', '"instructions"', '"stop_procedures"', '"offset_stated"', '"last_with"', '"count_from"',
                   'min_days and max_days go INSIDE "timing"', "never in its label", "Never leave such a point only in notes"):
        assert needle in prompt, needle
    review = build_review_prompt(normalize(model(events=[{**EVENT, "visits": [visit("et")]}])), "[[Page 1]]\nx", [])
    assert '"questions_add"' in review and '"open_points"' in review and '"id":"et"' in review
