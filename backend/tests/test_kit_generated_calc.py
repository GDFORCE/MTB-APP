"""Generated calculations: the analysis writes the missing logic for a rule no handler covers, and the
app validates, tests, shows, versions and runs it without any protocol-specific code.

Acceptance (test_upload_generates_the_missing_calculation_and_dates_the_samples): a synthetic protocol
whose PK sample rule ("7 days (+/-1) after each injection that is given; none after a withheld
injection") has no handler is uploaded through /api/kit/analyze. The model provider is a controlled
stand-in returning what an analysis following the prompt returns, including the generated program
and its tests. Everything after that is the app's own path: clean-up runs the program's tests, the
workflow shows it for review, approval pins it, the patient record says which injection was withheld,
and the app's engine and patient calendar produce the sample dates. Expected dates are by hand.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import server  # noqa: I001 - server first: it imports kit_routes at its end
import test_kit_adaptive_study as harness
from test_kit_adaptive_study import PI, SPONSOR, analyze, app, enrol, put_kit  # noqa: F401 - app is a fixture
from app.kit import calc
from app.kit.normalize import normalize
from app.kit.patterns import PATTERNS, blockers
from app.kit.workflow import build_workflow, compile_workflow
from _kit_calendar import calendar

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "calc_vectors.json").read_text(encoding="utf-8"))["cases"]

PROGRAM = {"map": {"filter": {"fact": "visit", "id": "inj"}, "as": "o", "where": {"op": "and", "args": [
    {"op": "!=", "args": [{"var": "o.given"}, False]}, {"op": "!=", "args": [{"var": "o.status"}, {"str": "cancelled"}]}]}},
    "as": "o", "do": {"record": {
        "n": {"var": "o.n"},
        "date": {"op": "if", "args": [{"var": "o.given"}, {"op": "add_days", "args": [{"var": "o.actual"}, 7]}, None]},
        "pending": {"op": "if", "args": [{"var": "o.given"}, None, {"op": "text", "args": [
            {"str": "7 days after injection "}, {"var": "o.n"}, {"str": ", once given"}]}]},
        "window_before": 1, "window_after": 1}}}
TESTS = [
    {"name": "a sample 7 days after each given injection; later ones wait",
     "facts": {"visits": {"inj": [{"n": 1, "planned": "2026-01-05", "actual": "2026-01-06", "given": True},
                                  {"n": 2, "planned": "2026-02-02", "actual": None, "given": None}]}},
     "expect": [{"n": 1, "date": "2026-01-13", "pending": None, "window_before": 1, "window_after": 1},
                {"n": 2, "date": None, "pending": "7 days after injection 2, once given", "window_before": 1, "window_after": 1}]},
    {"name": "no sample after a withheld injection",
     "facts": {"visits": {"inj": [{"n": 1, "planned": "2026-01-05", "actual": "2026-01-05", "given": False}]}},
     "expect": []},
]
QUOTE = "A pharmacokinetic blood sample is taken 7 days (±1 day) after each injection that is given"
TEXT = ("SYN-GEN-PK synthetic protocol. Day 1 is the first injection. Injections are given every 28 days (±3 days), six in "
        f"total. {QUOTE}; no sample is taken after a withheld injection. The end of study visit is on Day 169 (±7 days).")


def gen_model(program=PROGRAM, tests=TESTS, inputs=None, number="SYN-GEN-PK") -> dict:
    """What an analysis following the prompt returns for TEXT (synthetic)."""
    return {
        "protocol": {"number": number, "title": "Synthetic PK sampling after given doses", "version": "1"},
        "anchors": [{"id": "day1", "label": "Day 1 (first injection)", "kind": "start", "is_day1": True}],
        "questions": [],
        "visits": [
            {"id": "inj", "code": "INJ", "name": "Injection", "phase": "Treatment", "anchor": "day1", "offset_days": 0,
             "window_before": 3, "window_after": 3, "procedures": [{"name": "Study drug injection"}],
             "repeat": {"every_days": 28, "count": 6, "code_pattern": "INJ {n}", "label_pattern": "Injection {n}"}},
            {"id": "pk", "code": "PK", "name": "PK sample", "phase": "Treatment", "anchor": "day1", "offset_days": 7,
             "window_before": 1, "window_after": 1, "procedures": [{"name": "PK blood sample"}]},
            {"id": "eos", "code": "EOS", "name": "End of study", "phase": "Follow-up", "anchor": "day1", "offset_days": 168,
             "window_before": 7, "window_after": 7, "procedures": [{"name": "Final assessments"}]},
        ],
        "events": [], "rules": [], "review_questions": [], "notes": [],
        "pattern_rules": [{"id": "pk_after_given", "pattern": "SAMPLE_AFTER_GIVEN_DOSE",
                           "label": "PK sample 7 days after each injection that was given", "visits": ["pk"], "params": {},
                           "quote": QUOTE, "page": 1, "program": copy.deepcopy(program), "output": {"kind": "occurrences"},
                           "inputs": inputs if inputs is not None else [{"fact": "visit", "id": "inj"}],
                           "tests": copy.deepcopy(tests)}],
    }


# ── the language: both interpreters agree on the shared vectors ──────────────────


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_shared_vectors(case):
    if case.get("error"):
        with pytest.raises(calc.CalcError):
            calc.evaluate(case["program"], case.get("facts") or {}, case.get("this"))
    else:
        assert calc.evaluate(case["program"], case.get("facts") or {}, case.get("this")) == case["expect"]


def test_programs_are_checked_before_anything_runs():
    model = normalize(gen_model())
    declared = [{"fact": "visit", "id": "inj"}]
    bad = {
        "unknown operation": {"op": "exec", "args": [{"str": "rm"}]},
        "unbound name": {"op": "add_days", "args": [{"var": "x"}, 1]},
        "unknown visit": {"fact": "visit", "id": "nope"},
        "undeclared input": {"fact": "anchor", "id": "day1"},
        "written-out list": [1, 2],
        "wrong arity": {"op": "add_days", "args": [1]},
    }
    for name, program in bad.items():
        problems, _ = calc.check(program, "occurrences", model, declared)
        assert problems, name
    deep = 1
    for _ in range(80):
        deep = {"op": "+", "args": [deep, 1]}
    assert "too large" in calc.check(deep, "date", model, [])[0][0]
    loop = {"map": {"map": {"fact": "visit", "id": "inj"}, "as": "a", "do": {"var": "a"}}, "as": "b", "do": {"var": "b"}}
    assert calc.check(loop, "occurrences", model, declared)[0] == []


def test_a_valid_generated_calculation_is_used_only_after_review():
    model = normalize(gen_model())
    [rule] = model["pattern_rules"]
    assert rule["pattern"] not in PATTERNS, "no handler exists for this rule"
    assert "blocked" not in rule
    status = rule["generated"]
    assert status["calc_version"] == 1 and len(status["sha256"]) == 64 and status["reads"] == ["visit inj"]
    assert [t["passed"] for t in status["tests"]] == [True, True]
    assert blockers(model) == []
    point = next(p for p in model["open_points"] if p["id"] == "generated_pk_after_given")
    assert point["kind"] == "generated" and point["quote"] == QUOTE and point["affects"] == ["pk"]
    workflow = build_workflow(model, 1)
    assert compile_workflow(workflow) == model and not workflow["errors"]
    block = next(b for b in workflow["blocks"] if b["id"] == "pattern:pk_after_given")
    summary = "\n".join(block["summary"])
    assert "Generated calculation SAMPLE_AFTER_GIVEN_DOSE (calc v1" in summary
    assert "Inputs: visit inj" in summary and "Output: the dates of every occurrence of pk" in summary
    assert "Logic: for each o in [o in occurrences of inj where" in summary
    assert "Test passes: no sample after a withheld injection" in summary
    assert {"block": "visit:inj", "relation": "Generated calculation reads", "basis": "planned or actual"} in block["inputs"]
    assert any(i["id"] == "generated_pk_after_given" for i in workflow["issues"])


@pytest.mark.parametrize("change, words", [
    (lambda m: m["pattern_rules"][0]["program"]["do"]["record"]["date"]["args"][1]["args"].__setitem__(1, 6), "its own test"),
    (lambda m: m["pattern_rules"][0].__setitem__("inputs", []), "does not declare (visit inj)"),
    (lambda m: m["pattern_rules"][0].__setitem__("tests", TESTS[:1]), "at least two test cases"),
    (lambda m: m["pattern_rules"][0].__setitem__("visits", ["pk", "eos"]), "exactly one visit"),
    (lambda m: m["pattern_rules"][0].__setitem__("program", {"map": {"fact": "visit", "id": "pk"}, "as": "o", "do": {"var": "o"}}),
     "reads the visit it produces"),
])
def test_a_generated_calculation_that_fails_its_checks_is_a_blocker(change, words):
    raw = gen_model()
    change(raw)
    model = normalize(raw)
    rule = model["pattern_rules"][0]
    assert words in rule["blocked"] and "generated" not in rule
    [b] = blockers(model)
    assert b["pattern"] == "SAMPLE_AFTER_GIVEN_DOSE" and b["generated"] is True and words in b["problem"]
    assert any(words in e for e in build_workflow(model, 1)["errors"])


def test_a_named_rule_without_a_program_still_waits_for_a_handler():
    raw = gen_model()
    for key in ("program", "output", "inputs", "tests"):
        raw["pattern_rules"][0].pop(key)
    rule = normalize(raw)["pattern_rules"][0]
    assert rule["blocked"] == "no_handler"


# ── acceptance: upload → generated calculation → review → approval → enrolment → calendar ──


def test_upload_generates_the_missing_calculation_and_dates_the_samples(app, monkeypatch):
    monkeypatch.setitem(harness.MODELS, "SYN-GEN-PK", gen_model)
    app.db.trials.docs.append({"id": "genpk", "title": "Synthetic PK", "protocol_id": "SYN-GEN-PK"})
    body = analyze(app.client, TEXT)
    rule = body["baseModel"]["pattern_rules"][0]
    assert rule["generated"]["tests"][0]["passed"] and rule["quote_found"] is True
    preview = app.client.post("/api/kit/workflow-preview", json=body, headers=SPONSOR).json()
    assert preview["problems"] == [next(p["text"] for p in body["baseModel"]["open_points"] if p["kind"] == "generated")]
    refused = app.client.put("/api/trials/genpk/kit-model", headers=SPONSOR,
                             json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert refused.status_code == 422, "a generated calculation is not used before the study team confirms it"
    body["resolutions"] = {"generated_pk_after_given": "Checked against the protocol: 7 days +/-1 after each given injection."}
    preview = app.client.post("/api/kit/workflow-preview", json=body, headers=SPONSOR).json()
    assert not preview["problems"]
    saved = app.client.put("/api/trials/genpk/kit-model", headers=SPONSOR,
                           json={**body, "workflowAction": "approve", "workflowDigest": preview["digest"]})
    assert saved.status_code == 200, saved.text
    approved = app.client.get("/api/trials/genpk/kit-model", headers=SPONSOR).json()
    stored = approved["model"]["pattern_rules"][0]
    assert stored["program"] == PROGRAM and stored["generated"]["sha256"] == calc.fingerprint(PROGRAM)

    pid = enrol(app, "genpk", "PK-001", 1, {"startDate": "2026-01-05"})

    def record(r):
        r["actuals"].update({"inj#1": "2026-01-05", "inj#2": "2026-02-03", "inj#3": "2026-03-02"})
        r["withheld"] = {"inj#3": "IOP 32 mm Hg"}
    assert put_kit(app, pid, record).status_code == 200
    payload = app.client.get(f"/api/patients/{pid}/kit", headers=PI).json()
    assert payload["participant"]["withheld"] == {"inj#3": "IOP 32 mm Hg"}
    cal = calendar(payload)
    staff = cal["staff"]
    assert (staff["pk#1"]["planned"], staff["pk#1"]["windowStart"], staff["pk#1"]["windowEnd"]) == ("2026-01-12", "2026-01-11", "2026-01-13")
    assert staff["pk#2"]["planned"] == "2026-02-10", "counted from the recorded (late) injection, not its target"
    assert "pk#3" not in staff, "no sample after the withheld injection"
    assert staff["inj#3"]["withheld"] == "IOP 32 mm Hg"
    for n in (4, 5, 6):
        assert staff[f"pk#{n}"]["status"] == "waiting" and staff[f"pk#{n}"]["relative"] == f"7 days after injection {n}, once given"
    assert [staff[f"inj#{n}"]["planned"] for n in (4, 5, 6)] == ["2026-03-30", "2026-04-27", "2026-05-25"]
    assert staff["eos#1"]["planned"] == "2026-06-22"
    assert any("generated" in n for n in staff["pk#1"]["notes"])
    assert {"pk#1", "pk#2"} <= set(cal["patient"]) and "pk#4" not in cal["patient"]

    # Versioned: an amendment that changes the calculation leaves this patient on the approved one.
    amended = copy.deepcopy(body)
    amended["baseModel"]["pattern_rules"][0]["program"]["do"]["record"]["date"]["args"][1]["args"][1] = 14
    for t in amended["baseModel"]["pattern_rules"][0]["tests"]:
        for item in t["expect"]:
            if item["date"]:
                item["date"] = "2026-01-20"
    amended.update(expectedVersion=1, resolutions={"generated_pk_after_given": "Amendment 1: 14 days."})
    amended.pop("analysisJobId")
    preview2 = app.client.post("/api/kit/workflow-preview", json=amended, headers=SPONSOR).json()
    assert not preview2["problems"], preview2["problems"]
    assert app.client.put("/api/trials/genpk/kit-model", headers=SPONSOR, json={
        **amended, "workflowAction": "approve", "workflowDigest": preview2["digest"]}).status_code == 200
    pinned = app.client.get(f"/api/patients/{pid}/kit", headers=PI).json()
    assert pinned["workflow_version"] == 1 and calendar(pinned)["staff"]["pk#2"]["planned"] == "2026-02-10"
    assert calendar({**pinned, "model": pinned["latest"]["model"]})["staff"]["pk#2"]["planned"] == "2026-02-17"


def test_upload_with_a_calculation_that_fails_its_own_test_cannot_be_approved_or_enrolled(app, monkeypatch):
    wrong = copy.deepcopy(PROGRAM)
    wrong["do"]["record"]["date"]["args"][1]["args"][1] = 6
    monkeypatch.setitem(harness.MODELS, "SYN-GEN-BAD", lambda: gen_model(program=wrong, number="SYN-GEN-BAD"))
    app.db.trials.docs.append({"id": "genbad", "title": "Synthetic PK (bad)", "protocol_id": "SYN-GEN-BAD"})
    body = analyze(app.client, TEXT.replace("SYN-GEN-PK", "SYN-GEN-BAD"))
    rule = body["baseModel"]["pattern_rules"][0]
    assert "its own test" in rule["blocked"] and "expected" in rule["blocked"]
    preview = app.client.post("/api/kit/workflow-preview", json={**body, "resolutions": {}}, headers=SPONSOR).json()
    assert any("its own test" in e for e in preview["workflow"]["errors"])
    assert app.client.put("/api/trials/genbad/kit-model", headers=SPONSOR, json={
        **body, "workflowAction": "approve", "workflowDigest": preview["digest"]}).status_code == 422
    invite = app.client.post("/api/patients/invite", headers=PI, json={
        "full_name": "Nobody", "phone": "+12025559998", "trial_id": "genbad", "subject_id": "X-2",
        "kit_model_version": 1, "kit": {"startDate": "2026-01-05"}})
    assert invite.status_code == 409


def test_the_fixture_files_and_offline_answer_match_this_analysis():
    """The TypeScript tests and offline mode use files written from gen_model(); they must not drift."""
    import hashlib
    raw = json.loads((Path(__file__).parent / "fixtures" / "kit_synthetic" / "gen_pk.raw.json").read_text(encoding="utf-8"))
    raw.pop("_fixture")
    assert raw == gen_model()
    answers = Path(__file__).resolve().parents[1] / "app" / "kit" / "offline_answers"
    manifest = json.loads((answers / "manifest.json").read_text(encoding="utf-8"))
    entry = next(p for p in manifest["protocols"] if p["name"] == "SYN-GEN-PK")
    text = (Path(__file__).resolve().parents[2] / entry["file"]).read_bytes()
    assert text == TEXT.encode("utf-8") and hashlib.sha256(text).hexdigest() == entry["sha256"]
    assert json.loads((answers / entry["model"]).read_text(encoding="utf-8")) == gen_model()


def test_the_synthetic_protocol_uploads_offline_with_no_provider(app, monkeypatch):
    """KIT_OFFLINE=1: the uploaded text is read for real and the prepared analysis answers; no provider exists."""
    import kit_routes

    class NoProvider:
        def __init__(self, *a, **k):
            raise AssertionError("offline mode must not build a provider client")
    monkeypatch.setenv("KIT_OFFLINE", "1")
    monkeypatch.setattr(kit_routes, "OpenAIKitClient", NoProvider)
    path = Path(__file__).resolve().parents[2] / "docs" / "protocol-benchmarks" / "synthetic" / "syn07-generated-calculation" / "protocol.txt"
    body = analyze(app.client, path.read_text(encoding="utf-8"))
    rule = body["baseModel"]["pattern_rules"][0]
    assert rule["program"] == PROGRAM and [t["passed"] for t in rule["generated"]["tests"]] == [True, True]
    assert harness.ScriptedProvider.calls == []
