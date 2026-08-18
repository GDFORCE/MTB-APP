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
    CanonicalScheduleResponse,
    ExtractedSchedule,
    GeminiProtocolExtractor,
    OllamaProtocolExtractor,
    OpenRouterProtocolExtractor,
    ScheduleDraft,
    get_details_extractor,
    get_extractor,
)


def assert_gemini_compatible_enums(schema, path="ScheduleDraft"):
    """Gemini's Schema type only accepts string enum values.

    A non-string enum (e.g. `Literal[0, 1]`) makes the SDK reject the request
    before it is sent, which surfaces as a total extraction failure rather than
    a degraded result — so guard the request schema here instead.
    """
    if isinstance(schema, dict):
        for value in schema.get("enum", []):
            assert isinstance(value, str), (
                f"{path}.enum contains non-string {value!r}; "
                "Gemini rejects the whole request schema")
        for key, value in schema.items():
            assert_gemini_compatible_enums(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            assert_gemini_compatible_enums(value, f"{path}[{index}]")


def test_explicit_null_visit_window_stays_unknown():
    """An unstated window must not become a plausible-looking default."""
    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{
            "name": "Visit 1 - Screening",
            "day_offset": -14,
            "window_days": None,
            "activities": ["Informed consent"],
        }],
    })

    assert schedule.visits[0].window_days is None


def test_explicit_null_repeat_member_window_stays_unknown():
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

    assert schedule.repeating_blocks[0].members[0].window_days is None


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
    assert {visit.window_days for visit in schedule.visits} == {None}


def test_gemini_extractor_uses_native_pdf_and_structured_response():
    payload = {
        "schedule_kind": "linear",
        "anchor_study_day": 1,
        "includes_day_zero": False,
        "canonical_plan": {
                "anchors": [{
                    "id": "anchor-baseline", "name": "Baseline",
                    "anchor_type": "first_dose",
                    "evidence_ids": ["timing-p12-01"],
                }],
            "activities": [{
                "id": "activity-consent", "name": "Informed consent",
                "evidence_ids": ["activity-p12-01"],
            }],
            "events": [{
                "id": "event-screening", "name": "Screening",
                "event_type": "Screening",
                "timing": {
                    "kind": "offset", "anchor_id": "anchor-baseline",
                    "offset": {"value": -14, "unit": "day"},
                    "source_label": "Day -14",
                    "evidence_ids": ["timing-p12-01"],
                },
                "window": {
                    "state": "stated",
                    "early": {"value": 3, "unit": "day"},
                    "late": {"value": 3, "unit": "day"},
                    "source_label": "±3 days",
                    "evidence_ids": ["window-p12-01"],
                },
                "activity_ids": ["activity-consent"],
                "evidence_ids": ["visit-p12-01"],
            }],
        },
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
            if schema_name == "DocumentTaskClassification":
                return SimpleNamespace(parsed={
                    "document_type": "protocol",
                    "analysis_task": "full_protocol_schedule",
                    "schedule_archetypes": ["linear"],
                    "complexity": "simple",
                    "has_schedule": True,
                    "confidence": 0.99,
                    "evidence": ["Schedule of Assessments"],
                })
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
                    "visit_timing": [{
                        "evidence_id": "timing-p12-01",
                        "claim": "Screening Day -14",
                        "source_location": "Schedule table, page 12",
                        "source_quote": "Day -14",
                        "confidence": 0.99,
                    }],
                    "visit_windows": [{
                        "evidence_id": "window-p12-01",
                        "claim": "Screening window is +/-3 days",
                        "source_location": "Schedule table, page 12",
                        "source_quote": "+/-3 days",
                        "confidence": 0.99,
                    }],
                    "cycle_rules": [],
                    "relative_timing": [],
                    "open_ended_rules": [],
                    "conflicts_or_unknowns": [],
                })
            if schema_name == "ScheduleVisitEvidence":
                return SimpleNamespace(parsed={
                    "visit_columns": [{
                        "evidence_id": "visit-p12-01",
                        "claim": "Screening visit",
                        "source_location": "Schedule table, page 12",
                        "source_quote": "Screening",
                        "confidence": 0.99,
                    }],
                    "special_visits": [],
                    "activity_assignments": [{
                        "evidence_id": "activity-p12-01",
                        "claim": "Informed consent at Screening",
                        "source_location": "Schedule table, page 12",
                        "source_quote": "Informed consent X",
                        "confidence": 0.99,
                    }],
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
        ThinkingConfig=lambda **kwargs: kwargs,
    )
    extractor = GeminiProtocolExtractor(api_key="test")
    extractor._client = lambda: (
        SimpleNamespace(APIError=RuntimeError), fake_types, client)

    schedule = asyncio.run(extractor.extract(b"%PDF-test"))

    assert len(async_client.models.calls) == 7
    call = async_client.models.calls[0]
    assert call["model"] == "gemini-3.6-flash"
    assert call["contents"][0]["mime_type"] == "application/pdf"
    assert [item["config"]["response_schema"].__name__
            for item in async_client.models.calls] == [
        "DocumentTaskClassification", "ScheduleDocumentMap", "ScheduleTimingEvidence", "ScheduleVisitEvidence",
        "CanonicalScheduleResponse", "CanonicalScheduleResponse", "ScheduleAudit",
    ]
    assert async_client.models.calls[4]["config"]["response_schema"] is CanonicalScheduleResponse
    assert "additionalProperties" not in json.dumps(CanonicalScheduleResponse.model_json_schema())
    assert_gemini_compatible_enums(CanonicalScheduleResponse.model_json_schema())
    assert schedule.visits[0].name == "Screening"
    assert schedule.verification_status == "verified"
    assert schedule.verification_confidence == 0.98
    assert schedule.verification_scores["procedure_mapping"] == 0.98


def test_gemini_retries_only_the_malformed_structured_stage():
    valid_payload = {
        "schedule_kind": "linear",
        "canonical_plan": {
            "anchors": [{
                "id": "anchor-baseline", "name": "Baseline",
                "anchor_type": "first_dose",
            }],
            "events": [{
                "id": "event-baseline", "name": "Baseline",
                "timing": {
                    "kind": "offset", "anchor_id": "anchor-baseline",
                    "offset": {"value": 0, "unit": "day"},
                },
            }],
        },
    }

    class Models:
        def __init__(self):
            self.calls = []

        async def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(parsed=None, text="{truncated")
            return SimpleNamespace(parsed=valid_payload, text=json.dumps(valid_payload))

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
        ThinkingConfig=lambda **kwargs: kwargs,
    )
    extractor = GeminiProtocolExtractor(api_key="test")
    extractor._client = lambda: (
        SimpleNamespace(APIError=RuntimeError), fake_types, client)

    result = asyncio.run(extractor._generate(
        b"%PDF-test",
        "Build this stage.",
        ExtractedSchedule,
        system_instruction="Return structured data.",
        max_tokens=1000,
    ))

    assert result.canonical_plan.events[0].name == "Baseline"
    assert len(async_client.models.calls) == 2
    assert "STRUCTURED OUTPUT RETRY" in async_client.models.calls[1]["contents"][1]


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
