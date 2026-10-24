from copy import deepcopy
from contextlib import closing
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prototypes.extraction_lab.cases import CASES, BY_ID
from prototypes.extraction_lab.client import RequestBudget, StructuredGeminiClient
from prototypes.extraction_lab.core import extract, load_document, reader_limits, replay
from prototypes.extraction_lab.gold import Expectations, score
from prototypes.extraction_lab.schemas import response_schema, stage_for_prompt
from app.extraction.artifacts import MemoryArtifactStore
from app.extraction.reader import METADATA_PROMPT, _facts_prompt, REPAIR_FACTS_PROMPT
from app.extraction.contracts import DocumentPage
from app.extraction.llm_client import ExtractionModelError


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_synthetic_case_through_real_pipeline(case):
    report = replay(case)
    assert report["status"] == "COMPLETED", report.get("error")
    assert report["score"]["status"] == "PASS", report["score"]
    assert report["mode"] == "OFFLINE_FIXTURE_REPLAY"
    assert report["requests"] == []
    for fact in report["facts"]["facts"]:
        assert all(c["verification"] == "TEXT_MATCHED" for c in fact["evidence"])
    assert all(result["schedule"]["schedule_metadata"]["status"] == "EXTRACTED"
               for result in report["results"] if result["schedule"])


@pytest.mark.parametrize("stage", ["metadata", "facts", "repair"])
def test_response_schema_is_valid_and_cannot_set_verification(stage):
    schema = response_schema(stage)
    Draft202012Validator.check_schema(schema)
    assert "verification" not in schema.get("$defs", {}).get("Citation", {}).get("properties", {})
    if stage == "metadata":
        assert "title" in schema["$defs"]["TrialMetadata"]["properties"]
    else:
        name = "ScheduleFact" if stage == "facts" else "RepairFact"
        assert "provenance" not in schema["$defs"][name]["properties"]


class FakeSDK:
    def __init__(self, case=None, *, text=None, finish="STOP", error=None):
        self.models = self
        self.case = case or CASES[0]
        self.text, self.finish, self.error = text, finish, error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        prompt = kwargs["contents"][-1]
        value = self.case.metadata if stage_for_prompt(prompt) == "metadata" else self.case.reply
        return SimpleNamespace(text=self.text if self.text is not None else json.dumps(value),
                               candidates=[SimpleNamespace(finish_reason=self.finish)], usage_metadata=None)


def client(sdk, *, constrained=True, maximum=8):
    return StructuredGeminiClient(api_key="test-placeholder-key", model="test-model",
                                  budget=RequestBudget(maximum=maximum, interval_seconds=0),
                                  constrained=constrained, sdk_client=sdk)


def test_schema_adapter_uses_schema_on_every_call_and_does_not_send_gold():
    sdk = FakeSDK()
    c = client(sdk)
    case = CASES[0]
    report = extract(case.source.encode(), "case.txt", client=c, expected=case.expected)
    assert report["score"]["status"] == "PASS"
    assert len(sdk.calls) == 2
    assert all(call["config"].response_json_schema for call in sdk.calls)
    assert all(call["config"].response_mime_type == "application/json" for call in sdk.calls)
    assert all("required_issue_codes" not in str(call["contents"]) for call in sdk.calls)
    assert all(r["schema_valid"] for r in report["requests"])


def test_baseline_cache_is_separate_from_constrained_mode():
    cache = MemoryArtifactStore()
    case = CASES[0]
    sdk = FakeSDK()
    for constrained in (True, False):
        c = client(sdk, constrained=constrained)
        report = extract(case.source.encode(), "case.txt", client=c, artifacts=cache, expected=case.expected)
        assert report["score"]["status"] == "PASS"
        assert len(report["requests"]) == 2
    assert len(sdk.calls) == 4
    assert sdk.calls[-1]["config"].response_json_schema is None
    # Same mode and question can now reuse completed answers with no new call.
    report = extract(case.source.encode(), "case.txt", client=client(sdk), artifacts=cache)
    assert report["status"] == "COMPLETED" and report["requests"] == []
    assert len(sdk.calls) == 4


@pytest.mark.parametrize("kwargs", [
    {"text": "{"}, {"text": '{"unrecognized": 1}'}, {"finish": "MAX_TOKENS"},
    {"error": RuntimeError("429 RESOURCE_EXHAUSTED key=test-placeholder-key")},
])
def test_unusable_provider_response_is_not_a_success(kwargs):
    sdk = FakeSDK(**kwargs)
    report = extract(CASES[0].source.encode(), "case.txt", client=client(sdk), expected=CASES[0].expected)
    assert report["status"] == "FAILED"
    assert report["score"]["status"] == "INCOMPLETE"
    assert "test-placeholder-key" not in json.dumps(report)
    assert len(sdk.calls) == 1
    assert report["requests"][0]["status"] == "FAILED"


def test_unknown_question_never_reaches_provider():
    sdk = FakeSDK()
    with pytest.raises(ExtractionModelError, match="no registered response schema"):
        client(sdk).complete(system="", prompt="A future, unsupported question")
    assert not sdk.calls


def test_repair_question_has_a_distinct_schema():
    assert stage_for_prompt(METADATA_PROMPT + "\nparse repair") == "metadata"
    prompt = REPAIR_FACTS_PROMPT.format(fact_format="", items="")
    assert stage_for_prompt(prompt) == "repair"
    assert "repair_id" in response_schema("repair")["$defs"]["RepairFact"]["required"]


def test_budget_covers_scoped_client_copies():
    sdk = FakeSDK()
    c = client(sdk, maximum=1)
    scoped = c.for_document(document_text="Source", page_numbers=[1])
    scoped.complete(system="", prompt=METADATA_PROMPT)
    with pytest.raises(ExtractionModelError, match="budget reached"):
        c.complete(system="", prompt=METADATA_PROMPT)
    assert c.budget.calls == 1 and len(sdk.calls) == 1


def test_omitted_pages_prevent_a_pass_even_with_correct_selected_facts():
    case = CASES[0]
    from prototypes.extraction_lab.client import ReplayClient
    report = extract((case.source + "\fAnother page contains visit requirements.").encode(), "pages.txt",
                     client=ReplayClient(case.metadata, case.reply), expected=case.expected,
                     limits=reader_limits(max_pages=1))
    assert report["coverage_status"] == "INCOMPLETE"
    assert report["score"]["status"] == "INCOMPLETE"


def test_incorrect_anchor_and_duplicate_occurrence_cannot_pass():
    report = replay(BY_ID["screening-anchor"])
    modified = deepcopy(report)
    visit = next(f for f in modified["facts"]["facts"] if f["kind"] == "visit")
    visit["timing"]["from"]["name"] = "First dose"
    assert score(modified, BY_ID["screening-anchor"].expected)["status"] == "FAIL"
    expected = Expectations(checks=[{"name": "Screening labs"}, {"name": "Screening labs"}])
    assert score(report, expected)["status"] == "FAIL"
    assert score(report, None)["status"] == "NOT_SCORED"


@pytest.mark.parametrize("data,name", [(b"not a pdf", "x.pdf"), (b"\xff\xfe", "x.txt"), (b"", "x.txt")])
def test_invalid_input_rejected_without_a_request(data, name):
    with pytest.raises(ValueError):
        load_document(data, name)


def sample_pdf():
    """Two real pages of different sizes; works on pdfium versions without __enter__."""
    import pypdfium2 as pdfium
    with closing(pdfium.PdfDocument.new()) as document:
        for width in (200, 300):
            with closing(document.new_page(width, 240)):
                pass
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()


def test_pdf_upload_and_scoping_use_real_pdfium_without_context_protocol():
    import pypdfium2 as pdfium
    pdf = sample_pdf()
    pages, original = load_document(pdf, "synthetic.pdf")
    assert original == pdf
    assert [page.page_number for page in pages] == [1, 2]
    sdk = FakeSDK()
    c = client(sdk)
    c._pdf_bytes = pdf
    scoped = c.for_document(document_text="Second source page", page_numbers=[2])
    with closing(pdfium.PdfDocument(scoped._pdf_bytes)) as packet:
        assert len(packet) == 1
        with closing(packet[0]) as page:
            assert page.get_size() == (300, 240)
    assert c._pdf_bytes == pdf
    scoped.complete(system="", prompt=METADATA_PROMPT)
    assert sdk.calls[0]["contents"][0].inline_data.data == scoped._pdf_bytes


def test_uploaded_pdf_reaches_real_reader_and_compiler_with_fake_provider():
    sdk = FakeSDK()
    report = extract(sample_pdf(), "synthetic.pdf", client=client(sdk))
    assert report["status"] == "COMPLETED", report.get("error")
    assert report["event_count"] == 1
    assert len(sdk.calls) == 2
    assert all(call["contents"][0].inline_data.mime_type == "application/pdf" for call in sdk.calls)
    # Blank/image-only pages cannot authenticate a quote against embedded text.
    assert all(citation["verification"] == "UNVERIFIED"
               for fact in report["facts"]["facts"] for citation in fact["evidence"])


def test_oversized_pdf_is_closed_when_page_limit_rejects_it(monkeypatch):
    import pypdfium2 as pdfium
    class OversizedDocument:
        closed = False
        def __len__(self):
            return 1201
        def close(self):
            self.closed = True
    document = OversizedDocument()
    monkeypatch.setattr(pdfium, "PdfDocument", lambda data: document)
    with pytest.raises(ValueError, match="1,200 PDF pages"):
        load_document(b"%PDF-synthetic", "synthetic.pdf")
    assert document.closed


def test_streamlit_opens_and_replays_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "prototypes/extraction_lab/app.py"), default_timeout=30).run()
    assert not app.exception
    assert next(b for b in app.button if b.label == "Run extraction").disabled
    app.radio(key="run_mode").set_value("Offline fixture replay").run()
    next(b for b in app.button if b.label == "Run extraction").click().run()
    assert not app.exception
    assert app.session_state["lab_report"]["score"]["status"] == "PASS"
    app.multiselect[0].set_value([c.id for c in CASES]).run()
    next(b for b in app.button if b.label == "Run selected cases").click().run()
    assert not app.exception
    assert app.session_state["lab_suite"]["new_api_requests"] == 0
    assert all(row["status"] == "PASS" for row in app.session_state["lab_suite"]["results"])


class FlakySDK(FakeSDK):
    """Fails on the named models, answers on the rest."""
    def __init__(self, failing, error, **kwargs):
        super().__init__(**kwargs)
        self.failing, self.failure = failing, error

    def generate_content(self, **kwargs):
        if kwargs["model"] in self.failing:
            self.calls.append(kwargs)
            raise self.failure
        return super().generate_content(**kwargs)


def chain_client(sdk, chain, maximum=8):
    return StructuredGeminiClient(api_key="test-placeholder-key", model=chain[0], fallback_models=chain[1:],
                                  budget=RequestBudget(maximum=maximum, interval_seconds=0), sdk_client=sdk)


def test_unavailable_model_switches_to_next_and_stays_there():
    sdk = FlakySDK({"m-a"}, RuntimeError("503 UNAVAILABLE. high demand"))
    case = CASES[0]
    report = extract(case.source.encode(), "case.txt", client=chain_client(sdk, ["m-a", "m-b", "m-c"]), expected=case.expected)
    assert report["status"] == "COMPLETED" and report["score"]["status"] == "PASS"
    assert [c["model"] for c in sdk.calls] == ["m-a", "m-b", "m-b"]
    assert report["requests"][0]["status"] == "FAILED" and report["requests"][0]["switched_to"] == "m-b"
    assert report["model"] == "m-b" and report["models_used"] == ["m-b"]


def test_chain_is_exhausted_then_run_fails_honestly():
    sdk = FlakySDK({"m-a", "m-b"}, RuntimeError("429 RESOURCE_EXHAUSTED"))
    report = extract(CASES[0].source.encode(), "case.txt", client=chain_client(sdk, ["m-a", "m-b"]))
    assert report["status"] == "FAILED"
    assert [c["model"] for c in sdk.calls] == ["m-a", "m-b"]


def test_key_and_content_errors_never_switch_models():
    sdk = FlakySDK({"m-a"}, RuntimeError("403 PERMISSION_DENIED api key invalid"))
    report = extract(CASES[0].source.encode(), "case.txt", client=chain_client(sdk, ["m-a", "m-b"]))
    assert report["status"] == "FAILED"
    assert [c["model"] for c in sdk.calls] == ["m-a"]
    assert sdk.calls and not any(c["model"] == "m-b" for c in sdk.calls)


def test_fallback_chain_puts_choice_first_without_duplicates():
    from prototypes.extraction_lab.client import fallback_chain
    chain = fallback_chain("gemini-3.8-flash")
    assert chain[0] == "gemini-3.8-flash" and len(chain) == len(set(chain)) == 3


def test_event_rows_render_activities_for_every_case():
    from prototypes.extraction_lab.core import event_rows, replay
    rows = [row for case in CASES for row in event_rows(replay(case))]
    assert rows and all(isinstance(row["activities"], str) for row in rows)
    assert any(row["activities"] for row in rows)


def _case_schedule(case_id):
    from prototypes.extraction_lab import engine
    from prototypes.extraction_lab.cases import BY_ID
    return engine, engine.load_schedule(replay(BY_ID[case_id])["results"][0]["schedule"])


def test_engine_dates_follow_the_protocol_rule_and_never_guess():
    from datetime import date
    engine, schedule = _case_schedule("offset-window")
    # Protocol text: 4 weeks after Registration. Day arithmetic is the engine's, not the lab's.
    rows, _ = engine.patient_rows(schedule, {"REGISTRATION": date(2026, 1, 5)}, horizon=date(2027, 1, 1))
    assert rows[0]["Expected Date"] == "02 Feb 2026" and rows[0]["Status"] == "RESOLVED"
    rows, _ = engine.patient_rows(schedule, {}, horizon=date(2027, 1, 1))
    assert rows[0]["Expected Date"] == "" and rows[0]["Status"] == "WAITING_FOR_ANCHOR"


def test_engine_reports_integrity_blockers_instead_of_a_bare_error():
    from datetime import date
    engine, schedule = _case_schedule("missing-day-one")
    rows, info = engine.patient_rows(schedule, {"REGISTRATION": date(2026, 1, 5)}, horizon=date(2027, 1, 1))
    assert rows == [] and any("UNRESOLVED_SCHEDULE_ORIGIN" in line for line in info["blocked_by"])


def test_protocol_table_has_the_app_columns_and_does_not_mutate_the_schedule():
    engine, schedule = _case_schedule("table-footnote")
    before = schedule.model_dump_json()
    rows = engine.protocol_rows(schedule)
    assert list(rows[0]) == ["Visit", "Visit Name", "Timing", "Window", "Type", "Activities", "Status", "Notes"]
    from datetime import date
    engine.patient_rows(schedule, {"REGISTRATION": date(2026, 1, 5)}, horizon=date(2027, 1, 1))
    assert schedule.model_dump_json() == before and schedule.schedule_metadata.status.value == "EXTRACTED"
