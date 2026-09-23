"""Phase 2 part 3: the legacy-vs-native pipeline diff harness (eval/pipeline_diff.py).

The two extraction calls themselves (run_legacy, run_native) make real,
billed model calls and are not exercised here - this proves the comparison
and reporting logic (diff_report), which is what actually decides whether a
protocol is safe to read as agreeing between pipelines, is correct on its
own, independent of any specific model's output that day.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount

from pipeline_diff import diff_report  # noqa: E402


def _schedule(event_codes: list[str]) -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=1, source_text="Schedule")
    events = [
        Event(
            code=code, protocol_label=code, display_name=code, event_type="SITE_VISIT",
            timing=OffsetTiming(reference=AnchorReference(code="BASELINE"),
                                 offset=TemporalAmount(value=index * 7, unit="DAY")),
            evidence_refs=[evidence.id],
        )
        for index, code in enumerate(event_codes)
    ]
    claims = [
        ClaimEvidence(evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                      claim_entity_id=event.id, claim_path=path)
        for event in events
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        events=events, evidence=[evidence], claim_evidence=claims,
    )


def test_two_pipelines_agreeing_reports_zero_significant_changes():
    legacy = _schedule(["C1D1", "C1D8"])
    native = _schedule(["C1D1", "C1D8"])

    report = diff_report(legacy, [], native, [])

    assert report["comparable"] is True
    assert report["clinically_significant_changes"] == 0
    assert report["added_events"] == []
    assert report["removed_events"] == []


def test_a_visit_only_one_pipeline_extracted_is_reported():
    legacy = _schedule(["C1D1"])
    native = _schedule(["C1D1", "C1D8"])

    report = diff_report(legacy, [], native, [])

    assert report["comparable"] is True
    assert report["added_events"] == ["C1D8"]
    assert report["legacy_event_count"] == 1
    assert report["native_event_count"] == 2


def test_a_timing_disagreement_between_pipelines_is_clinically_significant():
    legacy = _schedule(["C1D1", "C1D8"])
    native = _schedule(["C1D1", "C1D8"])
    # The native pipeline read Day 8 as Day 15 for the same visit.
    native.events[1].timing = OffsetTiming(
        reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=15, unit="DAY"))

    report = diff_report(legacy, [], native, [])

    assert report["clinically_significant_changes"] >= 1
    assert report["changes_by_type"].get("TIMING_CHANGED", 0) >= 1
    assert any("C1D8" in summary or "Day" in summary
               for summary in report["significant_change_summaries"])


def test_a_failed_pipeline_is_reported_as_not_comparable_with_its_reasons():
    legacy = _schedule(["C1D1"])

    report = diff_report(legacy, [], None, ["native extraction failed: provider timeout"])

    assert report["comparable"] is False
    assert report["native_issues"] == ["native extraction failed: provider timeout"]


def test_the_corpus_file_filter_only_matches_numbered_pdfs_in_order(tmp_path):
    from pipeline_diff import _corpus_files

    for name in ("10.protocol.pdf", "2.protocol.pdf", "notes.pdf", "1.protocol.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4")

    files = _corpus_files(tmp_path)

    assert [item.name for item in files] == [
        "1.protocol.pdf", "2.protocol.pdf", "10.protocol.pdf",
    ]
