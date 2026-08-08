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

    assert len(async_client.models.calls) == 1
    call = async_client.models.calls[0]
    assert call["model"] == "gemini-3.6-flash"
    assert call["contents"][0]["mime_type"] == "application/pdf"
    assert call["config"]["response_schema"] is ExtractedSchedule
    assert schedule.visits[0].name == "Screening"


def test_provider_switch_selects_gemini(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "gemini")
    assert isinstance(get_extractor(), GeminiProtocolExtractor)
    assert isinstance(get_details_extractor(), GeminiProtocolExtractor)


def test_provider_switch_selects_claude(monkeypatch):
    monkeypatch.setenv("PROTOCOL_EXTRACTION_PROVIDER", "claude")
    assert isinstance(get_extractor(), ClaudeProtocolExtractor)
    assert isinstance(get_details_extractor(), ClaudeProtocolExtractor)
