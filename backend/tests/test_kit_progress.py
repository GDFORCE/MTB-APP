"""Job progress for oversized protocols, without network or database operations."""

from __future__ import annotations

import copy
import json

import server  # noqa: F401 - initialize the circular route import first
import kit_routes
from app.kit.analyze import SAMPLES
from app.kit.llm import AnalysisError


def new_job():
    return {"stages": [{"id": i, "label": label, "status": "wait", "detail": ""}
                       for i, label in kit_routes.STAGES]}


def test_summary_progress_appears_before_model_and_updates_without_duplicate_rows():
    job = new_job()
    kit_routes._set_stage(job, "summarize", "run", "Summarising section 1 of 3")
    kit_routes._set_stage(job, "summarize", "run", "Summarising section 2 of 3")
    ids = [s["id"] for s in job["stages"]]
    assert ids.count("summarize") == 1 and ids.index("summarize") < ids.index("model")
    stage = next(s for s in job["stages"] if s["id"] == "summarize")
    assert stage["status"] == "run" and stage["detail"] == "Summarising section 2 of 3"


def test_text_uploads_keep_complete_source_for_the_summary_fallback():
    prepared = kit_routes._prepare(new_job(), b"All protocol text", "protocol.txt")
    assert prepared["fullText"] and prepared["pageTexts"] == [prepared["text"]]


def test_job_reports_summary_retries_on_the_summary_stage_and_finishes(monkeypatch):
    monkeypatch.delenv("KIT_SAMPLE_MODEL", raising=False)
    base = json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"]
    snapshots = []
    original_set_stage = kit_routes._set_stage

    def capture(job, stage_id, status, detail=""):
        original_set_stage(job, stage_id, status, detail)
        snapshots.append(copy.deepcopy(job["stages"]))

    class Client:
        model = "fake"
        on_status = None

        def __init__(self):
            self.calls = 0

        def json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise AnalysisError("prompt_too_large", "too big")
            if "SECTION TEXT:" in prompt:
                self.on_status("Model busy, retrying summary")
                return {"summary": "[[Page 1]] Day 7 assessment; window UNRESOLVED."}
            if "MODEL VISITS:" in prompt:
                return {"corrections": []}
            return copy.deepcopy(base)

    monkeypatch.setattr(kit_routes, "_set_stage", capture)
    monkeypatch.setattr(kit_routes, "OpenAIKitClient", Client)
    job = new_job()
    result = kit_routes._run(job, b"Full text protocol", "protocol.txt")
    assert result["model"]["visits"]
    assert any(s["id"] == "summarize" and s["status"] == "run" and "retrying summary" in s["detail"]
               for snapshot in snapshots for s in snapshot)
    assert not any(s["id"] == "model" and "retrying summary" in s["detail"]
                   for snapshot in snapshots for s in snapshot)
    assert all(s["status"] == "done" for s in job["stages"])
