"""The Protocol Visit Scheduler kit port: PDF input, prompts, normalize and the two-request pipeline.

No network: the model is a fake client that returns a shipped sample, then a correction.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.kit import analyze as kit_analyze
from app.kit.llm import AnalysisError, parse_json_tolerant
from app.kit.normalize import build_template_model, default_review_answers, normalize
from app.kit.pdf_input import (
    PdfReadError, find_schedule_table_pages, is_scanned, prepare_protocol_input, select_pages,
)
from app.kit.prompts import build_prompt, build_review_prompt

SAMPLES = Path(kit_analyze.__file__).parent / "samples"


def sample(name: str = "arc") -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))["model"]


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


def test_normalize_is_stable_and_builds_a_template():
    base = normalize(sample())
    again = normalize(copy.deepcopy(base))
    assert json.dumps(base, sort_keys=True) == json.dumps(again, sort_keys=True)
    model = build_template_model(base, default_review_answers(base))
    assert model["visits"] and {a["kind"] for a in model["anchors"]} >= {"start"}


def test_normalize_rejects_a_model_without_visits():
    with pytest.raises(ValueError):
        normalize({"protocol": {}, "visits": []})


def test_review_answer_patches_change_the_model():
    base = normalize(sample())
    question = base["review_questions"][0]
    patched = [build_template_model(base, {question["id"]: o["value"]}) for o in question["options"]]
    assert patched[0] != patched[1]


def test_parse_json_tolerant_reads_fences_and_surrounding_prose():
    assert parse_json_tolerant('{"a": 1}') == {"a": 1}
    assert parse_json_tolerant('Here you go:\n```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json_tolerant('prefix {"a": 3} suffix') == {"a": 3}
    with pytest.raises(AnalysisError):
        parse_json_tolerant("no json here")


def test_pipeline_applies_cross_check_corrections():
    base = sample()
    first_visit = normalize(copy.deepcopy(base))["visits"][0]
    correction = {"corrections": [{"id": first_visit["id"], "window_after": 9, "reason": "Table 3 says +9"}], "notes": ["n1"]}
    client = FakeClient([base, correction])
    stages: list[tuple] = []
    out = kit_analyze.analyze_protocol(
        {"text": "[[Page 1]]\nx", "tableText": "[[Page 1]]\nx", "tablePages": [1]}, client,
        on_stage=lambda *args: stages.append(args))
    assert out["corrections"] and "Table 3" in out["corrections"][0]
    assert out["baseModel"]["visits"][0]["window_after"] == 9
    assert [s[0] for s in stages if s[1] == "done"] == ["model", "verify", "check"]
    assert "PROTOCOL TEXT:" in client.prompts[0] and "MODEL VISITS:" in client.prompts[1]


def test_pipeline_records_a_skipped_cross_check_but_not_a_credit_failure():
    out = kit_analyze.analyze_protocol(
        {"text": "t", "tableText": "t"}, FakeClient([sample(), AnalysisError("invalid_json", "bad")]),
        on_stage=lambda *_: None)
    assert out["corrections"] is None and out["warnings"]
    with pytest.raises(AnalysisError) as raised:
        kit_analyze.analyze_protocol(
            {"text": "t", "tableText": "t"}, FakeClient([sample(), AnalysisError("no_credits", "none")]),
            on_stage=lambda *_: None)
    assert raised.value.code == "no_credits"


def test_prompts_carry_the_kit_schema_and_rules():
    prompt = build_prompt("[[Page 1]]\nhello")
    assert '"protocol": {"title"' in prompt and "Rules:" in prompt and prompt.endswith("hello")
    review = build_review_prompt(normalize(sample()), "TABLE", [32, 33])
    assert "page 32, 33" in review and "SCHEDULE TABLE TEXT:\nTABLE" in review


def test_schedule_table_page_detection_needs_marks_and_a_heading():
    marks = "Visit  V1 V2 V3\nPhysical exam   X X X\nECG   X X X\n"
    pages = ["intro", "Schedule of Activities\n" + marks, marks, "prose"]
    assert find_schedule_table_pages(pages) == [2, 3]
    assert find_schedule_table_pages(["Schedule of Activities only"]) == []


def test_select_pages_sends_everything_when_small_and_keeps_the_front_when_large():
    assert select_pages(["a", "b"], 1000) == [0, 1]
    pages = [("filler " * 200) for _ in range(40)]
    pages[30] = "visit window day 7 week 4 cycle screening follow-up " * 40
    picked = select_pages(pages, 9000)
    assert {0, 1, 2, 3} <= set(picked) and 30 in picked and len(picked) < 40


def test_a_pdf_without_a_text_layer_is_scanned():
    assert is_scanned(["", "", ""]) is True
    assert is_scanned(["x" * 500] * 10) is False


def test_prepare_reads_a_generated_pdf_with_a_schedule_table():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Schedule of Activities", fontsize=12)
    for line in range(40):  # enough text that the PDF is not treated as a scan
        page.insert_text((72, 300 + line * 12), "The study visits are described in this section of the protocol text. " * 2, fontsize=6)
    for row, label in enumerate(["Physical exam", "ECG", "Vitals"]):
        y = 110 + row * 20
        page.insert_text((72, y), label, fontsize=10)
        for col in range(3):
            page.insert_text((250 + col * 60, y), "X", fontsize=10)
    prepared = prepare_protocol_input(doc.tobytes())
    assert prepared["pageCount"] == 1 and prepared["tablePages"] == [1] and not prepared["scanned"]
    assert "column layout preserved" in prepared["text"]
    assert len(prepared["tableImages"]) == 1 and prepared["tableImages"][0]["mediaType"] == "image/jpeg"


def test_prepare_rejects_a_file_that_is_not_a_pdf():
    with pytest.raises(PdfReadError):
        prepare_protocol_input(b"not a pdf")


def test_the_whole_protocol_is_sent_unless_it_exceeds_the_limit():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for n in range(6):
        page = doc.new_page()
        for line in range(60):
            page.insert_text((72, 60 + line * 11), f"Page {n + 1} visit schedule text line {line} " * 2, fontsize=6)
    data = doc.tobytes()
    whole = prepare_protocol_input(data, budget=2000)
    assert whole["fullText"] and all(f"[[Page {n}]]" in whole["text"] for n in range(1, 7))
    assert whole["selectedText"] != whole["text"]
    capped = prepare_protocol_input(data, budget=2000, full_text_max_chars=1000)
    assert not capped["fullText"] and capped["text"] == capped["selectedText"]


def test_a_too_large_whole_text_falls_back_to_the_page_selection():
    calls = []

    class Client(FakeClient):
        def json(self, prompt, *, images=None, on_text=None):
            calls.append(prompt)
            if len(calls) == 1:
                raise AnalysisError("prompt_too_large", "too big")
            return super().json(prompt, images=images, on_text=on_text)

    out = kit_analyze.analyze_protocol(
        {"text": "WHOLE", "selectedText": "SELECTED", "fullText": True, "tableText": "t"},
        Client([sample(), {"corrections": []}]), on_stage=lambda *_: None)
    assert "WHOLE" in calls[0] and "SELECTED" in calls[1]
    assert any("too long" in w for w in out["warnings"])


def test_a_busy_model_is_retried_then_falls_back(monkeypatch):
    openai = pytest.importorskip("openai")
    from app.kit import llm

    monkeypatch.setattr(llm, "RETRY_WAITS", (0, 0))
    monkeypatch.setenv("KIT_FALLBACK_MODELS", "backup")
    client = llm.OpenAIKitClient(api_key="k", model="main")
    seen: list[str] = []
    statuses: list[str] = []
    client.on_status = statuses.append

    def fake_stream(self, openai_module, model, content, on_text):
        seen.append(model)
        if model == "main":
            raise openai.InternalServerError("busy", response=type("R", (), {"status_code": 503, "headers": {}, "request": None})(), body=None)
        return '{"ok": true}', "stop", None

    monkeypatch.setattr(llm.OpenAIKitClient, "_stream_once", fake_stream)
    assert client.json("p") == {"ok": True}
    assert seen == ["main", "main", "main", "backup"]
    assert any("busy" in s for s in statuses)


def test_a_model_that_stays_busy_ends_with_a_clear_error(monkeypatch):
    openai = pytest.importorskip("openai")
    from app.kit import llm

    monkeypatch.setattr(llm, "RETRY_WAITS", (0,))
    monkeypatch.delenv("KIT_FALLBACK_MODELS", raising=False)

    def always_busy(self, openai_module, model, content, on_text):
        raise openai.APIConnectionError(request=None)

    monkeypatch.setattr(llm.OpenAIKitClient, "_stream_once", always_busy)
    with pytest.raises(AnalysisError) as raised:
        llm.OpenAIKitClient(api_key="k", model="main").json("p")
    assert raised.value.code == "model_busy"


def test_parse_accepts_near_json():
    assert parse_json_tolerant('{"a": "line1\nline2", "b": [1, 2,], /* note */ "c": 3 // x\n}') == {
        "a": "line1\nline2", "b": [1, 2], "c": 3}
    assert parse_json_tolerant('```json\n{"k": 1,}\n```') == {"k": 1}
    with pytest.raises(AnalysisError):
        parse_json_tolerant('{"a": [{"x": 1}, {"y": "tru')


def test_an_unfinished_reply_is_asked_again_and_never_parsed_as_whole(monkeypatch):
    pytest.importorskip("openai")
    from app.kit import llm

    monkeypatch.setattr(llm, "RETRY_WAITS", (0, 0))
    monkeypatch.delenv("KIT_FALLBACK_MODELS", raising=False)
    monkeypatch.setattr(llm, "dump_bad_reply", lambda text: None)
    replies = [('{"visits": [', None, None), ('{"visits": []}', "stop", None)]
    monkeypatch.setattr(llm.OpenAIKitClient, "_stream_once", lambda self, *a: replies.pop(0))
    assert llm.OpenAIKitClient(api_key="k", model="m").json("p") == {"visits": []}
    # a partial reply that happens to be valid JSON, cut off by the connection, is still not accepted
    replies[:] = [('{"visits": []}', None, None)] * 3
    with pytest.raises(AnalysisError) as raised:
        llm.OpenAIKitClient(api_key="k", model="m").json("p")
    assert raised.value.code == "invalid_json"
