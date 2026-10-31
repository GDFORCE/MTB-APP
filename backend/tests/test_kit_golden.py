"""Step 0 golden: the sample protocol models exactly as the backend saves them (normalize, then the
default review answers, as save_kit_model does), frozen as JSON.

The files are written to frontend/tests/golden/models/, where the frontend golden test
(frontend/tests/kit-golden.test.ts) computes and freezes their schedules. So this test runs first.

- A missing file is created from today's code (refused when CI is set).
- UPDATE_GOLDEN=1 rewrites every file on purpose.
- Otherwise the saved model must be identical: a normalize change that alters an existing sample
  model fails here.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from app.kit.normalize import build_template_model, default_review_answers, normalize

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "app" / "kit" / "samples"
FRONTEND_TESTS = BACKEND.parent / "frontend" / "tests"
GOLDEN = FRONTEND_TESTS / "golden" / "models"
UPDATE = os.getenv("UPDATE_GOLDEN") == "1"
SAMPLE_FILES = sorted(SAMPLES.glob("*.json"))


def saved_model(raw: dict) -> dict:
    """The model a trial stores for this sample (kit_routes.save_kit_model without site answers)."""
    base = normalize(copy.deepcopy(raw["model"]))
    return build_template_model(base, default_review_answers(base))


def as_json(value):
    return json.loads(json.dumps(value))


def test_the_three_samples_are_there():
    assert {p.stem for p in SAMPLE_FILES} >= {"arc", "helix", "orion"}


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda p: p.stem)
def test_saved_sample_model_matches_golden(path: Path):
    if not FRONTEND_TESTS.is_dir():
        pytest.skip("frontend/tests is not next to backend/; the golden models live there")
    raw = json.loads(path.read_text(encoding="utf-8"))
    current = as_json({"name": path.stem, "siteAnswers": {}, "model": saved_model(raw)})
    target = GOLDEN / f"{path.stem}.json"
    if UPDATE or not target.exists():
        if not UPDATE and os.getenv("CI"):
            pytest.fail(f"{target.name} is missing; create it locally (run this test once) and commit it")
        GOLDEN.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert current == stored, (
        f"The saved {path.stem} model changed. If the change is intended, rerun with UPDATE_GOLDEN=1 "
        "and rerun the frontend golden test with UPDATE_GOLDEN=1 too.")


def test_sample_review_questions_settle_as_entered_ranges():
    """Worked out by hand from the samples: each review question only picks a gap between two dates,
    so normalize turns it into a range entered per participant and nobody is asked."""
    models = {p.stem: saved_model(json.loads(p.read_text(encoding="utf-8"))) for p in SAMPLE_FILES}
    anchor = lambda name, aid: next(a for a in models[name]["anchors"] if a["id"] == aid)  # noqa: E731
    assert [m["review_questions"] for m in models.values()] == [[], [], []]
    # ARC washout 42 or 49 days after Period 1; HELIX first dose 7-21 days after screening;
    # ORION adjuvant start 28-56 days after surgery.
    assert anchor("arc", "p2")["kind"] == "ask"
    assert anchor("arc", "p2")["range"] == {"relative_to": "p1", "min_days": 42, "max_days": 49}
    assert anchor("helix", "day1")["range"] == {"relative_to": "screening", "min_days": 7, "max_days": 21}
    assert anchor("orion", "adj_c1")["range"] == {"relative_to": "surgery", "min_days": 28, "max_days": 56}
    # The typical gap stays only as the planned timing.
    assert anchor("arc", "p2")["estimates"] == [{"when": [], "relative_to": "p1", "offset_days": 42}]
