"""A protocol with a borderless schedule table through the upload job and on to enrolment,
without network or database: the new table-reading step must not disturb what follows."""

from __future__ import annotations

import copy
import json

import pytest

import server  # noqa: F401 - initialize the circular route import first
import kit_routes
from app.kit.analyze import SAMPLES
from app.kit.normalize import build_template_model, normalize


def _borderless_pdf() -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    xs = [220, 290, 360]
    page.insert_text((72, 90), "Assessment", fontsize=10)
    for x, head in zip(xs, ["Screening", "Day 1", "Week 4"]):
        page.insert_text((x - 8, 90), head, fontsize=10)
    for r, (label, marked) in enumerate([("Consent", [0]), ("ECG", [0, 1, 2]), ("Labs", [0, 2])]):
        for c in marked:
            page.insert_text((xs[c], 120 + r * 22), "X", fontsize=10)
        page.insert_text((72, 120 + r * 22), label, fontsize=10)
    for line in range(30):
        page.insert_text((72, 300 + line * 10), "Narrative protocol text describing the study in detail. " * 2,
                         fontsize=6)
    return doc.tobytes()


def test_the_upload_job_reads_a_borderless_table_and_the_result_can_be_saved_and_enrolled(monkeypatch):
    monkeypatch.delenv("KIT_SAMPLE_MODEL", raising=False)
    base = json.loads((SAMPLES / "arc.json").read_text(encoding="utf-8"))["model"]
    wrong = {"columns": ["Screening", "Day 1", "Week 4"],
             "rows": [{"label": "Consent", "cells": ["X", "", ""]}, {"label": "ECG", "cells": ["X", "", ""]},
                      {"label": "Labs", "cells": ["X", "", "X"]}]}

    class Client:
        model = "fake"
        on_status = None

        def json(self, prompt, **kwargs):
            if "Copy the table exactly" in prompt:
                return copy.deepcopy(wrong)  # the same misreading every time
            if "MODEL VISITS:" in prompt:
                return {"corrections": []}
            if "MODEL EVENTS:" in prompt:
                return {"events": []}
            return copy.deepcopy(base)

    monkeypatch.setattr(kit_routes, "OpenAIKitClient", Client)
    job = kit_routes._new_job("user-1", "protocol.pdf")
    result = kit_routes._run(job, _borderless_pdf(), "protocol.pdf")

    ids = [s["id"] for s in job["stages"]]
    assert ids == ["read", "locate", "tables", "model", "verify", "events", "check"]
    locate = next(s for s in job["stages"] if s["id"] == "locate")
    assert locate["detail"] == "tables on p. 1 (1 without cell borders)"
    flag = next(q for q in result["model"]["review_questions"] if q["id"] == "table_check_p1")
    assert result["reviewAnswers"]["table_check_p1"] == "unsure"

    # PUT /trials/{id}/kit-model: normalise the base model, apply the reviewer's answers.
    answers = {**result["reviewAnswers"], flag["id"]: "checked"}
    saved = build_template_model(normalize(copy.deepcopy(result["baseModel"])), answers)
    assert saved["visits"] and saved["anchors"]

    # Add Patient: the saved model still validates a participant record.
    start = next(a for a in saved["anchors"] if a["kind"] == "start")
    participant_questions = [q for q in saved["questions"] if q["scope"] == "participant"]
    record = kit_routes.clean_participant(saved, {
        "startDate": "2026-11-02",
        "answers": {q["id"]: (q["options"][0]["value"] if q["options"] else "yes") for q in participant_questions},
    })
    assert record["startAnchor"] == start["id"] and record["startDate"] == "2026-11-02"
