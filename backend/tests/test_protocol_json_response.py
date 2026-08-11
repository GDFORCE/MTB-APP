"""Regression coverage for model-authored protocol schedule JSON."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_extraction import (  # noqa: E402
    ClaudeProtocolExtractor,
    ExtractedSchedule,
    GeminiProtocolExtractor,
    OllamaProtocolExtractor,
    OpenRouterProtocolExtractor,
    get_details_extractor,
    get_extractor,
)


def test_explicit_null_visit_window_uses_documented_default():
    """The live provider uses null when the protocol gives no visit window."""
    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{
            "name": "Visit 1 - Screening",
            "day_offset": -14,
            "window_days": None,
            "activities": ["Informed consent"],
        }],
    })

    assert schedule.visits[0].window_days == 3


def test_explicit_null_repeat_member_window_uses_documented_default():
    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "cyclic",
        "repeating_blocks": [{
            "from_cycle": 2,
            "to_cycle": 3,
            "cycle_length_days": 21,
            "first_cycle_start_day": 21,
            "members": [{
                "name_template": "Cycle {cycle} Day 1",
                "day_within_cycle": 0,
                "window_days": None,
            }],
        }],
    })

    assert schedule.repeating_blocks[0].members[0].window_days == 3


def test_json_extractor_does_not_retry_valid_schedule_with_null_windows():
    payload = {
        "schedule_kind": "linear",
        "visits": [
            {
                "name": f"Visit {number}",
                "visit_type": "Treatment",
                "day_offset": number * 7,
                "day_end": None,
                "hour_offset": None,
                "hour_end": None,
                "window_days": None,
                "window_before": None,
                "window_after": None,
                "relative_to": None,
                "relative_offset_days": None,
                "arm": None,
                "period": None,
                "activities": [],
            }
            for number in range(1, 19)
        ],
        "repeating_blocks": [],
        "assumptions": [],
        "source_notes": "Schedule of Assessments",
    }
    response = SimpleNamespace(content=[SimpleNamespace(
        type="text", text=json.dumps(payload))])

    class Messages:
        def __init__(self):
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            return response

    messages = Messages()
    client = SimpleNamespace(messages=messages)
    extractor = ClaudeProtocolExtractor(api_key="test")

    schedule = asyncio.run(extractor._extract_without_grammar(client, b"%PDF-test"))

    assert messages.calls == 1
    assert len(schedule.visits) == 18
    assert {visit.window_days for visit in schedule.visits} == {3}


def test_gemini_extractor_uses_native_pdf_and_structured_response():
    payload = {
        "schedule_kind": "linear",
        "visits": [{
            "name": "Screening",
            "day_offset": -14,
            "window_days": 3,
            "activities": ["Informed consent"],
        }],
        "repeating_blocks": [],
        "assumptions": [],
        "source_notes": "Schedule of Assessments",
    }
    response = SimpleNamespace(parsed=payload, text=json.dumps(payload))

    class Models:
        def __init__(self):
            self.calls = []

        async def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            schema_name = kwargs["config"]["response_schema"].__name__
            if schema_name == "ScheduleDocumentMap":
                return SimpleNamespace(parsed={
                    "has_schedule": True,
                    "schedule_kind": "linear",
                    "schedule_locations": ["Schedule of Assessments"],
                    "supporting_locations": [],
                    "arms_and_periods": [],
                    "baseline_anchor": "Day 1",
                    "notes": [],
                })
            if schema_name == "ScheduleTimingEvidence":
                return SimpleNamespace(parsed={
                    "visit_timing": ["Screening Day -14"],
                    "visit_windows": [],
                    "cycle_rules": [],
                    "relative_timing": [],
                    "open_ended_rules": [],
                    "conflicts_or_unknowns": [],
                })
            if schema_name == "ScheduleVisitEvidence":
                return SimpleNamespace(parsed={
                    "visit_columns": ["Screening"],
                    "special_visits": [],
                    "activity_assignments": ["Screening: Informed consent"],
                    "table_footnotes": [],
                    "arm_period_differences": [],
                    "conflicts_or_unknowns": [],
                })
            if schema_name == "ScheduleAudit":
                dimension = {
                    "applicable": True,
                    "accuracy": 0.98,
                    "passed": True,
                    "checked_items": ["Schedule table and footnotes"],
                    "summary": "matches",
                }
                return SimpleNamespace(parsed={
                    "approved": True,
                    "confidence": 0.98,
                    "visit_coverage": dimension,
                    "timing": dimension,
                    "windows": dimension,
                    "visit_types": dimension,
                    "procedure_mapping": dimension,
                    "overall_schedule": dimension,
                    "verified_items": ["Schedule table and footnotes"],
                    "issues": [],
                    "summary": "Candidate matches the protocol.",
                })
            return response

    class AsyncClient:
        def __init__(self):
            self.models = Models()

        async def aclose(self):
            return None

    async_client = AsyncClient()
    client = SimpleNamespace(aio=async_client, close=lambda: None)
    fake_types = SimpleNamespace(
        Part=SimpleNamespace(from_bytes=lambda **kwargs: kwargs),
        GenerateContentConfig=lambda **kwargs: kwargs,
    )
    extractor = GeminiProtocolExtractor(api_key="test")
    extractor._client = lambda: (
        SimpleNamespace(APIError=RuntimeError), fake_types, client)

    schedule = asyncio.run(extractor.extract(b"%PDF-test"))

    assert len(async_client.models.calls) == 5
    call = async_client.models.calls[0]
    assert call["model"] == "gemini-3.6-flash"
    assert call["contents"][0]["mime_type"] == "application/pdf"
    assert [item["config"]["response_schema"].__name__
            for item in async_client.models.calls] == [
        "ScheduleDocumentMap", "ScheduleTimingEvidence", "ScheduleVisitEvidence",
        "ExtractedSchedule", "ScheduleAudit",
    ]
    assert async_client.models.calls[3]["config"]["response_schema"] is ExtractedSchedule
    assert schedule.visits[0].name == "Screening"
    assert schedule.verification_status == "verified"
    assert schedule.verification_confidence == 0.98
    assert schedule.verification_scores["procedure_mapping"] == 0.98


def test_provider_switch_selects_gemini(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "gemini")
    assert isinstance(get_extractor(), GeminiProtocolExtractor)
    assert isinstance(get_details_extractor(), GeminiProtocolExtractor)


def test_provider_switch_selects_claude(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "claude")
    assert isinstance(get_extractor(), ClaudeProtocolExtractor)
    assert isinstance(get_details_extractor(), ClaudeProtocolExtractor)


def test_provider_switch_selects_openrouter_deepseek(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    extractor = get_extractor()
    assert isinstance(extractor, OpenRouterProtocolExtractor)
    assert isinstance(get_details_extractor(), OpenRouterProtocolExtractor)
    assert extractor.configured
    assert extractor._model == "~deepseek/deepseek-v4-flash-latest"


def test_openrouter_sends_pdf_and_structured_schema(monkeypatch):
    payload = {
        "schedule_kind": "linear",
        "visits": [{"name": "Baseline", "day_offset": 0}],
        "repeating_blocks": [],
        "assumptions": [],
        "source_notes": "Schedule table",
    }
    calls = []

    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    extractor = OpenRouterProtocolExtractor(api_key="test")

    schedule = asyncio.run(extractor.extract(b"%PDF-test"))

    assert schedule.visits[0].name == "Baseline"
    url, kwargs = calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["json"]["model"] == "~deepseek/deepseek-v4-flash-latest"
    assert kwargs["json"]["response_format"]["json_schema"]["strict"] is True
    file_part = kwargs["json"]["messages"][1]["content"][1]
    assert file_part["file"]["file_data"].startswith(
        "data:application/pdf;base64,")


def test_provider_switch_selects_local_ollama(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "ollama")
    monkeypatch.setenv(
        "OLLAMA_PROTOCOL_EXTRACTION_MODEL", "qwen3-vl:4b-instruct-q4_K_M")
    extractor = get_extractor()
    assert isinstance(extractor, OllamaProtocolExtractor)
    assert extractor.configured
    assert extractor._model == "qwen3-vl:4b-instruct-q4_K_M"


def test_ollama_large_pdf_batches_and_resumes_from_checkpoints(tmp_path):
    extractor = OllamaProtocolExtractor(model="test-vl")
    extractor._batch_size = 2
    extractor._pdf_page_count = lambda _data: 5
    extractor._render_pdf_pages = lambda _data, start, end: [
        f"page-{number}".encode() for number in range(start, end)
    ]
    calls = []

    async def fake_chat(messages, **_kwargs):
        calls.append(messages)
        return SimpleNamespace(message=SimpleNamespace(
            content=f"evidence batch {len(calls)}"))

    extractor._chat = fake_chat
    checkpoint = tmp_path / "evidence.jsonl"
    evidence = asyncio.run(extractor._collect_evidence(
        b"pdf", kind="details", checkpoint_path=checkpoint))

    assert len(calls) == 3
    assert "evidence batch 3" in evidence
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 3

    calls.clear()
    resumed = asyncio.run(extractor._collect_evidence(
        b"pdf", kind="details", checkpoint_path=checkpoint))

    assert calls == []
    assert resumed == evidence
