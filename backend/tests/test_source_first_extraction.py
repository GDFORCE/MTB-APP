import json
from copy import deepcopy

import pytest

from app.extraction.graph import DocumentPage
from app.extraction.prompts import CATEGORY_PROMPTS
from app.extraction.source_first import CLAIM_GROUPS, run_source_first
from app.extraction.source_inventory import SourceUnit, reconcile_sources
from test_uctsm_llm_extraction import ScriptedClient, script


PAGES = [DocumentPage(page_number=8, text="C1D1\nECG X\na. ECG must be performed pre-dose.")]


def responses():
    old = ScriptedClient(script())
    groups = {group[0]: {category: json.loads(old.complete(system="", prompt=CATEGORY_PROMPTS[category]))["claims"]
                        for category in group} for group in CLAIM_GROUPS}
    groups = json.loads(json.dumps(groups).replace('"e1"', '"source-8-1"').replace('"e2"', '"source-8-3"'))
    groups["activities"]["activities"][0]["evidence_ids"] = ["source-8-2"]
    return {
        "SOURCE_MAP:": {"regions": [{"label": "SOA", "pages": [8], "kind": "TABLE"}], "uncertainties": []},
        "SOURCE_RECONSTRUCTION:": {"pages_read": [8], "uncertainties": [], "units": [
            {"page": 8, "scope": "PATIENT_SCHEDULE", "kind": "VISIT", "text": "C1D1", "table": "SOA", "visit_label": "C1D1"},
            {"page": 8, "scope": "PATIENT_SCHEDULE", "kind": "ASSESSMENT", "text": "X", "table": "SOA", "row": "ECG", "column": "C1D1", "visit_label": "C1D1", "activity_label": "ECG"},
            {"page": 8, "scope": "PATIENT_SCHEDULE", "kind": "FOOTNOTE", "text": "a. ECG must be performed pre-dose.", "marker": "a"},
        ]},
        "SOURCE_RECONCILIATION:": {"schedules": [{"id": "primary", "label": "Primary",
            "source_ids": ["source-8-1", "source-8-2", "source-8-3"], "assignment_evidence_ids": [],
            "visits": [{"code": "C1D1", "label": "C1D1", "source_ids": ["source-8-1"]}]}], "uncertainties": []},
        **{f"SOURCE_SEMANTICS: Extract these related categories together: {name}": answer for name, answer in groups.items()},
        "SOURCE_AUDIT:": {"pages_checked": [8], "findings": []},
    }


def run(answers):
    client = ScriptedClient(answers)
    results = run_source_first(client, PAGES, document_hash="a" * 64)
    return results[0][1], client


def test_reconciled_sources_retain_visits_cells_and_footnotes_with_fewer_model_calls():
    result, client = run(responses())
    assert result.schedule is not None
    assert [event.code for event in result.schedule.events] == ["C1D1"]
    assert result.schedule.events[0].activities[0].display_name == "ECG"
    coverage = result.schedule.schedule_metadata.extensions[-1]["items"]
    assert [item["status"] for item in coverage] == ["RETAINED"] * 3
    assert len(client.prompts) == 8  # map + reconstruction + reconciliation + 4 groups + audit
    assert set(CATEGORY_PROMPTS) == {category for group in CLAIM_GROUPS for category in group}


def test_continuation_table_keeps_both_assessments_on_one_reconciled_visit():
    answers = responses()
    answers["SOURCE_MAP:"]["regions"][0]["pages"] = [8, 9]
    batch = answers["SOURCE_RECONSTRUCTION:"]
    batch["pages_read"] = [8, 9]
    batch["units"].extend([
        {"page": 9, "scope": "PATIENT_SCHEDULE", "kind": "VISIT", "text": "C1D1", "visit_label": "C1D1", "table": "SOA continued"},
        {"page": 9, "scope": "PATIENT_SCHEDULE", "kind": "ASSESSMENT", "text": "X", "visit_label": "C1D1", "activity_label": "CBC", "table": "SOA continued", "row": "CBC", "column": "C1D1"},
    ])
    group = answers["SOURCE_RECONCILIATION:"]["schedules"][0]
    group["source_ids"].extend(["source-9-4", "source-9-5"])
    group["visits"][0]["source_ids"].append("source-9-4")
    answers["SOURCE_SEMANTICS: Extract these related categories together: protocol_metadata"]["events"][0]["evidence_ids"].append("source-9-4")
    activities = answers["SOURCE_SEMANTICS: Extract these related categories together: activities"]["activities"]
    cbc = deepcopy(activities[0])
    cbc.update(claim_id="ac2", evidence_ids=["source-9-5"])
    cbc["candidate"].update(code="CBC", protocol_label="CBC", display_name="CBC")
    activities.append(cbc)
    answers["SOURCE_AUDIT:"]["pages_checked"] = [8, 9]
    results = run_source_first(ScriptedClient(answers), [*PAGES, DocumentPage(page_number=9, text="SOA continued\nC1D1\nCBC X")], document_hash="a" * 64)
    assert len(results) == 1
    result = results[0][1]
    assert len(result.schedule.events) == 1
    assert {activity.display_name for activity in result.schedule.events[0].activities} == {"ECG", "CBC"}
    coverage = next(step for step in result.extraction_trace if step["node"] == "SOURCE_COVERAGE")["items"]
    assert len(coverage) == 5 and all(item["status"] == "RETAINED" for item in coverage)


def test_dropped_assessment_cannot_look_complete_even_if_audit_says_no_findings():
    answers = responses()
    answers["SOURCE_SEMANTICS: Extract these related categories together: activities"]["activities"] = []
    result, _ = run(answers)
    issue = next(issue for issue in result.issues if issue.issue_code == "SOURCE_CONTENT_UNMAPPED")
    assert issue.details["source_id"] == "source-8-2" and issue.blocking


@pytest.mark.parametrize("anchor_code", ["FIRST_DOSE", "RANDOMIZATION", "SURGERY_DATE"])
def test_missing_reference_date_is_recovered_only_with_a_cited_definition(anchor_code):
    answers = responses()
    metadata = answers["SOURCE_SEMANTICS: Extract these related categories together: protocol_metadata"]
    anchor = deepcopy(metadata["anchors"][0])
    anchor["candidate"].update(code=anchor_code, display_name=anchor_code, anchor_type="OTHER")
    metadata["anchors"] = []
    timing = answers["SOURCE_SEMANTICS: Extract these related categories together: event_types"]["timing"][0]
    timing["candidate"]["timing"]["reference"]["code"] = anchor_code
    answers["SOURCE_ANCHOR_RECOVERY:"] = {"claims": [anchor]}
    result, client = run(answers)
    assert [anchor.code for anchor in result.schedule.anchors] == [anchor_code]
    assert not any(issue.issue_code == "UNKNOWN_ANCHOR" for issue in result.issues)
    assert len(client.prompts) == 9
    assert any(claim.claim_type == "ANCHOR" for claim in result.schedule.claim_evidence)


@pytest.mark.parametrize("answer_kind", ["absent", "uncited", "duplicate", "unrequested", "failure"])
def test_anchor_recovery_does_not_invent_or_choose_a_reference_date(answer_kind):
    answers = responses()
    metadata = answers["SOURCE_SEMANTICS: Extract these related categories together: protocol_metadata"]
    anchor = deepcopy(metadata["anchors"][0])
    metadata["anchors"] = []
    if answer_kind == "uncited":
        anchor["evidence_ids"] = ["nonexistent-source"]
    if answer_kind == "unrequested":
        anchor["candidate"]["code"] = "GUESSED_BASELINE"
    answers["SOURCE_ANCHOR_RECOVERY:"] = ("not JSON" if answer_kind == "failure" else
        {"claims": [] if answer_kind == "absent" else [anchor, deepcopy(anchor)] if answer_kind == "duplicate" else [anchor]})
    result, client = run(answers)
    assert result.schedule.anchors == []
    assert any(issue.issue_code == "UNKNOWN_ANCHOR" and issue.blocking for issue in result.issues)
    assert len(client.prompts) == 9  # no repeated recovery loop


def test_existing_anchor_label_is_not_recovered_as_a_second_anchor():
    answers = responses()
    timing = answers["SOURCE_SEMANTICS: Extract these related categories together: event_types"]["timing"][0]
    timing["candidate"]["timing"]["reference"]["code"] = "First Dose"
    result, client = run(answers)
    assert [anchor.code for anchor in result.schedule.anchors] == ["FIRST_DOSE"]
    assert result.schedule.events[0].timing.reference.code == "FIRST_DOSE"
    assert len(client.prompts) == 8


def test_assessment_header_code_matches_the_visit_with_a_long_display_label():
    answers = responses()
    answers["SOURCE_RECONCILIATION:"]["schedules"][0]["visits"][0]["label"] = "Cycle 1 Day 1"
    result, _ = run(answers)
    assert not any(issue.issue_code == "SOURCE_CELL_WRONG_VISIT" for issue in result.issues)


def test_explicit_detailed_occasion_link_overrides_an_overview_header_label():
    from app.extraction.source_first import SourceFirstProvider, source_coverage
    from app.extraction.source_inventory import ScheduleGroup
    from app.extraction.graph import ExtractionResult
    from app.domain.schedule.models import ClaimEvidence, Event, ScheduleMetadata, UniversalSchedule
    from app.domain.schedule.timing import UnresolvedTiming
    from uuid import uuid4
    unit = SourceUnit(id="overview-cell", page=1, scope="PATIENT_SCHEDULE", kind="ASSESSMENT",
        text="X", visit_label="Treatment period", activity_label="ECG")
    group = ScheduleGroup(id="one", label="One", assignment_evidence_ids=[], source_ids=[unit.id], visits=[
        {"code": "HOUR_1", "label": "Hour 1", "source_ids": [unit.id]},
        {"code": "CONTINUOUS", "label": "Treatment period", "source_ids": ["monitoring"]},
    ])
    provider = SourceFirstProvider(ScriptedClient({}), group, [unit])
    evidence_id = uuid4()
    provider._evidence_ids[unit.id] = evidence_id
    event = Event(code="HOUR_1", protocol_label="Hour 1", display_name="Hour 1", event_type="ASSESSMENT",
        timing=UnresolvedTiming(reason="Test only"))
    schedule = UniversalSchedule(schedule_metadata=ScheduleMetadata(name="One"), events=[event], claim_evidence=[
        ClaimEvidence(claim_entity_type="EVENT", claim_entity_id=event.id, claim_type="ACTIVITY", evidence_id=evidence_id)])
    coverage, issues = source_coverage(provider, ExtractionResult(schedule=schedule, issues=[], extraction_trace=[]))
    assert coverage[0]["status"] == "RETAINED"
    assert not any(issue.issue_code == "SOURCE_CELL_WRONG_VISIT" for issue in issues)


def test_reconciled_visit_timing_must_be_retained_on_that_visit():
    answers = responses()
    answers["SOURCE_RECONSTRUCTION:"]["units"].append({"page": 8, "scope": "PATIENT_SCHEDULE",
        "kind": "TIMING", "text": "Day 1", "visit_label": "C1D1"})
    group = answers["SOURCE_RECONCILIATION:"]["schedules"][0]
    group["source_ids"].append("source-8-4")
    group["visits"][0]["source_ids"].append("source-8-4")
    group["visits"][0]["timing_source_ids"] = ["source-8-4"]
    # The model still cites only the visit-name source for timing: retaining
    # another field or a phase label cannot substitute for this timing source.
    result = run_source_first(ScriptedClient(answers), [DocumentPage(page_number=8, text=PAGES[0].text + "\nDay 1")], document_hash="a" * 64)[0][1]
    issue = next(issue for issue in result.issues if issue.issue_code == "SOURCE_VISIT_TIMING_UNMAPPED")
    assert issue.blocking and issue.details["visit"] == "C1D1"


def test_audit_failure_and_incomplete_page_coverage_block_approval():
    for answer, expected in [("not json", "SOURCE_AUDIT_FAILED"), ({"pages_checked": [], "findings": []}, "SOURCE_AUDIT_INCOMPLETE")]:
        answers = responses()
        answers["SOURCE_AUDIT:"] = answer
        result, _ = run(answers)
        assert any(issue.issue_code == expected and issue.blocking for issue in result.issues)


def test_missing_semantic_category_is_a_failure_not_an_empty_answer():
    answers = responses()
    del answers["SOURCE_SEMANTICS: Extract these related categories together: event_types"]["timing"]
    result, _ = run(answers)
    assert any(issue.issue_code == "SOURCE_SEMANTIC_GROUP_FAILED" for issue in result.issues)


def test_bad_category_does_not_erase_valid_visits_from_the_same_group():
    answers = responses()
    answers["SOURCE_SEMANTICS: Extract these related categories together: protocol_metadata"]["epochs"] = "bad shape"
    result, client = run(answers)
    assert result.schedule and [event.code for event in result.schedule.events] == ["C1D1"]
    failure = next(issue for issue in result.issues if issue.issue_code == "SOURCE_SEMANTIC_GROUP_FAILED")
    assert failure.details["categories"] == ["epochs"]
    assert len(client.prompts) == 9


def test_malformed_json_gets_one_format_repair_and_transport_failure_does_not():
    from app.extraction.llm_client import ExtractionModelError
    for failure in ["truncated JSON", ExtractionModelError("quota exhausted")]:
        class OnceBadClient(ScriptedClient):
            broken = False
            calls_to_group = 0
            def complete(self, **kwargs):
                if kwargs["prompt"].startswith("SOURCE_SEMANTICS: Extract these related categories together: protocol_metadata"):
                    self.calls_to_group += 1
                    if not self.broken:
                        self.broken = True
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                return super().complete(**kwargs)
        client = OnceBadClient(responses())
        result = run_source_first(client, PAGES, document_hash="a" * 64)[0][1]
        if isinstance(failure, Exception):
            assert client.calls_to_group == 1
            assert any(issue.issue_code == "SOURCE_SEMANTIC_GROUP_FAILED" for issue in result.issues)
        else:
            assert client.calls_to_group == 2
            assert result.schedule and len(result.schedule.events) == 1
            assert not any(issue.issue_code == "SOURCE_SEMANTIC_GROUP_FAILED" for issue in result.issues)


def test_unverified_reconstruction_is_reported():
    answers = responses()
    answers["SOURCE_RECONSTRUCTION:"]["units"][1]["text"] = "fabricated passage"
    result, _ = run(answers)
    assert any(issue.issue_code == "SOURCE_QUOTE_UNVERIFIED" for issue in result.issues)


@pytest.mark.parametrize("failure_kind", ["format", "transport", "single_page"])
def test_malformed_source_packets_split_without_retrying_transport_failures(failure_kind):
    from app.extraction.llm_client import ExtractionModelError
    from app.extraction.source_inventory import reconstruct_sources
    class PacketClient:
        def __init__(self, numbers=None, calls=None):
            self.numbers = numbers
            self.calls = calls if calls is not None else []
        def for_document(self, *, document_text, page_numbers):
            return PacketClient(page_numbers, self.calls)
        def complete(self, *, system, prompt):
            if prompt.startswith("SOURCE_MAP:"):
                return json.dumps({"regions": [{"label": "SOA", "pages": [1, 2, 3], "kind": "TABLE"}], "uncertainties": []})
            self.calls.append(self.numbers)
            if failure_kind == "transport":
                raise ExtractionModelError("quota exhausted")
            if len(self.numbers) > 1 or (failure_kind == "single_page" and self.numbers == [2]):
                return "truncated {"
            number = self.numbers[0]
            return json.dumps({"pages_read": self.numbers, "uncertainties": [], "units": [
                {"page": number, "scope": "PATIENT_SCHEDULE", "kind": "VISIT", "text": f"Day {number}"}]})
    client = PacketClient()
    units, issues, trace = reconstruct_sources(client, [DocumentPage(page_number=i, text=f"Day {i}") for i in [1, 2, 3]])
    if failure_kind == "transport":
        assert client.calls == [[1, 2, 3]] and not units
    else:
        assert client.calls == [[1, 2, 3], [1], [2, 3], [2], [3]]
        assert [unit.page for unit in units] == ([1, 3] if failure_kind == "single_page" else [1, 2, 3])
        assert len({unit.id for unit in units}) == len(units)
        assert sum(item["node"] == "SOURCE_BATCH_SPLIT" for item in trace) == 2
    assert any(issue.issue_code == "SOURCE_BATCH_FAILED" and issue.blocking for issue in issues) == (failure_kind != "format")


def test_trial_monitoring_is_excluded_and_a_wrongly_classified_population_visit_is_blocked():
    from app.extraction.source_inventory import reconstruct_sources
    for scope in ["TRIAL_MONITORING", "PATIENT_SCHEDULE"]:
        answers = responses()
        answers["SOURCE_RECONSTRUCTION:"]["units"].append({"page": 8, "scope": scope,
            "kind": "VISIT", "text": "7 Patients Evaluated", "visit_label": "7 Patients Evaluated", "table": "Stopping boundaries"})
        pages = [DocumentPage(page_number=8, text=PAGES[0].text + "\n7 Patients Evaluated")]
        units, issues, trace = reconstruct_sources(ScriptedClient(answers), pages)
        assert all(unit.text != "7 Patients Evaluated" for unit in units)
        excluded = next(item for item in trace if item["node"] == "SOURCE_EXCLUSIONS")["items"]
        assert excluded[0]["text"] == "7 Patients Evaluated"
        assert any(issue.issue_code == "SOURCE_NON_PATIENT_CONTENT" for issue in issues) == (scope == "PATIENT_SCHEDULE")


def test_unstated_source_scope_is_not_assumed_to_be_a_patient_requirement():
    answers = responses()
    del answers["SOURCE_RECONSTRUCTION:"]["units"][0]["scope"]
    result, _ = run(answers)
    assert any(issue.issue_code == "SOURCE_SCOPE_UNRESOLVED" and issue.blocking for issue in result.issues)


def test_unresolved_variable_cadence_keeps_source_wording_without_inventing_an_interval():
    from app.domain.schedule.validator import ScheduleValidator
    answers = responses()
    wording = "Every 1-2 weeks for 4 weeks, then every 4-8 weeks until 6 months."
    answers["SOURCE_RECONSTRUCTION:"]["units"].append({"page": 8, "scope": "PATIENT_SCHEDULE",
        "kind": "TIMING", "text": wording, "visit_label": "C1D1"})
    group = answers["SOURCE_RECONCILIATION:"]["schedules"][0]
    group["source_ids"].append("source-8-4")
    group["visits"][0]["source_ids"].append("source-8-4")
    claim = answers["SOURCE_SEMANTICS: Extract these related categories together: event_types"]["timing"][0]
    claim["candidate"]["timing"] = {"type": "UNRESOLVED", "reason": "Variable cadence needs an explicit scheduling policy."}
    claim["evidence_ids"] = ["source-8-4"]
    result = run_source_first(ScriptedClient(answers), [DocumentPage(page_number=8, text=PAGES[0].text + "\n" + wording)], document_hash="a" * 64)[0][1]
    event = result.schedule.events[0]
    assert event.timing.type == "UNRESOLVED"
    assert event.timing.source_reference["statements"][0] == {"text": wording, "page": 8, "source_id": "source-8-4"}
    assert event.recurrence is None
    assert ScheduleValidator.blocking(ScheduleValidator().validate(result.schedule))


def test_multiple_schedule_split_requires_assignment_evidence():
    answers = responses()
    first = answers["SOURCE_RECONCILIATION:"]["schedules"][0]
    other = deepcopy(first)
    other.update(id="followup", label="Followup")
    answers["SOURCE_RECONCILIATION:"]["schedules"].append(other)
    units = [SourceUnit.model_validate({**raw, "id": f"source-8-{i+1}"})
             for i, raw in enumerate(answers["SOURCE_RECONSTRUCTION:"]["units"])]
    _, issues = reconcile_sources(ScriptedClient(answers), units)
    assert sum(issue.issue_code == "SOURCE_SPLIT_UNSUPPORTED" for issue in issues) == 2


@pytest.mark.parametrize("repair_kind", ["complete", "drops_existing", "still_missing", "failure"])
def test_reconciliation_recovers_an_independent_assessment_without_losing_existing_visits(repair_kind):
    answers = responses()
    units = [SourceUnit.model_validate({**raw, "id": f"source-8-{i+1}"})
             for i, raw in enumerate(answers["SOURCE_RECONSTRUCTION:"]["units"])]
    units.append(SourceUnit(id="cadence", page=8, scope="PATIENT_SCHEDULE", kind="ASSESSMENT",
        activity_label="CBC", text="CBC every 2 weeks", visit_label=None))
    initial = answers["SOURCE_RECONCILIATION:"]
    initial["schedules"][0]["source_ids"].append("cadence")
    repaired = deepcopy(initial)
    repaired["schedules"][0]["visits"].append({"code": "CBC_REPEAT", "label": "CBC every 2 weeks",
        "source_ids": ["cadence"], "timing_source_ids": ["cadence"]})
    if repair_kind == "drops_existing":
        repaired["schedules"][0]["visits"] = repaired["schedules"][0]["visits"][1:]
    if repair_kind == "still_missing":
        repaired = deepcopy(initial)
    answers["SOURCE_RECONCILIATION_REPAIR:"] = "not JSON" if repair_kind == "failure" else repaired
    client = ScriptedClient(answers)
    result, issues = reconcile_sources(client, units)
    assert len(client.prompts) == 2
    assert "C1D1" in {visit.code for visit in result.schedules[0].visits}
    if repair_kind == "complete":
        assert issues == []
        assert {visit.code for visit in result.schedules[0].visits} == {"C1D1", "CBC_REPEAT"}
    else:
        assert any(issue.issue_code == "SOURCE_ASSESSMENT_UNMAPPED" and issue.blocking for issue in issues)
        assert len(result.schedules[0].visits) == 1


def test_unknown_pipeline_is_rejected(monkeypatch):
    from app.extraction.runner import run_extraction_for_all_options
    monkeypatch.setenv("UCTSM_EXTRACTION_PIPELINE", "typo")
    with pytest.raises(Exception, match="Unknown UCTSM"):
        run_extraction_for_all_options(ScriptedClient({}), PAGES, document_hash="a" * 64)


def test_numbered_day_with_window_uses_clinical_day_one_convention():
    from datetime import date
    from app.domain.schedule.evaluator import evaluate_timing
    from app.domain.schedule.timing import NominalWindowTiming
    from uuid import uuid4
    from app.domain.schedule.models import PatientContext
    patient = PatientContext(patient_id=uuid4(), schedule_version_id=uuid4(), anchors={"BASELINE": date(2026, 1, 1)})
    for day, expected in [(1, date(2026, 1, 1)), (29, date(2026, 1, 29)), (-2, date(2025, 12, 30))]:
        timing = NominalWindowTiming.model_validate({"type": "NOMINAL_WITH_WINDOW",
            "nominal": {"type": "PROTOCOL_DAY", "reference": {"kind": "ANCHOR", "code": "BASELINE"}, "day": day},
            "window": {"before": {"value": 3, "unit": "DAY"}, "after": {"value": 3, "unit": "DAY"}}})
        result = evaluate_timing(timing, patient)
        assert result.nominal_start == expected
        assert (result.latest - result.earliest).days == 6


def test_invalid_clinical_day_is_reportable_json_not_a_serialization_crash():
    from app.extraction.graph import run_extraction
    from app.extraction.claude_provider import ClaudeExtractionProvider
    answers = script()
    answers["state WHEN it occurs"]["claims"][0]["candidate"]["timing"]["day"] = 0
    result = run_extraction(ClaudeExtractionProvider(ScriptedClient(answers)), document_hash="a" * 64, pages=PAGES)
    encoded = result.model_dump_json()
    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" in encoded
