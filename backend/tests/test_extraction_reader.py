"""The AI reader (app/extraction/reader.py) with a scripted client - no network.

Checks what the reader asks, which pages it reads, that it parses strictly,
retries a malformed reply exactly once, and turns what it still cannot read
into visible issues rather than an empty answer.
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction import runner
from app.extraction.contracts import DocumentPage
from app.extraction.llm_client import ExtractionModelError, ExtractionNotConfigured
from app.extraction.reader import ReaderLimits, amendment_history_pages, plan_pages  # noqa: E402
from app.extraction.documents import looks_scrambled  # noqa: E402
from app.extraction.reader import (
    PAGES_PER_BATCH, SYSTEM_PROMPT, quote_found, read_document, read_trial_metadata, select_pages,
)

PAGES = {
    1: "PROTOCOL ABC-123. A Phase III study of Drug X in breast cancer. CTRI/2026/01/000001.",
    2: "Table of contents. Acknowledgements. Investigator signature page.",
    3: "Section 3.1 Eligibility. Pre-treatment evaluation: CBC and chemistry within 28 days before "
       "registration. PET/bone scan within 42 days prior to registration.",
    4: "Background and rationale for the study drug. Mechanism of action.",
    5: "Section 7. Schedule of Assessments. Day 1 is the day of registration. Visit 3: 4 weeks after "
       "registration +/- 3 days. Day 8: vital signs and CBC.",
    6: "Schedule of Assessments (continued) footnotes a-d.",
    7: "Section 8. Dose modification. Grade 3 toxicity: hold treatment until <= grade 1, then resume. "
       "Discontinue for progression.",
    8: "Section 10.3 Follow-up. Quality of life questionnaire at 3 months post completion of treatment. "
       "Follow-up every 3 months for 2 years.",
    9: "Statistical analysis plan. Sample size of 200 patients.",
}


def pages() -> list[DocumentPage]:
    return [DocumentPage(page_number=number, text=text) for number, text in PAGES.items()]


METADATA = {
    "trial": {"ctri_number": "CTRI/2026/01/000001", "title": "A Phase III study of Drug X",
              "phase": "Phase III", "indications": ["Breast cancer"], "drug": "Drug X",
              "duration": "", "target_enrollment": 200, "total_visits": None},
    "schedule_options": [],
    "day_numbering": {"day_1_anchor": "Registration", "counting_convention": "CLINICAL_DAY",
                      "evidence": [{"page": 5, "quote": "Day 1 is the day of registration"}]},
    "arms": [], "cohorts": [], "populations": [], "blocks": [],
    "visit_names": ["Screening labs", "Visit 3", "Day 8"],
    "anchor_names": ["Registration"],
}


def fact(what, page, quote, **extra):
    return {"what": what, "kind": "visit", "evidence": [{"page": page, "quote": quote}], **extra}


LABS = fact("Screening labs", 3, "CBC and chemistry within 28 days before registration",
            timing={"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"})
VISIT_3 = fact("Visit 3", 5, "Visit 3: 4 weeks after registration +/- 3 days",
               timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"},
               window={"before": "3 DAY", "after": "3 DAY"})


class ScriptedClient:
    """Answers the metadata question from ``metadata`` and each facts question
    from ``facts`` (a list consumed in order, or a callable of the prompt)."""

    def __init__(self, *, metadata=None, facts=None):
        self.metadata = metadata if metadata is not None else [json.dumps(METADATA)]
        self.facts = facts if facts is not None else []
        self.calls: list[dict] = []

    def complete(self, *, system, prompt, max_tokens=0):
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        source = self.metadata if "return one JSON object" in prompt else self.facts
        if callable(source):
            return source(prompt)
        if not source:
            return json.dumps({"facts": []})
        reply = source.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


# --- page selection ---------------------------------------------------------------

def test_page_selection_reads_tables_and_the_narrative_sections_timing_lives_in():
    selected = select_pages(pages())

    # Eligibility/pre-treatment (3), the schedule table and its continuation (5, 6),
    # dose modification / toxicity (7) and follow-up / QoL (8).
    assert selected == [3, 5, 6, 7, 8]


def test_image_only_pages_are_kept_for_a_reader_that_can_see_the_page_image():
    scanned = pages() + [DocumentPage(page_number=10, text="", text_status="image_or_empty")]

    assert 10 in select_pages(scanned)


def test_page_selection_is_bounded():
    many = [DocumentPage(page_number=n, text=PAGES[5]) for n in range(1, 200)]

    assert len(select_pages(many, limit=20)) == 20


def test_quote_matching_tolerates_pdf_noise_but_not_invented_words():
    page = "Pre-treatment evaluation: CBC and chem-\nistry within 28 days before registration."

    assert quote_found("CBC and chemistry within 28 days before registration", page)
    assert quote_found("within 28 days before   registration", page)
    assert not quote_found("CBC within 14 days after surgery and a biopsy", page)


# --- prompts and parsing ----------------------------------------------------------------

def test_the_prompt_forbids_dates_guesses_and_invented_windows():
    for rule in ("Never compute or write a patient calendar date", "Never guess",
                 "Never invent a window", "UNRESOLVED", "conflict_group", "WITHIN_BEFORE"):
        assert rule in SYSTEM_PROMPT


def test_read_document_makes_one_metadata_call_and_one_call_per_batch():
    client = ScriptedClient(facts=[{"facts": [LABS, VISIT_3]}])
    document = read_document(pages(), client, limits=RANKED)

    assert len(client.calls) == 2
    metadata_call, facts_call = client.calls
    assert "return one JSON object" in metadata_call["prompt"]
    # Text-only clients get the pages in the prompt, with page markers to cite.
    assert "--- page 3 ---" in facts_call["prompt"] and "--- page 9 ---" not in facts_call["prompt"]
    assert "Screening labs" in facts_call["prompt"]  # overview names passed on
    assert [item.what for item in document.facts] == ["Screening labs", "Visit 3"]
    assert document.day_numbering.day_1_anchor == "Registration"
    assert document.trial.target_enrollment == 200
    assert document.reader_issues == []
    assert [entry["node"] for entry in document.trace][:2] == ["reader.metadata", "reader.facts"]


def test_large_documents_are_read_in_bounded_batches():
    many = [DocumentPage(page_number=n, text=PAGES[5]) for n in range(1, 3 * PAGES_PER_BATCH + 1)]
    client = ScriptedClient()
    read_document(many, client)

    facts_calls = [call for call in client.calls if "return one JSON object" not in call["prompt"]]
    assert len(facts_calls) == 3


def test_a_malformed_reply_is_retried_once_then_used():
    client = ScriptedClient(facts=["Sure! Here are the facts: {not json", {"facts": [LABS]}])
    document = read_document(pages(), client, limits=RANKED)

    assert [item.what for item in document.facts] == ["Screening labs"]
    retry = client.calls[-1]["prompt"]
    assert "could not be used" in retry
    facts_trace = next(entry for entry in document.trace if entry["node"] == "reader.facts")
    assert facts_trace["attempts"] == 2 and facts_trace["status"] == "OK"


def test_pages_that_stay_unreadable_become_a_blocking_issue_not_an_empty_answer():
    two_batches = [DocumentPage(page_number=n, text=PAGES[5]) for n in range(1, PAGES_PER_BATCH + 3)]
    client = ScriptedClient(facts=["nope", "still nope", {"facts": [VISIT_3]}])
    document = read_document(two_batches, client)

    unread = [item for item in document.reader_issues if item.code == "EXTRACTION_PAGES_UNREAD"]
    assert len(unread) == 1 and unread[0].blocking
    assert unread[0].pages == list(range(1, PAGES_PER_BATCH + 1))
    assert [item.what for item in document.facts] == ["Visit 3"]


def test_when_no_batch_can_be_read_extraction_fails_loudly():
    client = ScriptedClient(facts=lambda prompt: "garbage")

    with pytest.raises(ExtractionModelError, match="no page batch"):
        read_document(pages(), client)


def test_a_transport_failure_is_not_retried_or_hidden():
    client = ScriptedClient(facts=[ExtractionModelError("model request failed: timeout")])

    with pytest.raises(ExtractionModelError, match="model request failed"):
        read_document(pages(), client)
    assert len(client.calls) == 2


def test_an_invalid_fact_is_reported_and_the_rest_are_kept():
    broken = {"what": "Mystery", "kind": "visit", "evidence": []}  # no citation
    client = ScriptedClient(facts=[{"facts": [broken, LABS]}])
    document = read_document(pages(), client)

    assert [item.what for item in document.facts] == ["Screening labs"]
    assert [item.code for item in document.reader_issues] == ["EXTRACTION_FACT_INVALID"]


def test_a_quote_not_on_its_cited_page_is_flagged():
    invented = fact("Echo", 5, "Echocardiogram every 12 weeks",
                    timing={"pattern": "OFFSET", "from": "Registration", "offset": "12 WEEK"})
    client = ScriptedClient(facts=[{"facts": [invented]}])
    document = read_document(pages(), client)

    flagged = [item for item in document.reader_issues if item.code == "EVIDENCE_QUOTE_NOT_FOUND"]
    assert len(flagged) == 1 and flagged[0].pages == [5]


def test_fact_ids_are_made_unique_and_conflict_links_follow_them():
    first = {**LABS, "id": "x", "conflicts_with": ["y"]}
    second = {**LABS, "id": "y", "timing": {"pattern": "UNRESOLVED", "reason": "unclear"}}
    client = ScriptedClient(facts=[{"facts": [first, second]}])
    document = read_document(pages(), client)

    ids = [item.id for item in document.facts]
    assert len(set(ids)) == 2
    assert document.facts[0].conflicts_with == [ids[1]]


def test_an_unreadable_overview_degrades_to_unstated_values_with_an_issue():
    client = ScriptedClient(metadata=["bad", "bad again"], facts=[{"facts": [LABS]}])
    document = read_document(pages(), client)

    assert document.day_numbering.day_1_anchor is None
    assert "EXTRACTION_OVERVIEW_UNREAD" in {item.code for item in document.reader_issues}


def test_a_client_that_takes_document_packets_gets_only_its_pages_attached():
    scoped_pages: list[list[int]] = []

    class PacketClient(ScriptedClient):
        def for_document(self, *, document_text, page_numbers):
            scoped_pages.append(page_numbers)
            return self

    client = PacketClient(facts=[{"facts": [LABS]}])
    read_document(pages(), client, limits=RANKED)

    assert scoped_pages[1] == [3, 5, 6, 7, 8]
    assert "--- page" not in client.calls[1]["prompt"]


def test_trial_metadata_alone_is_one_call():
    client = ScriptedClient()
    details = read_trial_metadata(pages(), client)

    assert len(client.calls) == 1
    assert details.ctri_number == "CTRI/2026/01/000001"


# --- runner wiring --------------------------------------------------------------------

def test_engine_is_available_only_with_an_explicit_provider_and_its_key(monkeypatch):
    monkeypatch.delenv("UCTSM_EXTRACTION_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert runner.engine_available() is False

    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "anthropic")
    assert runner.engine_available() is True

    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert runner.engine_available() is False

    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "something-else")
    assert runner.engine_available() is False
    with pytest.raises(ExtractionNotConfigured):
        runner.extract_schedules(pages(), pdf_bytes=None, document_hash="h")


def test_extract_schedules_runs_reader_then_compiler():
    client = ScriptedClient(facts=[{"facts": [LABS, VISIT_3]}])
    [(option, result)] = runner.extract_schedules(
        pages(), pdf_bytes=None, document_hash="h" * 64, schedule_name="protocol.pdf", client=client)

    assert option is None
    assert {item.code for item in result.schedule.events} == {"SCREENING_LABS", "VISIT_3"}
    assert result.schedule.schedule_metadata.day_numbering.day_1_anchor.code == "REGISTRATION"
    nodes = [entry["node"] for entry in result.extraction_trace]
    assert nodes[0] == "reader.metadata" and "compile" in nodes


# --- cycles, phased plans, planned anchors, occurrence rules, nested groups -------------

def test_the_facts_prompt_asks_for_planned_anchors_cycle_modes_occurrences_and_one_phased_plan():
    from app.extraction.reader import FACT_FORMAT, METADATA_PROMPT

    for text in ('"accepts": "PLANNED_OR_ACTUAL"', "ACTUAL_PREVIOUS_CYCLE_START", '"occurrences"',
                 "ONE PHASED_REPEAT fact", '"count": int or null', "starting_at"):
        assert text in FACT_FORMAT
    for text in ('"parts"', '"parent_type"', "ACTUAL_PREVIOUS_CYCLE_START"):
        assert text in METADATA_PROMPT


def test_new_fact_fields_round_trip_from_a_reply_through_the_compiler():
    metadata = {**METADATA,
                "parts": [{"name": "Part A", "evidence": [{"page": 5, "quote": "Part A"}]}],
                "cohorts": [{"name": "Cohort 1", "parent": "Part A", "parent_type": "part"}],
                "blocks": [{"name": "Treatment", "dependency_mode": "actual previous cycle start",
                            "evidence": [{"page": 7, "quote": "each cycle starts 21 days after"}]}]}
    surgery = fact("Surgery", 7, "surgery", kind="anchor", anchor={"type": "RECORDED"})
    pre_op = fact("Pre-op assessment", 7, "within 14 days before the planned surgery",
                  timing={"pattern": "WITHIN_BEFORE", "duration": "14 DAY",
                          "from": {"name": "Surgery", "accepts": "planned"}})
    day_1 = fact("Cycle day 1", 7, "Day 1 of each cycle", block="Treatment",
                 timing={"pattern": "CYCLE_DAY", "day": 1,
                         "cycle": {"start_anchor": "Cycle 1 Day 1", "length": "21 days"}},
                 repeat={"interval": "21 days", "end": {"type": "COUNT", "count": 4}})
    mri = {**fact("MRI", 7, "MRI every second cycle", at_visits=["Cycle day 1"]), "kind": "activity",
           "occurrence_note": "every second cycle", "occurrences": {"every": 2, "starting_at": 2}}
    client = ScriptedClient(metadata=[json.dumps(metadata)],
                            facts=[{"facts": [surgery, pre_op, day_1, mri]}])
    [(_option, result)] = runner.extract_schedules(
        pages(), pdf_bytes=None, document_hash="h" * 64, schedule_name="protocol.pdf", client=client)

    schedule = result.schedule
    events = {item.code: item for item in schedule.events}
    assert events["PRE_OP_ASSESSMENT"].timing.reference.accepts == "PLANNED_OR_ACTUAL"
    block = next(item for item in schedule.repeat_blocks if item.code == "TREATMENT")
    assert block.cycle_plan.dependency_mode == "ACTUAL_PREVIOUS_CYCLE_START"
    [mri_activity] = events["CYCLE_DAY_1"].activities
    assert mri_activity.conditions[0].values == [2, 4]
    [cohort] = schedule.cohorts
    assert (cohort.parent_dimension_type, cohort.parent_code) == ("PART", "PART_A")


# --- read every page (product default) -------------------------------------------------

#: The cheaper keyword-ranked mode, kept for the tests that describe it.
RANKED = ReaderLimits()


def test_the_product_default_reads_every_page_not_only_keyword_matches(monkeypatch):
    monkeypatch.delenv("UCTSM_EXTRACTION_READ_ALL_PAGES", raising=False)
    assert ReaderLimits.from_env().read_all_pages is True
    client = ScriptedClient()
    document = read_document(pages(), client)
    read = document.trace[-1]["selected"]
    # Page 9 (statistics) has no schedule keyword; it is read all the same.
    assert read == [page.page_number for page in pages()]
    assert document.accountability["coverage"]["status"] == "COMPLETE"


def test_scrambled_font_text_is_treated_as_an_image_page():
    # What a broken font map produces: every character shifted by a fixed amount.
    sentence = ("Patients should be randomized within three calendar days of the first day and those "
                "randomized to treatment should meet with a person who is experienced in giving advice. ")
    soup = "".join(chr(ord(char) - 29) if char.isalpha() else char for char in sentence * 2)
    assert looks_scrambled(soup)
    assert not looks_scrambled("Patients will return to the clinic on Day 1 of each week of Cycle 1 "
                               "for vital signs, hematology and selinexor dosing as specified. " * 2)
    # Abbreviation-heavy but real text is not scrambled.
    assert not looks_scrambled("CBC with differential, ECG, ECOG, BSA, PT, PTT, TSH, LDH, ALT, AST and "
                               "bilirubin are assessed at screening and on the first day of every cycle "
                               "for all patients in both treatment arms of the study.")


def test_amendment_history_pages_are_never_a_schedule_source():
    body = DocumentPage(page_number=1, text="Visit 4 on Cycle 1 Day 4 (+1 day): review temperature diary.")
    history = [
        DocumentPage(page_number=2, text="Amendment rationale. A summary of the key changes is provided below."),
        DocumentPage(page_number=3, text=""),  # text did not extract; still part of the listing
        DocumentPage(page_number=4, text="Old text: Cycle 1 Day 3 phone call has been changed to: Day 4 visit. "
                                         "Modified sections: 9.2.4"),
    ]
    document = [body, *history]
    assert amendment_history_pages(document) == {2, 3, 4}
    plan = plan_pages(document, read_all=True, excluded=amendment_history_pages(document))
    assert plan["selected"] == [1]
    # A body page that merely mentions an amendment once is not history.
    assert amendment_history_pages([DocumentPage(
        page_number=1, text="See Appendix 7: Protocol amendments, rationales and summaries of changes.")]) == set()
