"""The OpenAI client honours the same LLMClient contract as Anthropic/Gemini.

No network: the SDK client is replaced by a recorder, so these tests prove what
is sent and how replies are turned into text or a visible error.
"""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction import runner  # noqa: E402
from app.extraction.llm_client import (  # noqa: E402
    ExtractionModelError, ExtractionNotConfigured, OpenAIClient,
)


class _Recorder:
    def __init__(self, reply):
        self.calls = []
        self._reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._reply


def _reply(content='{"facts": []}', finish_reason="stop", refusal=None):
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _client(recorder, **kwargs):
    client = OpenAIClient(api_key="test-key", model="gpt-test", **kwargs)
    client._client = lambda: recorder
    return client


def test_sends_system_prompt_document_and_asks_for_json():
    recorder = _Recorder(_reply())
    text = _client(recorder, document_text="--- page 1 ---\nDay 8").complete(
        system="rules", prompt="extract", max_tokens=123)
    assert text == '{"facts": []}'
    call = recorder.calls[0]
    assert call["model"] == "gpt-test"
    assert call["max_completion_tokens"] == 123
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0] == {"role": "system", "content": "rules"}
    parts = call["messages"][1]["content"]
    assert "Day 8" in parts[0]["text"] and parts[-1]["text"] == "extract"


def test_attaches_the_pdf_as_a_file_part_when_given():
    recorder = _Recorder(_reply())
    _client(recorder, pdf_bytes=b"%PDF-1.4").complete(system="s", prompt="p")
    first = recorder.calls[0]["messages"][1]["content"][0]
    assert first["type"] == "file"
    assert first["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_a_truncated_reply_is_an_error_not_bad_json():
    recorder = _Recorder(_reply(content='{"facts": [', finish_reason="length"))
    with pytest.raises(ExtractionModelError, match="cut off"):
        _client(recorder).complete(system="s", prompt="p")


def test_a_refusal_is_an_error():
    recorder = _Recorder(_reply(content=None, refusal="no"))
    with pytest.raises(ExtractionModelError, match="refused"):
        _client(recorder).complete(system="s", prompt="p")


def test_missing_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ExtractionNotConfigured):
        OpenAIClient(api_key="")._client()


def test_runner_selects_openai_only_when_named_and_keyed(monkeypatch):
    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert runner.configured_provider() is None
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert runner.configured_provider() == "openai"
    assert isinstance(runner.build_client(), OpenAIClient)
