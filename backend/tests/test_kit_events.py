"""The events check (app/kit/events.py): a separate request that reads the whole protocol for what
logging each event changes, merged into the model in shapes the schedule engine applies.

No network: the model is a fake client with scripted replies.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.kit import analyze as kit_analyze
from app.kit.events import (
    CHECKLIST, apply_event_check, build_events_prompt, page_words, run_event_check, verify_quote,
)
from app.kit.llm import AnalysisError
from app.kit.normalize import build_template_model, normalize
from app.kit.summarize import split_sections

SAMPLES = Path(kit_analyze.__file__).parent / "samples"

PAGES = [
    "[[Page 1]]\nA randomized crossover study of an inhaled treatment.",
    "[[Page 42]]\n8.3 Pregnancy\nSubjects who become pregnant must discontinue study drug imme-\ndiately and will be "
    "followed until delivery. A urine pregnancy test is done at each remaining visit. The investigator must "
    "report the pregnancy to the sponsor within 24 hours.",
    "[[Page 50]]\n8.4 Withdrawal\nSubjects may withdraw consent at any time without giving a reason.",
]


def base() -> dict:
    return normalize(json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"])


class FakeClient:
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def json(self, prompt, *, images=None, on_text=None):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


PREGNANCY = {
    "id": "preg", "label": "Pregnancy", "checklist": "pregnancy", "status": "changes",
    "summary": "Study drug stops, later dosing visits are cancelled, pregnancy is followed until delivery.",
    "evidence": [{"page": 42, "quote": "Subjects who become pregnant must discontinue study drug immediately and "
                                       "will be followed until delivery."}],
    "ends_schedule": True, "repeatable": False,
    "questions": [{"id": "preg_continue", "label": "Continuing", "text": "Is the participant continuing?",
                   "kind": "boolean"}],
    "visits": [{"id": "preg_out", "code": "PREG-OUT", "name": "Pregnancy outcome call", "modality": "phone",
                "offset_days": 280, "window_before": 14, "window_after": 14, "procedures": ["Pregnancy outcome"]}],
    "rules": [{"id": "preg_stop", "label": "Pregnancy stops dosing", "when": {"event": "preg"},
               "stop_visits": ["p1d28", "p2d1"], "same_day": "keep", "effect": "Dosing visits stop.",
               "source": "Section 8.3, p. 42"}],
    "add_tests": [{"name": "Urine pregnancy test", "visits": "all", "source": "p. 42"}],
    "remove_tests": [{"name": "Spirometry", "visits": "all", "source": "p. 42"}],
    "site_actions": ["Report the pregnancy to the sponsor within 24 hours"],
}


def test_the_prompt_asks_about_every_model_event_and_the_checklist_with_exact_quotes():
    prompt = build_events_prompt(base(), "\n\n".join(PAGES))
    assert all(cid in prompt for cid, _ in CHECKLIST)
    assert '"id":"preg"' in prompt and '"id":"wd"' in prompt  # the model's events, with ids to reuse
    assert "MODEL SCHEDULED VISITS:" in prompt and '"id":"p1d28"' in prompt
    assert "character for character" in prompt and "not_in_protocol" in prompt
    assert "site_actions" in prompt and "add_tests" in prompt and "remove_tests" in prompt
    assert "[[Page 42]]" in prompt and "PROTOCOL TEXT:" in prompt


def test_a_pregnancy_check_cancels_visits_adds_visits_and_tests_and_keeps_site_actions():
    st = base()
    out = apply_event_check(st, {"events": [PREGNANCY]}, PAGES)
    st = normalize(st)
    preg = next(e for e in st["events"] if e["id"] == "preg")
    assert [v["code"] for v in preg["visits"]][-1] == "PREG-OUT" and preg["visits"][-1]["modality"] == "phone"
    assert preg["ends_schedule"] and preg["repeatable"] is False
    assert any(q["id"] == "preg_continue" for q in preg["questions"])
    assert preg["site_actions"] == ["Report the pregnancy to the sponsor within 24 hours"]
    assert preg["check"]["status"] == "changes" and preg["check"]["evidence"][0]["found"] is True
    rule = next(r for r in st["rules"] if r["id"] == "preg_stop")
    assert rule["when"] == {"event": "preg"} and rule["stop_visits"] == ["p1d28", "p2d1"]
    # A test after the event is a condition on what happened by each visit's date.
    for v in st["visits"]:
        test = next(p for p in v["procedures"] if p["name"] == "Urine pregnancy test")
        assert test["if"] == [{"event": "preg", "happened": True}]
    spiro = next(p for p in next(v for v in st["visits"] if v["id"] == "p1d28")["procedures"] if p["name"] == "Spirometry")
    assert {"not": {"event": "preg", "happened": True}} in spiro["if"]
    assert "Pregnancy: adds visit PREG-OUT" in out["changes"] and "Rule added: Pregnancy stops dosing" in out["changes"]
    assert any("Urine pregnancy test" in c for c in out["changes"])
    # It survives the template build the reviewer's answers go through.
    tmpl = build_template_model(st, {})
    assert any(r["id"] == "preg_stop" for r in tmpl["rules"])
    assert next(e for e in tmpl["events"] if e["id"] == "preg")["check"]["status"] == "changes"


def test_gaps_are_written_down_never_left_silent():
    st = base()
    reply = {"events": [
        PREGNANCY,
        {"id": "wd", "status": "no_schedule_change", "checklist": "consent_withdrawal",
         "evidence": [{"page": 50, "quote": "Withdrawal is followed by a telephone call on day 30 after the last dose"}]},
        {"id": "death", "checklist": "death", "status": "not_in_protocol", "evidence": []},
    ]}
    apply_event_check(st, reply, PAGES)
    notes = "\n".join(st["notes"])
    assert "The passage quoted for “Early withdrawal” (p. 50) was not found in the PDF text" in notes
    assert "The events check did not report on: Asthma exacerbation." in notes  # skipped events are named
    assert "Not mentioned in the protocol, according to the events check: death." in notes
    assert "did not say whether the protocol covers" in notes and "Lost to follow-up" in notes
    # "Changes nothing" against a model that ends the schedule on it is a conflict, never a fact.
    assert "says logging “Early withdrawal” changes nothing, but the model ends the schedule" in notes
    assert next(e for e in st["events"] if e["id"] == "wd")["ends_schedule"] is True
    assert not any(e["id"] == "death" for e in st["events"])  # nothing invented for what the protocol lacks


def test_a_named_event_with_no_schedule_change_is_stated_with_its_page():
    st = base()
    reply = {"events": [{"id": "lost", "label": "Lost to follow-up", "checklist": "lost_to_follow_up",
                         "status": "no_schedule_change",
                         "evidence": [{"page": 50, "quote": "Subjects may withdraw consent at any time"}]}]}
    apply_event_check(st, reply, PAGES)
    assert "Logging “Lost to follow-up” does not change the schedule, according to the protocol (p. 50)." in st["notes"]


def test_existing_rules_and_visits_are_kept_unless_named_with_a_reason():
    st = base()
    apply_event_check(st, {"events": [PREGNANCY]}, PAGES)
    again = copy.deepcopy(PREGNANCY)
    again["rules"] = [{**PREGNANCY["rules"][0], "stop_visits": ["p1d28"]}]  # same id: replaced, not doubled
    again["visits"] = []
    apply_event_check(st, {"events": [again]}, PAGES)
    assert [r["id"] for r in st["rules"]].count("preg_stop") == 1
    assert next(r for r in st["rules"] if r["id"] == "preg_stop")["stop_visits"] == ["p1d28"]
    preg = next(e for e in st["events"] if e["id"] == "preg")
    assert any(v["id"] == "preg_out" for v in preg["visits"])  # not listed again, still kept
    again["remove_visits"] = [{"id": "preg_out", "reason": "The protocol has no outcome call"}]
    again["remove_rules"] = [{"id": "preg_stop", "reason": "Dosing continues"}]
    out = apply_event_check(st, {"events": [again]}, PAGES)
    assert not any(v["id"] == "preg_out" for v in preg["visits"])
    assert not any(r["id"] == "preg_stop" for r in st["rules"])
    assert any("removed (The protocol has no outcome call)" in c for c in out["changes"])
    # The same test is not added twice.
    p1 = next(v for v in st["visits"] if v["id"] == "p1d1")
    assert [p["name"] for p in p1["procedures"]].count("Urine pregnancy test") == 1


def test_a_new_event_with_its_rule_and_a_new_date_is_added():
    st = base()
    reply = {"anchors_add": [{"id": "delivery", "label": "Delivery date", "kind": "relative"}], "events": [{
        "id": "sae", "label": "Serious adverse event", "checklist": "serious_adverse_event", "status": "changes",
        "evidence": [{"page": 42, "quote": "The investigator must report the pregnancy to the sponsor within 24 hours"}],
        "visits": [{"id": "sae_fu", "code": "SAE-FU", "name": "SAE follow-up", "offset_days": 7}],
        "rules": [{"id": "sae_hold", "label": "SAE holds dosing", "stop_visits": ["p2d1"], "effect": "Holds dosing"}],
    }]}
    apply_event_check(st, reply, PAGES)
    st = normalize(st)
    sae = next(e for e in st["events"] if e["id"] == "sae")
    assert sae["visits"][0]["code"] == "SAE-FU"
    assert next(r for r in st["rules"] if r["id"] == "sae_hold")["when"] == {"event": "sae"}
    delivery = next(a for a in st["anchors"] if a["id"] == "delivery")
    assert delivery["kind"] == "ask"  # a date the protocol counts from is entered, never computed


def test_quotes_are_matched_word_by_word_across_line_breaks_and_pages():
    pages = page_words(PAGES)
    hit = verify_quote(42, "Subjects who become pregnant must discontinue study drug immediately", pages)
    assert hit["found"] is True and hit["found_on"] == 42  # "imme-\ndiately" in the PDF text
    moved = verify_quote(1, "Subjects may withdraw consent at any time", pages)
    assert moved["found"] is True and moved["found_on"] == 50
    assert verify_quote(42, "pregnant subjects", pages)["found"] is False  # too short to prove anything
    assert verify_quote(42, "Subjects who become pregnant continue dosing", pages)["found"] is False
    assert verify_quote(42, "anything at all here", {})["found"] is None  # no text layer


def test_the_pipeline_runs_the_events_check_after_the_cross_check():
    raw = json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"]
    client = FakeClient([raw, {"corrections": []}, {"events": [PREGNANCY]}])
    stages: list[tuple] = []
    out = kit_analyze.analyze_protocol(
        {"text": "\n\n".join(PAGES), "pageTexts": PAGES, "tableText": "t", "tablePages": []}, client,
        on_stage=lambda *a: stages.append(a))
    assert [s[0] for s in stages if s[1] == "done"] == ["model", "verify", "events", "check"]
    detail = next(s[2] for s in stages if s[0] == "events" and s[1] == "done")
    assert "events checked" in detail and "applied" in detail
    assert "CHECKLIST:" in client.prompts[2] and "[[Page 42]]" in client.prompts[2]
    assert out["eventCheck"]["ran"] and out["eventCheck"]["changes"]
    assert out["baseModel"]["event_check"]["ran"]
    assert any(r["id"] == "preg_stop" for r in out["model"]["rules"])


def test_a_failed_events_check_is_recorded_but_a_credit_failure_stops_the_analysis():
    raw = json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"]
    prepared = {"text": "t", "pageTexts": PAGES, "tableText": "t"}
    out = kit_analyze.analyze_protocol(
        prepared, FakeClient([raw, {"corrections": []}, AnalysisError("invalid_json", "bad")]), on_stage=lambda *_: None)
    assert out["eventCheck"]["ran"] is False and any("Events check skipped" in w for w in out["warnings"])
    assert any("The events check could not run" in n for n in out["baseModel"]["notes"])
    with pytest.raises(AnalysisError) as raised:
        kit_analyze.analyze_protocol(
            prepared, FakeClient([raw, {"corrections": []}, AnalysisError("no_credits", "none")]), on_stage=lambda *_: None)
    assert raised.value.code == "no_credits"


def test_a_protocol_too_large_for_one_request_is_read_in_parts_and_merged():
    pages = [f"[[Page {i}]]\n" + "ordinary protocol text " * 150 for i in range(1, 5)]
    text = "\n\n".join(pages)
    parts = split_sections(text, len(text) // 2)
    first = {"events": [{**PREGNANCY, "status": "no_schedule_change", "rules": [], "add_tests": [], "remove_tests": []}]}
    later = {"events": [{**PREGNANCY, "evidence": [{"page": 3, "quote": "ordinary protocol text ordinary protocol text"}]}]}
    client = FakeClient([AnalysisError("prompt_too_large", "too big"), first, *([later] * (len(parts) - 1))])
    st = base()
    out = run_event_check({"pageTexts": pages, "text": text}, st, client, on_stage=lambda *_: None)
    assert len(client.prompts) == 1 + len(parts)
    assert "part 1 of" in client.prompts[1]
    preg = next(e for e in out["events"] if e["id"] == "preg")
    assert preg["status"] == "changes" and len(preg["evidence"]) >= 2  # any part's change counts
    assert any(r["id"] == "preg_stop" for r in st["rules"])


def test_a_scanned_protocol_is_not_checked_and_says_so():
    st = base()
    client = FakeClient([])
    assert run_event_check({"scanned": True, "text": "(no text)"}, st, client, on_stage=lambda *_: None) is None
    assert not client.prompts and st["event_check"]["ran"] is False
    assert any("needs a text layer" in n for n in st["notes"])
