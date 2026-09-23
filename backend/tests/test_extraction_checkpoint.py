import copy

import pytest

from eval.extraction_checkpoint import CheckpointClient


class Client:
    _model = "test-model"
    _document_text = "whole protocol"
    _pdf_bytes = b"pdf"
    _api_key = "secret-must-not-be-recorded"

    def for_document(self, *, document_text, page_numbers):
        scoped = copy.copy(self)
        scoped._document_text = document_text + str(page_numbers)
        return scoped

    def complete(self, **kwargs):
        return '{"claims": []}'


def test_completed_request_can_resume_without_a_model_call_and_without_recording_secrets(tmp_path):
    path = tmp_path / "calls.jsonl"
    original = CheckpointClient(Client(), path, document_hash="abc")
    answer = original.complete(system="system", prompt="prompt")
    assert original.calls == 1
    assert "secret-must-not-be-recorded" not in path.read_text()
    resumed = CheckpointClient(Client(), path, document_hash="abc", resume=True)
    assert resumed.complete(system="system", prompt="prompt") == answer
    assert resumed.calls == 0 and resumed.reused == 1
    fresh = CheckpointClient(Client(), path, document_hash="abc")
    fresh.complete(system="system", prompt="prompt")
    assert fresh.calls == 1 and fresh.reused == 0


@pytest.mark.parametrize("changed", ["model", "document", "context", "prompt", "system", "tokens"])
def test_changed_model_source_or_request_cannot_reuse_a_previous_answer(tmp_path, changed):
    path = tmp_path / "calls.jsonl"
    CheckpointClient(Client(), path, document_hash="abc").complete(system="system", prompt="prompt")
    delegate = Client()
    if changed == "model": delegate._model = "different-model"
    if changed == "context": delegate._document_text = "different packet"
    resumed = CheckpointClient(delegate, path, document_hash="def" if changed == "document" else "abc", resume=True)
    kwargs = {"system": "other" if changed == "system" else "system", "prompt": "other" if changed == "prompt" else "prompt"}
    if changed == "tokens": kwargs["max_tokens"] = 200
    resumed.complete(**kwargs)
    assert resumed.calls == 1 and resumed.reused == 0


def test_scoped_document_calls_share_counts_but_not_context_keys(tmp_path):
    path = tmp_path / "calls.jsonl"
    original = CheckpointClient(Client(), path, document_hash="abc")
    original.for_document(document_text="page", page_numbers=[2]).complete(system="system", prompt="prompt")
    resumed = CheckpointClient(Client(), path, document_hash="abc", resume=True)
    resumed.for_document(document_text="page", page_numbers=[2]).complete(system="system", prompt="prompt")
    resumed.for_document(document_text="page", page_numbers=[3]).complete(system="system", prompt="prompt")
    assert original.calls == 1 and resumed.calls == 1 and resumed.reused == 1


def test_failed_and_partial_responses_are_not_reused(tmp_path):
    path = tmp_path / "calls.jsonl"
    class Broken(Client):
        def complete(self, **kwargs):
            raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError):
        CheckpointClient(Broken(), path, document_hash="abc").complete(system="system", prompt="prompt")
    with path.open("a") as handle: handle.write('{"incomplete":')
    resumed = CheckpointClient(Broken(), path, document_hash="abc", resume=True)
    with pytest.raises(RuntimeError): resumed.complete(system="system", prompt="prompt")
    assert resumed.calls == 1 and resumed.reused == 0
