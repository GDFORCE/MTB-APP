"""Regression coverage for model-authored protocol schedule JSON."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_extraction import ClaudeProtocolExtractor, ExtractedSchedule  # noqa: E402


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
