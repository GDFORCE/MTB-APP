"""A patient's record on the kit schedule: what clean_participant accepts and refuses.

Imports server through kit_routes, so run it with the local Mongo env vars set
(see MTB_IMPLEMENTATION_HANDOFF.txt); it never touches the database itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.kit import analyze as kit_analyze
from app.kit.normalize import build_template_model, default_review_answers, normalize
import server  # noqa: F401 - kit_routes imports back from server, so server loads first
from kit_routes import clean_participant


@pytest.fixture(scope="module")
def model() -> dict:
    raw = json.loads((Path(kit_analyze.__file__).parent / "samples" / "arc.json").read_text(encoding="utf-8"))["model"]
    base = normalize(raw)
    return build_template_model(base, default_review_answers(base))


def valid(**extra) -> dict:
    return {"startDate": "2026-11-02", "answers": {"seq": "AB", "wocbp": "no"}, **extra}


def refused(model, raw) -> str:
    with pytest.raises(HTTPException) as error:
        clean_participant(model, raw)
    assert error.value.status_code == 422
    return error.value.detail


def test_enrolment_keeps_only_what_the_engine_needs(model):
    out = clean_participant(model, valid(answers={"seq": "BA", "wocbp": "yes", "runin_ok": ""}))
    assert out == {"startAnchor": "screening", "startDate": "2026-11-02", "answers": {"seq": "BA", "wocbp": "yes"},
                   "events": [], "actuals": {}, "doseTimes": {}}


def test_every_participant_question_must_be_answered(model):
    seq = next(q for q in model["questions"] if q["id"] == "seq")
    assert refused(model, valid(answers={"wocbp": "no"})) == f"Answer: {seq['label']}"


def test_answers_must_be_options_of_their_question(model):
    assert "CC" in refused(model, valid(answers={"seq": "CC", "wocbp": "no"}))
    assert "Unknown question" in refused(model, valid(answers={"seq": "AB", "wocbp": "no", "made_up": "1"}))


def test_dates_must_be_real(model):
    assert "YYYY-MM-DD" in refused(model, valid(startDate="2026-02-30"))
    assert "YYYY-MM-DD" in refused(model, valid(actuals={"scr#1": "02/11/2026"}))


def test_recorded_visits_events_and_decisions_are_kept(model):
    out = clean_participant(model, valid(
        answers={"seq": "AB", "wocbp": "no", "runin_ok": "yes"},
        actuals={"scr#1": "2026-11-03"},
        events=[{"type": "exac", "date": "2026-12-01", "answers": {}}, {"key": "e2", "type": "exac", "date": "2026-12-20"}],
    ))
    assert out["answers"]["runin_ok"] == "yes"
    assert out["actuals"] == {"scr#1": "2026-11-03"}
    assert [e["type"] for e in out["events"]] == ["exac", "exac"] and out["events"][1]["key"] == "e2"


def test_unknown_visits_and_repeated_one_off_events_are_refused(model):
    assert "Unknown visit" in refused(model, valid(actuals={"nope#1": "2026-11-03"}))
    assert "Unknown event" in refused(model, valid(events=[{"type": "nope", "date": "2026-12-01"}]))
    assert "only be logged once" in refused(model, valid(events=[
        {"type": "wd", "date": "2026-12-01"}, {"type": "wd", "date": "2026-12-02"}]))
