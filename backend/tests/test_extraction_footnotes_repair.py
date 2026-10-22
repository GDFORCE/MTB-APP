"""Extraction: a malformed fact is corrected once by the model, and every footnote is read.

Offline: every model answer is scripted. Nothing here converts a bare number to a unit or treats
a footnote as harmless on the model's word alone.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.domain.schedule.resolutions import checked_resolution  # noqa: E402
from app.extraction import ledger as L  # noqa: E402
from test_extraction_accountability import (  # noqa: E402
    BASE_FACTS, PAGES, REG, LABS, VISIT_2, CT, Scripted, codes, extract, fact, statement,
)

REPAIR_MARK = "could not be read by the schedule model"

BAD_VISIT_2 = {**VISIT_2, "window": {"before": 3, "after": 3}}          # bare numbers, no unit


class Repairing(Scripted):
    def __init__(self, *args, repair_with=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.repair_with = repair_with
        self.repair_calls = 0

    def complete(self, *, system, prompt, max_tokens=0):
        if REPAIR_MARK in prompt:
            self.repair_calls += 1
            self.calls.append(prompt)
            return json.dumps({"facts": self.repair_with(prompt)})
        return super().complete(system=system, prompt=prompt, max_tokens=max_tokens)


def corrected(_prompt):
    return [{**VISIT_2, "repair_id": "r1"}]                              # window with "3 DAY"


def still_wrong(_prompt):
    return [{**BAD_VISIT_2, "repair_id": "r1"}]


# --------------------------------------------------------------------------- repair
def test_a_fact_with_a_bare_number_where_a_duration_belongs_is_corrected_once_by_the_model():
    client = Repairing([REG, LABS, BAD_VISIT_2, CT], repair_with=corrected)
    result = extract(client)
    assert client.repair_calls == 1
    assert "EXTRACTION_FACT_INVALID" not in codes(result)
    visit = next(e for e in result.schedule.events if e.code == "VISIT_2")
    assert visit.timing is not None
    repaired = result.accountability["repaired_statements"]
    assert [item["statement_id"] for item in repaired] and "validation error" in repaired[0]["error"]


def test_a_correction_that_is_still_invalid_stays_rejected_and_says_it_was_retried():
    client = Repairing([REG, LABS, BAD_VISIT_2, CT], repair_with=still_wrong)
    result = extract(client)
    assert client.repair_calls == 1                    # exactly one round, never a loop
    invalid = [i for i in result.issues if i.issue_code == "EXTRACTION_FACT_INVALID"]
    assert invalid and invalid[0].details["repair_attempted"] is True and invalid[0].blocking
    assert [s for s in result.accountability["statements"] if s["disposition"] == L.INVALID]
    assert not any(e.code == "VISIT_2" for e in result.schedule.events)     # nothing was guessed


def test_a_valid_reply_makes_no_correction_call():
    client = Repairing(BASE_FACTS, repair_with=corrected)
    extract(client)
    assert client.repair_calls == 0


def test_the_model_cannot_use_a_correction_to_change_another_item():
    def wrong_id(_prompt):
        return [{**VISIT_2, "repair_id": "r99"}]
    client = Repairing([REG, LABS, BAD_VISIT_2, CT], repair_with=wrong_id)
    result = extract(client)
    assert "EXTRACTION_FACT_INVALID" in codes(result)


# ------------------------------------------------------------------------ footnotes
NO_EFFECT_TEXT = "Any assessments for unscheduled visits are done at the discretion of the Investigator."
RULE_TEXT = "Pregnancy test is required for female subjects who are not exempt; see Section 6.1."


def with_table_page(*lines):
    texts = dict(PAGES)
    texts[6] = "Table 1: Schedule of Events (continued)\n" + "\n".join(lines)
    return texts


def note(text, *, label="*", effect="NONE", reason="the investigator may choose; no rule is stated"):
    return {"table": "Table 1", "page": 6, "label": label, "text": text,
            **({"effect": effect, "effect_reason": reason} if effect else {})}


def footnote_statement(result, text):
    return next(item for item in result.accountability["statements"]
                if item["source"] == "FOOTNOTE" and item["citations"][0]["quote"] == text)


def test_a_footnote_with_no_scheduling_wording_is_recorded_as_read_with_its_reason():
    result = extract(Scripted(extra={6: {"footnotes": [note(NO_EFFECT_TEXT)]}}),
                     texts=with_table_page("* " + NO_EFFECT_TEXT))
    item = footnote_statement(result, NO_EFFECT_TEXT)
    assert item["disposition"] == L.NO_SCHEDULING_EFFECT and "no scheduling effect" in item["reason"]
    assert item["citations"][0]["verification"] == "TEXT_MATCHED"       # it was READ on its page
    assert "FOOTNOTE_NOT_INTERPRETED" not in codes(result)


def test_the_models_no_effect_claim_is_refused_when_the_footnote_has_schedule_wording():
    result = extract(Scripted(extra={6: {"footnotes": [note(RULE_TEXT, label="**")]}}),
                     texts=with_table_page("** " + RULE_TEXT))
    item = footnote_statement(result, RULE_TEXT)
    assert item["disposition"] == L.PENDING_INTERPRETATION
    assert "refused" in item["reason"] and "required" in item["reason"]
    assert "FOOTNOTE_NOT_INTERPRETED" in codes(result, blocking=True)


def test_a_footnote_the_model_did_not_classify_stays_pending():
    result = extract(Scripted(extra={6: {"footnotes": [note(NO_EFFECT_TEXT, effect=None)]}}),
                     texts=with_table_page("* " + NO_EFFECT_TEXT))
    assert footnote_statement(result, NO_EFFECT_TEXT)["disposition"] == L.PENDING_INTERPRETATION


def test_a_no_effect_claim_without_a_reason_or_not_found_on_its_page_stays_pending():
    no_reason = extract(Scripted(extra={6: {"footnotes": [note(NO_EFFECT_TEXT, reason="")]}}),
                        texts=with_table_page("* " + NO_EFFECT_TEXT))
    assert footnote_statement(no_reason, NO_EFFECT_TEXT)["disposition"] == L.PENDING_INTERPRETATION
    elsewhere = extract(Scripted(extra={6: {"footnotes": [note(NO_EFFECT_TEXT)]}}),
                        texts=with_table_page("* Something else entirely is printed here."))
    assert footnote_statement(elsewhere, NO_EFFECT_TEXT)["disposition"] == L.PENDING_INTERPRETATION


def test_a_footnote_printed_under_a_table_but_not_reported_is_still_accounted_for():
    result = extract(Scripted(), texts=with_table_page("* " + NO_EFFECT_TEXT, "** " + RULE_TEXT))
    printed = [item for item in result.accountability["statements"] if item["source"] == "FOOTNOTE"]
    assert {item["citations"][0]["quote"] for item in printed} == {NO_EFFECT_TEXT, RULE_TEXT}
    assert all(item["disposition"] == L.PENDING_INTERPRETATION for item in printed)   # nobody classified them
    assert "FOOTNOTE_NOT_INTERPRETED" in codes(result, blocking=True)
    assert {item["provenance"]["found_by"] for item in printed} == {"TEXT_DETECTED"}


def test_a_reported_footnote_is_not_added_a_second_time():
    result = extract(Scripted(extra={6: {"footnotes": [note(NO_EFFECT_TEXT)]}}),
                     texts=with_table_page("* " + NO_EFFECT_TEXT))
    assert len([s for s in result.accountability["statements"] if s["source"] == "FOOTNOTE"]) == 1


def test_a_page_without_a_table_never_yields_a_guessed_footnote():
    texts = dict(PAGES)
    texts[6] = "a. Within 7 days before the visit. This is ordinary text."
    result = extract(Scripted(), texts=texts)
    assert not [s for s in result.accountability["statements"] if s["source"] == "FOOTNOTE"]


# ------------------------------------------------------------------ typed resolution
def test_a_footnote_finding_accepts_the_no_scheduling_effect_resolution_and_nothing_else():
    assert checked_resolution("NO_SCHEDULING_EFFECT", "FOOTNOTE_NOT_INTERPRETED") == "NO_SCHEDULING_EFFECT"
    assert checked_resolution(None, "AMBIGUOUS_TIMING") is None
    with pytest.raises(ValueError):
        checked_resolution("NO_SCHEDULING_EFFECT", "AMBIGUOUS_TIMING")
    with pytest.raises(ValueError):
        checked_resolution("SOMETHING_ELSE", "FOOTNOTE_NOT_INTERPRETED")


def test_the_review_view_lists_each_open_footnote_once_with_every_issue_it_raised():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.review_projection import _footnote_findings

    def issue(code, status="OPEN", statement="b3-n1", page=23):
        return SimpleNamespace(
            id=uuid4(), issue_code=code, status=SimpleNamespace(value=status), message="m",
            details={"footnote": {"statement_id": statement, "label": "*", "table": "Table 1",
                                  "page": page, "text": NO_EFFECT_TEXT, "verification": "TEXT_MATCHED"}})

    shared_one, shared_two = issue("FOOTNOTE_NOT_INTERPRETED"), issue("FOOTNOTE_NOT_INTERPRETED")
    findings = _footnote_findings([
        shared_one, shared_two, issue("FOOTNOTE_NOT_INTERPRETED", statement="b3-n2", page=24),
        issue("FOOTNOTE_NOT_INTERPRETED", status="RESOLVED", statement="b3-n3"),
        issue("AMBIGUOUS_TIMING")])
    assert [item["footnote_id"] for item in findings] == ["b3-n1", "b3-n2"]
    assert findings[0]["issue_ids"] == [str(shared_one.id), str(shared_two.id)]


def test_a_quote_cited_on_the_wrong_page_stays_rejected_but_says_where_the_words_are():
    from app.extraction.contracts import DocumentPage
    from app.extraction.facts import Citation
    from app.extraction.reader import verify_citation

    pages = {1: DocumentPage(page_number=1, text="Nothing relevant on this page at all."),
             2: DocumentPage(page_number=2, text="All adverse events must be reported within one working day of awareness.")}
    citation = Citation(page=1, quote="All adverse events must be reported within one working day of awareness.")
    state, issue = verify_citation(citation, {1}, pages)
    assert state == "REJECTED" and issue.code == "EVIDENCE_QUOTE_NOT_FOUND" and issue.blocking
    assert issue.details["found_on_pages"] == [2] and "page 2" in issue.message


# ------------------------------------------------------------- naming, silence, resumable runs
def test_a_sentence_used_as_a_name_gets_a_short_stable_code_and_a_rename_warning():
    from app.extraction.compiler import code_for
    sentence = ("Use of the following medications will be collected at baseline and at each postoperative "
                "interval and recorded on the appropriate eCRF: Anticoagulants, Antiplatelets")
    code = code_for(sentence)
    assert len(code) <= 48 and code == code_for(sentence)
    assert code != code_for(sentence + " and statins")                # two different sentences never collide
    assert code_for("3-6 Months") == "E_3_6_MONTHS" and code_for("Visit 2") == "VISIT_2"   # short names unchanged

    result = extract(Scripted([REG, LABS, fact(sentence, 5, "Visit 2: 4 weeks after registration +/- 3 days",
                                               timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"}), CT]))
    warning = [i for i in result.issues if i.issue_code == "NAME_LOOKS_LIKE_A_SENTENCE"]
    assert warning and not warning[0].blocking
    assert all(len(e.code) <= 48 for e in result.schedule.events)


def test_an_unstated_day_1_says_which_pages_were_read():
    texts = dict(PAGES)
    meta = {**__import__("test_extraction_accountability").METADATA,
            "day_numbering": {"day_1_anchor": None, "counting_convention": None, "evidence": []}}
    result = extract(Scripted(metadata=meta), texts=texts)
    origin = [e for e in result.schedule.schedule_metadata.extensions if e.get("type") == "UNRESOLVED_SCHEDULE_ORIGIN"]
    assert origin and "found no statement that answers it" in origin[0]["detail"] and origin[0]["pages_read"]


def test_a_disk_store_lets_a_rerun_reuse_every_completed_model_answer(tmp_path):
    from app.extraction.artifacts import DiskArtifactStore

    first = Scripted()
    extract(first, artifacts=DiskArtifactStore(tmp_path))
    assert first.facts_calls > 0
    second = Scripted()
    extract(second, artifacts=DiskArtifactStore(tmp_path))           # a new process would behave the same
    assert second.facts_calls == 0 and second.calls == []


def test_a_failed_call_is_stored_for_diagnosis_but_never_reused(tmp_path):
    from app.extraction.artifacts import DiskArtifactStore
    from app.extraction.reader import ReaderLimits

    limits = ReaderLimits(pages_per_batch=1)
    broken = extract(Scripted(fail_on={2}), artifacts=DiskArtifactStore(tmp_path), limits=limits)
    assert "EXTRACTION_INTERRUPTED" in codes(broken, blocking=True)
    assert broken.accountability["summary"]["complete"] is False
    retry = Scripted()
    resumed = extract(retry, artifacts=DiskArtifactStore(tmp_path), limits=limits)
    assert 0 < retry.facts_calls < 9                                  # only the unfinished pages were asked again
    assert resumed.accountability["coverage"]["status"] == "COMPLETE"
