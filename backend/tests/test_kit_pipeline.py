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
    PdfReadError, is_scanned, prepare_protocol_input, select_pages,
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
    # No drawn lines: found from its lined-up marks, not from the heading.
    assert prepared["pageCount"] == 1 and prepared["tablePages"] == [1] and not prepared["scanned"]
    assert prepared["tableSource"] == "aligned" and prepared["alignedTables"][0]["total"] == 9
    assert "schedule table without ruled lines" in prepared["text"]
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


def test_full_text_above_the_old_character_cap_keeps_every_page(monkeypatch):
    fitz = pytest.importorskip("fitz")
    monkeypatch.delenv("KIT_FULL_TEXT_MAX_CHARS", raising=False)

    class Page:
        def get_text(self, mode):
            return "ordinary document content " * 6000 if mode == "text" else {"blocks": []}

    class Document:
        page_count = 6
        closed = False

        def __getitem__(self, index):
            return Page()

        def close(self):
            self.closed = True

    doc = Document()
    monkeypatch.setattr(fitz, "open", lambda **kwargs: doc)
    prepared = prepare_protocol_input(b"mock pdf", budget=2000)
    assert len(prepared["text"]) > 650_000
    assert prepared["fullText"] and prepared["picked"] == list(range(6))
    assert "[[Page 6]]" in prepared["text"] and doc.closed


def test_a_too_large_protocol_summarizes_every_section_then_checks_original_tables():
    from app.kit.summarize import split_sections

    pages = [f"[[Page {i}]]\n" + "ordinary content " * 6000 for i in range(1, 4)]
    pages[-1] += "\nFootnote 19: stop Period 1 after Injection 2."
    source = "\n\n".join(pages)
    count = len(split_sections(source))
    summaries = [{"summary": f"Page references and scheduling facts from section {i}"} for i in range(count)]
    client = FakeClient([AnalysisError("prompt_too_large", "too big"), *summaries, sample(), {"corrections": []}])
    stages = []
    out = kit_analyze.analyze_protocol(
        {"text": "WHOLE", "selectedText": "SELECTED", "fullText": True,
         "pageTexts": pages, "tableText": "ORIGINAL TABLE: X means blood sample"},
        client, on_stage=lambda *args: stages.append(args))
    section_prompts = client.prompts[1:count + 1]
    assert all(f"[[Page {i}]]" in "\n".join(section_prompts) for i in range(1, 4))
    assert "Footnote 19: stop Period 1 after Injection 2." in "\n".join(section_prompts)
    assert "SELECTED" not in "\n".join(client.prompts)
    assert "SCHEDULING NOTES FROM ALL PROTOCOL SECTIONS" in client.prompts[-2]
    assert "ORIGINAL TABLE: X means blood sample" in client.prompts[-1]
    assert out["baseModel"]["visits"] and out["warnings"]
    assert any("summaries" in n for n in out["baseModel"]["notes"])
    assert any(s[0] == "summarize" and s[1] == "run" and "section 2 of" in s[2] for s in stages)
    assert [s[0] for s in stages if s[1] == "done"] == ["summarize", "model", "verify", "check"]


def test_summary_sections_keep_boundary_text_and_page_references():
    from app.kit.summarize import split_sections

    lines = [f"Line {i}: unique assessment and timing facts\n" for i in range(300)]
    sections = split_sections("[[Page 15]]\n" + "".join(lines), limit=2200)
    assert len(sections) > 1
    assert all(s.startswith("[[Page 15]]") for s in sections)
    assert all(any(line in section for section in sections) for line in lines)


def test_a_summary_section_that_is_too_large_is_split_without_dropping_the_end():
    from app.kit.summarize import _summarize_section

    class Client:
        def __init__(self):
            self.processed = []

        def json(self, prompt):
            section = prompt.split("SECTION TEXT:\n", 1)[1]
            if len(section) > 5000:
                raise AnalysisError("prompt_too_large", "too big")
            self.processed.append(section)
            return {"summary": "Source-grounded notes"}

    client = Client()
    text = "[[Page 20]]\n" + "timing detail " * 800 + "\nFINAL FOOTNOTE"
    progress = []
    notes = _summarize_section(text, client, progress.append)
    assert len(notes) > 1 and progress
    assert all("[[Page 20]]" in part for part in client.processed)
    assert "FINAL FOOTNOTE" in client.processed[-1]


def test_summaries_are_condensed_again_if_the_combined_notes_exceed_context():
    from app.kit.summarize import analyze_from_summaries

    client = FakeClient([
        {"summary": "Detailed facts with page references. " * 100},
        AnalysisError("prompt_too_large", "still big"),
        {"summary": "[[Page 1]] Visit on Day 7; window UNRESOLVED."},
        {"visits": ["result"]},
    ])
    stages = []
    result = analyze_from_summaries("source text " * 1000, client, build_prompt,
                                    on_stage=lambda *args: stages.append(args))
    assert result == {"visits": ["result"]}
    assert "Detailed facts with page references." in client.prompts[2]
    assert "window UNRESOLVED" in client.prompts[3]
    assert any("pass 2" in str(s[2]) for s in stages)


@pytest.mark.parametrize("reply", [{}, {"summary": ""}, {"summary": ["not text"]}])
def test_invalid_summaries_cannot_silently_drop_a_section(reply):
    from app.kit.summarize import analyze_from_summaries

    client = FakeClient([reply])
    with pytest.raises(AnalysisError) as raised:
        analyze_from_summaries("source", client, build_prompt, on_stage=lambda *_: None)
    assert raised.value.code == "invalid_json" and len(client.prompts) == 1


def test_summary_reduction_cannot_retry_forever_without_shrinking():
    from app.kit.summarize import analyze_from_summaries

    client = FakeClient([{"summary": "longer than the input"}, AnalysisError("prompt_too_large", "too big")])
    with pytest.raises(AnalysisError) as raised:
        analyze_from_summaries("source", client, build_prompt, on_stage=lambda *_: None)
    assert raised.value.code == "summary_failed" and len(client.prompts) == 2


def test_an_authentication_failure_does_not_start_summarizing():
    client = FakeClient([AnalysisError("auth", "bad key")])
    stages = []
    with pytest.raises(AnalysisError) as raised:
        kit_analyze.analyze_protocol({"text": "whole", "fullText": True}, client,
                                    on_stage=lambda *args: stages.append(args))
    assert raised.value.code == "auth"
    assert all(s[0] != "summarize" for s in stages)


@pytest.mark.parametrize("message, code, expected", [
    ("Maximum context length exceeded", "context_length_exceeded", "prompt_too_large"),
    ("Maximum input length exceeded", None, "prompt_too_large"),
    ("Too many input tokens", None, "prompt_too_large"),
    ("Input is too long", "input_too_long", "prompt_too_large"),
    ("Unsupported model", None, "upstream_error"),
])
def test_model_input_limit_errors_trigger_the_summary_path(monkeypatch, message, code, expected):
    import httpx
    openai = pytest.importorskip("openai")
    from app.kit.llm import OpenAIKitClient

    def rejected(*args):
        response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
        raise openai.BadRequestError(message, response=response, body={"code": code})

    monkeypatch.setattr(OpenAIKitClient, "_stream_once", rejected)
    with pytest.raises(AnalysisError) as raised:
        OpenAIKitClient(api_key="test", model="fake").json("protocol")
    assert raised.value.code == expected


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


def _with_rules(rules: list) -> dict:
    model = normalize(sample())
    model["rules"] = rules
    return model


def test_rules_keep_what_the_protocol_says_and_drop_unknown_references():
    model = normalize(sample())
    visit_ids = [v["id"] for v in model["visits"]]
    anchor_id = model["anchors"][-1]["id"]
    event = model["events"][0]
    out = normalize(_with_rules([
        {"id": "switch", "label": "Second period starts", "when": {"anchor": anchor_id},
         "stop_visits": visit_ids[-2:] + ["no_such_visit"], "same_day": "cancel", "starts": anchor_id,
         "blocks": ["ends", "missing", "switch"], "allowed": {"relative_to": anchor_id, "min_days": "29", "max_days": None},
         "effect": "Later visits stop.", "source": "p. 23"},
        {"id": "ends", "label": "Withdrawal", "when": {"event": event["id"]}, "stop_all": True, "effect": "Ends."},
        {"id": "ghost", "label": "Unknown trigger", "when": {"event": "nope"}},
    ]))["rules"]
    assert [r["id"] for r in out] == ["switch", "ends"]
    switch, ends = out
    assert switch["stop_visits"] == visit_ids[-2:]
    assert switch["same_day"] == "cancel" and switch["starts"] == anchor_id
    assert switch["blocks"] == ["ends"]
    assert switch["allowed"] == {"relative_to": anchor_id, "min_days": 29, "max_days": None, "not_on_visit": None}
    assert ends["same_day"] == "keep" and ends["stop_visits"] == [] and ends["allowed"] is None
    # An event rule that ends participation ends the schedule the same way the event flag does.
    assert next(e for e in normalize(_with_rules(out))["events"] if e["id"] == event["id"])["ends_schedule"] is True


def test_rules_survive_a_second_normalize_and_the_template_build():
    model = normalize(sample())
    rules = [{"id": "r", "label": "x", "when": {"anchor": model["anchors"][0]["id"]}, "stop_visits": [model["visits"][-1]["id"]]}]
    base = normalize(_with_rules(rules))
    assert normalize(copy.deepcopy(base))["rules"] == base["rules"]
    assert build_template_model(base, default_review_answers(base))["rules"] == base["rules"]


def test_the_cross_check_asks_for_missing_rules_and_adds_them():
    base = sample()
    review = build_review_prompt(normalize(copy.deepcopy(base)), "TABLE", [])
    assert "rules_add" in review and "do not complete further Period 1 visits" in review
    first = normalize(copy.deepcopy(base))
    added = {"id": "late", "label": "Footnote rule", "when": {"anchor": first["anchors"][0]["id"]},
             "stop_visits": [first["visits"][-1]["id"]], "effect": "Stops the last visit.", "source": "footnote 13"}
    out = kit_analyze.analyze_protocol(
        {"text": "t", "tableText": "t"}, FakeClient([base, {"corrections": [], "rules_add": [added, {"label": "no id"}]}]),
        on_stage=lambda *_: None)
    assert [r["id"] for r in out["baseModel"]["rules"]][-1] == "late"
    assert "Rule added: Footnote rule" in out["corrections"]
    assert out["model"]["rules"][-1]["source"] == "footnote 13"


def test_the_model_prompt_asks_for_every_if_this_happens_rule():
    prompt = build_prompt("x")
    assert '"rules": [{"id": string' in prompt and "stop_visits" in prompt
    assert "write one rule for EVERY statement" in prompt


# ── Schedule tables found by structure ──────────────────────────────────────

def _ruled_table(page, rows, *, x0=72, y0=100, col_w=70, row_h=18, merged_header=None, fontsize=8):
    """Draw a ruled table; ``merged_header`` = (first, last) columns of row 0 drawn as one cell."""
    cols = len(rows[0])
    for r in range(len(rows) + 1):
        page.draw_line((x0, y0 + r * row_h), (x0 + cols * col_w, y0 + r * row_h))
    for c in range(cols + 1):
        top = y0
        if merged_header and merged_header[0] < c <= merged_header[1]:
            top = y0 + row_h  # no line inside the merged header cell
        page.draw_line((x0 + c * col_w, top), (x0 + c * col_w, y0 + len(rows) * row_h))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                page.insert_text((x0 + c * col_w + 3, y0 + r * row_h + 12), text, fontsize=fontsize)


def _filler(page, top=420):
    for line in range(30):  # enough text that the PDF is not treated as a scan
        page.insert_text((72, top + line * 10), "Narrative protocol text describing the study in some detail. " * 2, fontsize=6)


def test_mark_score_counts_a_repeated_short_mark_but_not_numbers_or_words():
    from app.kit.pdf_input import mark_score
    marks = [["Visit", "V1", "V2", "V3"], ["ECG", "X", "X4", ""], ["Vitals", "Xa", "", "X*"], ["Labs", "", "X", "X"]]
    assert mark_score(marks) == 6  # footnotes glued to a mark count as the mark
    circles = [["", "D1", "D2"], ["Consent", "○", ""], ["ECG", "○", "○"], ["Labs", "○", "○"]]
    assert mark_score(circles) == 5
    numbers = [["Outcome", "A (%)", "B (%)"], ["Death", "1.1", "1.4"], ["MI", "0.5", "0.6"], ["Stroke", "0.5", "0.5"]]
    assert mark_score(numbers) == 0
    contents = [["1.", "Introduction", "....", "4"], ["2.", "Objectives", "....", "6"], ["3.", "Design", "....", "8"]]
    assert mark_score(contents) == 0
    prose = [["and the", "of the", "to"], ["and", "of", "to"], ["and", "of", "to"]]
    assert mark_score(prose) == 0


def test_a_ruled_schedule_without_a_heading_or_x_marks_is_found_by_structure():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    page = doc.new_page()
    page.insert_text((72, 80), "Table 4: Study visits", fontsize=10)  # no "schedule of activities" heading
    _ruled_table(page, [["Assessment", "Screen", "Day 1", "Week 4"], ["Consent", "o", "", ""],
                        ["ECG", "o", "o", "o"], ["Vitals", "o", "o", "o"], ["Labs", "o", "", "o"]])
    page.insert_text((72, 230), "a Footnote: labs are fasting.", fontsize=8)
    _filler(page)
    _filler(doc[0], top=80)
    prepared = prepare_protocol_input(doc.tobytes())
    assert prepared["tablePages"] == [2] and prepared["tableSource"] == "structure"
    block = prepared["pageTexts"][1]
    assert block.startswith("[[Page 2 · schedule table as a grid]]")
    assert "| ECG | o | o | o |" in block and "| Consent | o |  |  |" in block
    assert block.index("Table 4") < block.index("| Assessment") < block.index("Footnote")
    assert "schedule table as a grid" in prepared["tableText"] and len(prepared["tableImages"]) == 1


def test_merged_cells_say_which_neighbour_they_belong_to():
    fitz = pytest.importorskip("fitz")
    from app.kit.pdf_input import schedule_tables
    doc = fitz.open()
    page = doc.new_page()
    _ruled_table(page, [["", "Treatment", "", "Follow-up"], ["Assessment", "D1", "D8", "D30"],
                        ["ECG", "X", "X", "X"], ["Vitals", "X", "X", ""], ["Labs", "X", "", "X"]],
                 merged_header=(1, 2))
    (table,) = schedule_tables(page)
    assert table["grid"][0][1:] == ["Treatment", "←", "Follow-up"]
    assert table["grid"][2] == ["ECG", "X", "X", "X"]


def test_ruled_and_borderless_tables_are_both_found_without_matching_any_words():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    _ruled_table(page, [["Assessment", "V1", "V2", "V3"], ["ECG", "X", "X", "X"], ["Vitals", "X", "X", "X"]])
    _filler(page)
    plain = doc.new_page()  # marks lined up in columns, no lines and no heading
    for row, label in enumerate(["Exam", "ECG"]):
        plain.insert_text((72, 110 + row * 20), label, fontsize=10)
        for col in range(3):
            plain.insert_text((250 + col * 60, 110 + row * 20), "X", fontsize=10)
    _filler(plain)
    prepared = prepare_protocol_input(doc.tobytes())
    assert prepared["tablePages"] == [1, 2] and prepared["tableSource"] == "structure + aligned"
    assert [t["page"] for t in prepared["alignedTables"]] == [2]


def test_symbol_font_characters_are_translated_by_their_font():
    from app.kit.pdf_input import symbol_map

    class Page:
        def get_text(self, mode):
            spans = [{"font": "Wingdings-Regular", "text": "\uf0fc"}, {"font": "SymbolMT", "text": "\uf0b1 3 \uf061"},
                     {"font": "Wingdings2", "text": "\uf050"}, {"font": "Arial", "text": "\uf0fb"}]
            return {"blocks": [{"lines": [{"spans": spans}]}]}

    table = symbol_map(Page())
    assert "\uf0fc \uf0b1 3 \uf061 \uf050 \uf0fb".translate(table) == "✓ ± 3 α \uf050 \uf0fb"


def test_the_cross_check_adds_the_pages_the_model_names(monkeypatch):
    fitz = pytest.importorskip("fitz")
    from app.kit.pdf_input import cross_check_material
    doc = fitz.open()
    for _ in range(4):
        doc.new_page()
    prepared = {
        "pageCount": 4, "tablePages": [2], "tableText": "[[Page 2]] grid",
        "pageTexts": [f"[[Page {n}]] text {n}" for n in range(1, 5)],
        "tableImages": [{"page": 2, "mediaType": "image/jpeg", "base64": "x"}], "pdfBytes": doc.tobytes(),
    }
    material = cross_check_material(prepared, [3, 2, 99, True])
    assert material["pages"] == [2, 3]
    assert "text 3" in material["text"] and "text 1" not in material["text"]
    assert [i["page"] for i in material["images"]] == [2, 3] and material["images"][0]["base64"] == "x"
    monkeypatch.setenv("KIT_TABLE_IMAGE_LIMIT", "1")
    assert [i["page"] for i in cross_check_material(prepared, [3])["images"]] == [2]  # detector pages first
    assert cross_check_material(prepared, [])["text"] == "[[Page 2]] grid"


def test_the_model_names_schedule_pages_and_they_reach_the_cross_check():
    prompt = build_prompt("x")
    assert '"schedule_table_pages"' in prompt and "schedule table as a grid" in prompt
    base = sample()
    base["schedule_table_pages"] = [3, "4", "bad"]
    client = FakeClient([base, {"corrections": []}])
    prepared = {"text": "t", "tableText": "[[Page 2]] t2", "tablePages": [2], "pageCount": 5,
                "pageTexts": [f"[[Page {n}]] t{n}" for n in range(1, 6)]}
    out = kit_analyze.analyze_protocol(prepared, client, on_stage=lambda *_: None)
    assert "schedule_table_pages" not in out["baseModel"]
    assert out["counts"]["tablePages"] == [2, 3, 4]
    assert "[[Page 3]] t3" in client.prompts[1] and "[[Page 4]] t4" in client.prompts[1]
    assert "schedule table as a grid" in client.prompts[1]


# ── Symbol legend: any symbol, shown as printed ────────────────────────────

def test_any_short_mark_counts_including_new_symbols_and_plus():
    from app.kit.pdf_input import mark_score, mark_word
    assert [mark_word(c) for c in ("X", "X4", "+", "◉", "\uf050", "Y", "Day 1", "12", "--", "and")] == [
        "X", "X", "+", "◉", "\uf050", "Y", "Day", None, None, None]
    unseen = [["", "V1", "V2"], ["ECG", "◉", "◉"], ["Labs", "◉", ""], ["Vitals", "◉", "◉"]]
    assert mark_score(unseen) == 5


class _RawPage:
    """A page whose rawdict holds a symbol the PDF gives no character for."""

    def __init__(self, spans):
        self.spans = spans

    def get_text(self, mode):
        return {"blocks": [{"lines": [{"spans": self.spans}]}]}


def _span(text, font, x=100, y=100):
    chars = [{"c": c, "bbox": (x + 6 * i, y, x + 6 * (i + 1), y + 10)} for i, c in enumerate(text)]
    return {"font": font, "chars": chars, "bbox": (x, y, x + 6 * len(text), y + 10)}


def test_unreadable_symbols_from_any_font_get_a_name_and_a_legend_row():
    from app.kit.pdf_input import SymbolLegend
    legend = SymbolLegend()
    page = _RawPage([_span("Visit by \uf028 phone", "ABCDEF+Wingdings2"), _span("\ufffd", "Custom"),
                     _span("\uf0fc", "Wingdings")])
    names = legend.record(page, 0, {0xF0FC: "✓"}, [])
    assert "Visit by \uf028 phone \ufffd \uf0fc".translate({**names, 0xF0FC: "✓"}) == "Visit by [S1] phone [S2] ✓"
    again = legend.record(_RawPage([_span("\uf028", "Wingdings2")]), 3, {}, [])
    assert again == {0xF028: "[S1]"}  # the same symbol keeps its name across pages
    rows = legend.chosen()
    assert [(r["shown"], r["font"], r["count"]) for r in rows] == [("[S1]", "Wingdings2", 2), ("[S2]", "Custom", 1)]
    note = legend.note(rows)
    assert "1. written [S1]" in note and "font Wingdings2" in note and "ask a review question" in note


def test_table_marks_are_shown_as_printed_because_text_can_spell_them_wrongly():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    # A Wingdings tick stored as "ü" (a dingbat font with no Unicode mapping)
    _ruled_table(page, [["Assessment", "V1", "V2", "V3"], ["Consent", "ü", "", ""], ["ECG", "ü", "ü", "ü"],
                        ["Vitals", "ü", "ü", "ü"], ["Labs", "ü", "", "ü"]])
    _filler(page)
    prepared = prepare_protocol_input(doc.tobytes())
    legend = prepared["symbolLegend"]
    assert prepared["tableSource"] == "structure"
    assert "1. a schedule-table mark written “ü”" in legend["note"]
    image = legend["image"]
    assert image["mediaType"] == "image/png" and image["label"].startswith("Symbol legend image")
    assert fitz.open(stream=__import__("base64").b64decode(image["base64"]), filetype="png").page_count == 1


def test_a_protocol_with_nothing_unusual_has_no_legend():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    _filler(doc.new_page())
    assert prepare_protocol_input(doc.tobytes())["symbolLegend"] is None


def test_the_legend_goes_to_the_model_build_and_the_cross_check():
    class ImageClient(FakeClient):
        def __init__(self, replies):
            super().__init__(replies)
            self.images: list = []

        def json(self, prompt, *, images=None, on_text=None):
            self.images.append([i.get("label") or i.get("page") for i in images or []])
            return super().json(prompt, images=images, on_text=on_text)

    legend = {"note": "SYMBOL LEGEND. test note", "image": {"page": "legend", "label": "Symbol legend image", "base64": "x"}}
    client = ImageClient([sample(), {"corrections": []}])
    prepared = {"text": "t", "tableText": "t", "tablePages": [1], "pageCount": 1,
                "tableImages": [{"page": 1, "base64": "p"}], "symbolLegend": legend}
    kit_analyze.analyze_protocol(prepared, client, on_stage=lambda *_: None)
    assert all("SYMBOL LEGEND. test note" in p for p in client.prompts)
    assert client.images == [["Symbol legend image"], [1, "Symbol legend image"]]


# ── Tables without cell borders: read from the image, checked against the page ──

def _borderless_pdf(rows):
    """A page with a schedule laid out by spacing only. ``rows`` = [(label, [col indexes marked])]."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    xs = [220, 290, 360, 430]
    page.insert_text((72, 90), "Assessment", fontsize=10)
    for x, head in zip(xs, ["Screening", "Day 1", "Week 4", "Week 8"]):
        page.insert_text((x - 8, 90), head, fontsize=10)
    for r, (label, marked) in enumerate(rows):
        y = 120 + r * 22
        page.insert_text((72, y), label, fontsize=10)
        for c in marked:
            page.insert_text((xs[c], y), "X", fontsize=10)
    _filler(page, top=300)
    return doc.tobytes()


ROWS = [("Consent", [0]), ("ECG", [0, 1, 2, 3]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])]
COLUMNS = ["Screening", "Day 1", "Week 4", "Week 8"]


def _reading(rows=ROWS):
    return {"columns": COLUMNS, "rows": [{"label": label, "cells": ["X" if c in marked else "" for c in range(4)]}
                                          for label, marked in rows], "footnotes": ["a fasting"]}


class TableClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, list]] = []

    def json(self, prompt, *, images=None, on_text=None):
        self.calls.append((prompt, [i.get("label") for i in images or []]))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


def test_a_borderless_table_is_counted_per_row_and_column():
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    (table,) = prepared["alignedTables"]
    assert table["total"] == 11
    assert [(r["label"], r["count"]) for r in table["rows"]] == [("Consent", 1), ("ECG", 4), ("Vitals", 4), ("Labs", 2)]
    assert [(c["header"], c["count"]) for c in table["columns"]] == [
        ("Screening", 4), ("Day 1", 2), ("Week 4", 3), ("Week 8", 2)]


def test_a_correct_reading_is_accepted_first_time_and_becomes_a_grid():
    from app.kit.pdf_input import READ_LABEL
    from app.kit.tables import read_borderless_tables
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    client = TableClient([_reading()])
    out = read_borderless_tables(prepared, client, on_stage=lambda *_: None)
    assert out == {"flags": [], "notes": [], "warnings": []} and len(client.calls) == 1
    assert client.calls[0][1] == ["Page 1 image:"]
    assert f"[[Page 1 · {READ_LABEL}]]" in prepared["text"] and "| ECG | X | X | X | X |" in prepared["text"]
    assert "Footnotes: a fasting" in prepared["tableText"]


def test_a_misread_is_rechecked_with_the_exact_rows_and_a_zoomed_view():
    from app.kit.tables import read_table
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    wrong = _reading([("Consent", [0]), ("ECG", [0, 1, 2]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])])
    client = TableClient([wrong, _reading()])
    result = read_table(prepared["alignedTables"][0], prepared["pdfBytes"], client)
    assert result["agreed"] and result["attempts"] == 2 and not result.get("countDoubted")
    recheck_prompt, images = client.calls[1]
    assert 'row "ECG" has 4 marks on the page; you read 3' in recheck_prompt
    assert images == ["Page 1 image:", "Zoomed view of the table on page 1:"]


def test_the_same_reading_twice_is_kept_but_still_asked_about():
    from app.kit.tables import read_table, review_flag
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    wrong = _reading([("Consent", [0]), ("ECG", [0, 1, 2]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])])
    client = TableClient([wrong, wrong])
    result = read_table(prepared["alignedTables"][0], prepared["pdfBytes"], client)
    assert result["agreed"] and result["countDoubted"] and len(client.calls) == 2
    flag = review_flag(1, result)
    assert flag["id"] == "table_check_p1" and "confirmed on a second look" in flag["why"]


def test_rows_still_wrong_after_two_rechecks_are_the_only_thing_flagged():
    from app.kit.tables import read_borderless_tables
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    first = _reading([("Consent", [0]), ("ECG", [0]), ("Vitals", [0, 1]), ("Labs", [0, 2])])
    second = _reading([("Consent", [0]), ("ECG", [0, 1]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])])
    third = _reading([("Consent", [0]), ("ECG", [0, 1, 2]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])])
    client = TableClient([first, second, third])
    out = read_borderless_tables(prepared, client, on_stage=lambda *_: None)
    assert len(client.calls) == 3  # first reading + 2 rechecks
    (flag,) = out["flags"]
    assert flag["text"] == "Check page 1: do these rows match the table? ECG"
    assert "ECG: page shows 4 marks, read 3" in flag["why"]
    assert "Vitals" not in flag["text"]  # rows that match are trusted
    assert "p. " not in flag["text"]  # the review popup splits sentences at ". "
    assert "| ECG | X | X | X |  |" in prepared["text"]  # the closest reading is used


def test_a_borderless_table_flows_through_the_whole_pipeline_and_saves():
    """The new step must not disturb anything after it: model build, cross-check, review answers, saving."""
    from app.kit.pdf_input import READ_LABEL
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    wrong = _reading([("Consent", [0]), ("ECG", [0]), ("Vitals", [0, 1, 2, 3]), ("Labs", [0, 2])])
    client = TableClient([wrong, wrong, sample(), {"corrections": []}])
    stages: list[tuple] = []
    out = kit_analyze.analyze_protocol(prepared, client, on_stage=lambda *a: stages.append(a))
    assert [s[0] for s in stages if s[1] == "done"] == ["tables", "model", "verify", "check"]
    build_prompt_text = client.calls[2][0]
    assert f"[[Page 1 · {READ_LABEL}]]" in build_prompt_text  # the model build sees the reading
    flag = next(q for q in out["model"]["review_questions"] if q["id"] == "table_check_p1")
    assert flag["options"][0]["value"] == "unsure" and out["reviewAnswers"]["table_check_p1"] == "unsure"
    assert out["counts"]["review"] == len(out["baseModel"]["review_questions"])
    # Saving (PUT /trials/{id}/kit-model) normalises the base model and applies the answers.
    base = normalize(copy.deepcopy(out["baseModel"]))
    saved = build_template_model(base, {**out["reviewAnswers"], "table_check_p1": "checked"})
    assert saved["visits"] == out["model"]["visits"]
    assert any(q["id"] == "table_check_p1" for q in saved["review_questions"])


def test_a_table_that_cannot_be_read_falls_back_to_text_but_credits_stop_everything():
    from app.kit.tables import read_borderless_tables
    prepared = prepare_protocol_input(_borderless_pdf(ROWS))
    before = prepared["text"]
    out = read_borderless_tables(prepared, TableClient([AnalysisError("upstream_error", "boom")]),
                                 on_stage=lambda *_: None)
    assert out["warnings"] and "could not be read" in out["warnings"][0] and prepared["text"] == before
    with pytest.raises(AnalysisError):
        read_borderless_tables(prepare_protocol_input(_borderless_pdf(ROWS)),
                               TableClient([AnalysisError("no_credits", "none")]), on_stage=lambda *_: None)


def test_a_letter_after_a_mark_is_a_footnote_only_when_the_bare_mark_is_used_too():
    from app.kit.pdf_input import merge_letter_footnotes, table_marks
    assert merge_letter_footnotes(["X", "Xa", "No", "NE"]) == {"X": "X", "Xa": "X", "No": "No", "NE": "NE"}
    response = [["SD", "No", "SD"], ["PD", "No", "PD"], ["Any", "No", "PD"], ["CR", "No", "CR"]]
    assert "N" not in table_marks(response, min_cols=1)  # "No" is a word, not "N" + footnote "o"


def test_a_sideways_page_is_read_as_it_is_seen():
    """On a page printed sideways, "0.8 x 10e9" is not a standalone mark "x"."""
    fitz = pytest.importorskip("fitz")
    from app.kit.pdf_input import aligned_table
    doc = fitz.open()
    page = doc.new_page()
    for r in range(6):
        y = 100 + r * 22
        page.insert_text((72, y), f"Lab {r}", fontsize=9)
        for c in range(3):
            page.insert_text((180 + c * 120, y), f"<{r}.{c} x 10e9 /L", fontsize=9)
    page.set_rotation(90)
    assert aligned_table(doc.reload_page(page)) is None


def _not_found(openai):
    import httpx
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.NotFoundError("model retired", response=httpx.Response(404, request=request), body=None)


def test_a_retired_model_is_skipped_not_fatal(monkeypatch):
    openai = pytest.importorskip("openai")
    from app.kit import llm

    monkeypatch.setattr(llm, "RETRY_WAITS", (0, 0))
    monkeypatch.setenv("KIT_FALLBACK_MODELS", "retired,backup")
    client = llm.OpenAIKitClient(api_key="k", model="main")
    seen: list[str] = []
    statuses: list[str] = []
    client.on_status = statuses.append

    def fake_stream(self, openai_module, model, content, on_text):
        seen.append(model)
        if model == "main":
            raise openai.APIConnectionError(request=None)
        if model == "retired":
            raise _not_found(openai)
        return '{"ok": true}', "stop", None

    monkeypatch.setattr(llm.OpenAIKitClient, "_stream_once", fake_stream)
    assert client.json("p") == {"ok": True}
    assert seen == ["main", "main", "main", "retired", "backup"]  # a missing model is not retried
    assert "retired is not available, trying another model" in statuses


def test_when_no_configured_model_exists_the_error_says_so(monkeypatch):
    openai = pytest.importorskip("openai")
    from app.kit import llm

    monkeypatch.delenv("KIT_FALLBACK_MODELS", raising=False)
    monkeypatch.setattr(llm.OpenAIKitClient, "_stream_once",
                        lambda self, o, model, content, on_text: (_ for _ in ()).throw(_not_found(openai)))
    with pytest.raises(AnalysisError) as raised:
        llm.OpenAIKitClient(api_key="k", model="gone").json("p")
    assert raised.value.code == "upstream_error" and "None of the configured models exist" in str(raised.value)


def test_pdfs_read_at_the_same_time_give_the_same_tables_as_one_at_a_time():
    """PyMuPDF is not thread-safe: uploads read in parallel must not lose tables."""
    pytest.importorskip("fitz")
    from concurrent.futures import ThreadPoolExecutor
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(6):
        page = doc.new_page()
        _ruled_table(page, [["Assessment", "V1", "V2", "V3"], ["ECG", "X", "X", "X"], ["Vitals", "X", "X", "X"],
                            ["Labs", "X", "", "X"]])
        _filler(page)
    data = doc.tobytes()
    alone = prepare_protocol_input(data)["tablePages"]
    with ThreadPoolExecutor(4) as pool:
        together = list(pool.map(lambda _: prepare_protocol_input(data)["tablePages"], range(4)))
    assert alone == [1, 2, 3, 4, 5, 6] and together == [alone] * 4


# ── Cross-check corrections: only real changes are claimed ────────────────

def _two_anchor_model():
    m = normalize(sample())
    anchors = [a["id"] for a in m["anchors"]]
    assert len(anchors) >= 2
    return m, anchors


def test_an_offset_moves_with_its_new_anchor_never_onto_the_old_one():
    """CG03: "offset 854 days and changed anchor to first_treatment_day1" must apply both or neither."""
    m, anchors = _two_anchor_model()
    v = m["visits"][-1]
    other = next(a for a in anchors if a != v["anchor"])
    kit_analyze.apply_corrections(m, {"corrections": [{"id": v["id"], "anchor": other, "offset_days": 854,
                                                       "reason": "6 months after F10"}]})
    assert (v["anchor"], v["offset_days"]) == (other, 854)
    assert f"anchor" in m["review"][0] and "offset_days" in m["review"][0] and "→ 854" in m["review"][0]

    m2, _ = _two_anchor_model()
    v2 = m2["visits"][-1]
    before = (v2["anchor"], v2["offset_days"])
    n = kit_analyze.apply_corrections(m2, {"corrections": [{"id": v2["id"], "anchor": "no_such_anchor",
                                                            "offset_days": 854, "reason": "6 months after F10"}]})
    assert (v2["anchor"], v2["offset_days"]) == before and n == 0
    assert "timing left unchanged" in m2["notes"][-1] and "no_such_anchor" in m2["notes"][-1]


def test_a_correction_that_changes_nothing_is_a_note_not_a_claimed_fix():
    m = normalize(sample())
    v = next(x for x in m["visits"] if x.get("repeat")) if any(x.get("repeat") for x in m["visits"]) else m["visits"][0]
    n = kit_analyze.apply_corrections(m, {"corrections": [
        {"id": v["id"], "reason": "Page 9 shows 6 sessions, not 7"},   # nothing it can apply
        {"id": "ghost", "reason": "not a visit"},
    ]})
    assert n == 0 and m["review"] == []
    assert "Page 9 shows 6 sessions" in m["notes"][-1] and "ghost (no such visit)" in m["notes"][-1]


def test_the_cross_check_can_now_change_a_repeat_count():
    m = normalize(sample("helix"))
    v = next(x for x in m["visits"] if x.get("repeat"))
    target = (v["repeat"].get("count") or 3) + 2
    kit_analyze.apply_corrections(m, {"corrections": [{"id": v["id"], "repeat_count": target, "reason": "more cycles"}]})
    assert v["repeat"]["count"] == target and f"→ {target}" in m["review"][0]


# ── A table running over pages keeps its header; the cross-check knows where anchors are ──

def _continued_pdf(second_columns=4):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    first = doc.new_page()
    _filler(first, top=80)
    # Header and one row at the foot of page 1: too few marks to count on their own.
    _ruled_table(first, [["Assessment", "Screening", "Day 1", "Week 4"], ["Consent", "X", "", ""]], y0=700)
    second = doc.new_page()
    rows = [["ECG", "X", "X", "X"], ["Vitals", "X", "X", "X"], ["Labs", "X", "", "X"]]
    if second_columns != 4:
        rows = [r + ["X"] for r in rows]
    _ruled_table(second, rows, y0=60)
    _filler(second, top=300)
    return doc.tobytes()


def test_a_table_continued_from_the_page_before_gets_its_header_repeated():
    prepared = prepare_protocol_input(_continued_pdf())
    assert prepared["tablePages"] == [1, 2]
    page2 = prepared["pageTexts"][1]
    assert "| Assessment (header repeated from page 1) | Screening | Day 1 | Week 4 |" in page2
    assert page2.index("header repeated") < page2.index("| ECG |")
    assert "| Assessment | Screening | Day 1 | Week 4 |" in prepared["pageTexts"][0]


def test_a_different_table_on_the_page_before_is_not_joined():
    prepared = prepare_protocol_input(_continued_pdf(second_columns=5))
    assert prepared["tablePages"] == [2] and "header repeated" not in prepared["text"]


def test_the_cross_check_is_told_where_each_anchor_is_and_where_each_visit_falls():
    m = normalize({"anchors": [
        {"id": "week_1", "label": "Week 1", "kind": "start"},
        {"id": "week_8_post", "label": "Week 8", "kind": "relative", "relative_to": "week_1", "offset_days": 49},
        {"id": "progression", "label": "Progression", "kind": "ask"}],
        "visits": [{"id": "W8", "code": "W8", "name": "Week 8", "anchor": "week_8_post", "offset_days": 56},
                   {"id": "PD", "code": "PD", "name": "Progression visit", "anchor": "progression", "offset_days": 0}]})
    review = build_review_prompt(m, "TABLE", [])
    assert "- week_8_post (Week 8): = week_1 + 49 days (day 49 after the start)" in review
    assert "- progression (Progression): a date entered when it happens" in review
    assert '"day_from_start":105' in review  # 49 + 56: the error the cross-check must now see
    assert "offset_days counts from the visit's OWN anchor" in review
    assert '"code":"PD"' in review and '"day_from_start"' not in review.split('"code":"PD"')[1].split("}")[0]


# ── Rule conditions ("if"): branches of one event ─────────────────────────

def _branchy(rules):
    return {
        "questions": [{"id": "arm", "text": "Arm?", "kind": "choice", "scope": "participant",
                       "options": [{"value": "A", "label": "A"}, {"value": "B", "label": "B"}]}],
        "anchors": [{"id": "day1", "label": "Day 1", "kind": "start"}, {"id": "stopped", "label": "Stopped", "kind": "ask"}],
        "visits": [{"id": "c3", "code": "C3", "name": "Cycle 3", "anchor": "day1", "offset_days": 56},
                   {"id": "safe", "code": "SAFE", "name": "Safety", "anchor": "stopped", "offset_days": 30}],
        "events": [{"id": "stop", "label": "Stopped", "sets_anchor": "stopped",
                    "questions": [{"id": "grade", "text": "Grade?", "kind": "choice", "options": ["1", "3"]}]}],
        "rules": rules,
    }


def test_every_kind_of_rule_condition_is_kept_in_a_clean_form():
    rules = [{"id": "r", "label": "Branch", "when": {"event": "stop"}, "stop_visits": ["c3"], "if": [
        {"question": "arm", "in": "A"},
        {"event_question": "grade", "in": [3]},
        {"timing": {"visit": "c3", "max_days": -1}},
        {"timing": {"anchor": "day1", "min_days": "0", "max_days": 83}},
        {"occurrence": 2},
    ]}]
    (rule,) = normalize(_branchy(rules))["rules"]
    assert rule["if"] == [
        {"question": "arm", "in": ["A"]},
        {"event_question": "grade", "in": ["3"]},
        {"timing": {"visit": "c3", "n": None, "min_days": None, "max_days": -1}},
        {"timing": {"anchor": "day1", "n": None, "min_days": 0, "max_days": 83}},
        {"occurrence": 2},
    ]


@pytest.mark.parametrize("bad", [
    {"question": "no_such_question", "in": ["A"]},
    {"event_question": "no_such_question", "in": ["3"]},
    {"timing": {"visit": "no_such_visit", "max_days": -1}},
    {"timing": {"visit": "c3"}},          # no limits: would always hold
    {"occurrence": 0},
    {"something": "else"},
])
def test_a_rule_with_an_unusable_condition_is_dropped_never_kept_without_it(bad):
    rules = [{"id": "r", "label": "Branch", "when": {"event": "stop"}, "stop_visits": ["c3"], "if": [bad]}]
    model = normalize(_branchy(rules))
    assert model["rules"] == []  # without its condition it would act for every branch
    assert any('Rule "Branch" was left out' in n for n in model["notes"])
    assert normalize(copy.deepcopy(model))["notes"] == model["notes"]  # noted once, not on every pass


def test_a_conditional_end_of_everything_does_not_end_the_schedule_for_every_case():
    conditional = [{"id": "r", "label": "Arm B ends", "when": {"event": "stop"}, "stop_all": True,
                    "if": [{"question": "arm", "in": ["B"]}]}]
    assert normalize(_branchy(conditional))["events"][0]["ends_schedule"] is False
    always = [{"id": "r", "label": "Ends", "when": {"event": "stop"}, "stop_all": True}]
    assert normalize(_branchy(always))["events"][0]["ends_schedule"] is True


def test_the_prompts_ask_for_one_conditional_rule_per_branch():
    prompt = build_prompt("x")
    assert '"if": [condition]' in prompt and "never chooses between branches" in prompt
    review = build_review_prompt(normalize(_branchy([])), "TABLE", [])
    assert '"if": [condition]' in review and "tell them apart" in review
