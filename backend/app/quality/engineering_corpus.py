"""Rebuild versioned, explicitly SYNTHETIC rule/scenario fixtures.

Expected dates below are hand-written arithmetic, never copied from evaluation output.
Predictions are read from the constructed schedule, NOT from an external extraction model.
This corpus exercises engineering correctness and cannot qualify clinical release.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.application.pilot import build_pilot_amendment_schedule, build_pilot_schedule
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand, UnsupportedCondition
from app.domain.schedule.models import (
    ClaimEvidence, CyclePlan, Event, InterpretationStatus, LogicalOccurrenceState, OccurrenceFact,
    PatientContext, RecurrenceTermination,
    RepeatBlock, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, CycleDayTiming

from app.domain.schedule.amendment import propose_event_mappings
from .models import (Corpus, ExpectedCalendar, ExpectedEvidence, ExpectedField, ExpectedIdentity, ExpectedMapping,
                     ExpectedVisit, GoldRecord, Predictions, Scenario, SequenceScenario, Source, Step, Thresholds, digest)

AS_OF = date(2026, 1, 1)
HORIZON = date(2026, 2, 28)


def _stable(schedule: UniversalSchedule, label: str) -> UniversalSchedule:
    identifiers = {}
    def visit(value):
        if isinstance(value, dict):
            return {key: "2026-10-19T00:00:00Z" if key == "created_at" else visit(row) for key, row in value.items()}
        if isinstance(value, list):
            return [visit(row) for row in value]
        if isinstance(value, str):
            try:
                UUID(value)
            except ValueError:
                return value
            identifiers.setdefault(value, str(uuid5(NAMESPACE_URL, f"mtb/engineering-corpus/{label}/{len(identifiers)}")))
            return identifiers[value]
        return value
    return UniversalSchedule.model_validate(visit(schedule.model_dump(mode="json")))


def _expected(*, amendment=False, treatment=False, branch=False):
    visits = [ExpectedVisit(event_code="C1D1", status="RESOLVED", target=date(2026, 1, 11 if amendment else 8))]
    visits += [ExpectedVisit(event_code="DOSE", occurrence_index=n, status="RESOLVED", target=on)
               for n, on in enumerate([date(2026, 1, 15), date(2026, 1, 22), date(2026, 1, 29),
                                       date(2026, 2, 5)] + ([date(2026, 2, 12)] if amendment else []))]
    visits += [ExpectedVisit(event_code="IMAGING_BL" if amendment else "IMAGING_BASELINE",
                            status="NOT_APPLICABLE" if branch else "RESOLVED",
                            target=None if branch else date(2026, 1, 31),
                            earliest=None if branch else date(2026, 1, 28),
                            latest=None if branch else date(2026, 2, 3)),
               ExpectedVisit(event_code="LAB_AFTER_TX", status="RESOLVED" if treatment else "BLOCKED",
                             target=date(2026, 1, 22) if treatment else None)]
    if amendment:
        visits += [ExpectedVisit(event_code="SAFETY_CALL", status="RESOLVED", target=date(2026, 2, 15))]
    return visits


def _actual_sequence(schedule: UniversalSchedule) -> SequenceScenario:
    """Hand-written arithmetic (baseline 1 Jan): treatment given a week late on 15 Jan moves ONLY the labs
    (actual treatment + 7 days = 22 Jan); doses and imaging stay fixed to the baseline; an occurrence nothing
    depends on creates no evaluation; every earlier evaluation is retained unchanged."""
    return SequenceScenario(
        id="actual_relative_sequence", as_of=AS_OF, horizon=HORIZON, schedule=schedule, anchors={"BASELINE": AS_OF},
        steps=[
            Step(logical_key="C1D1:0", visit_date=date(2026, 1, 8), treatment_date=date(2026, 1, 15),
                 recorded_on=date(2026, 1, 16),
                 expect_moved=["LAB_AFTER_TX:0"], expect_reevaluated=True, expected=[
                     ExpectedCalendar(logical_key="C1D1:0", planned=date(2026, 1, 8), completed=True,
                                      actual_visit=date(2026, 1, 8), actual_treatment=date(2026, 1, 15)),
                     ExpectedCalendar(logical_key="LAB_AFTER_TX:0", planned=date(2026, 1, 22)),
                     ExpectedCalendar(logical_key="DOSE:0", planned=date(2026, 1, 15)),
                     ExpectedCalendar(logical_key="DOSE:3", planned=date(2026, 2, 5)),
                     ExpectedCalendar(logical_key="IMAGING_BASELINE:0", planned=date(2026, 1, 31),
                                      earliest=date(2026, 1, 28), latest=date(2026, 2, 3))]),
            Step(logical_key="LAB_AFTER_TX:0", visit_date=date(2026, 1, 23), recorded_on=date(2026, 1, 24),
                 expect_moved=[],
                 expect_reevaluated=False, expected=[
                     ExpectedCalendar(logical_key="LAB_AFTER_TX:0", planned=date(2026, 1, 22), completed=True,
                                      actual_visit=date(2026, 1, 23)),
                     ExpectedCalendar(logical_key="IMAGING_BASELINE:0", planned=date(2026, 1, 31))])])


def _expected_mappings() -> list[ExpectedMapping]:
    """Hand-written: V2 renames the imaging visit (needs review), adds a safety call, keeps the rest as they were."""
    same = [ExpectedMapping(source_codes=[code], target_codes=[code], mapping_type="EQUIVALENT")
            for code in ("C1D1", "DOSE", "LAB_AFTER_TX")]
    return [*same,
            ExpectedMapping(source_codes=["IMAGING_BASELINE"], target_codes=["IMAGING_BL"], mapping_type="RENAMED",
                            requires_review=True),
            ExpectedMapping(source_codes=[], target_codes=["SAFETY_CALL"], mapping_type="ADDED")]


def _proposed_mappings(older: UniversalSchedule, newer: UniversalSchedule) -> list[ExpectedMapping]:
    """The REAL mapping proposer's output, in the gold shape."""
    return [ExpectedMapping(source_codes=list(row.source_codes), target_codes=list(row.target_codes),
                            mapping_type=getattr(row.mapping_type, "value", row.mapping_type),
                            requires_review=getattr(row.review_status, "value", row.review_status) != "AUTO_CONFIRMED")
            for row in propose_event_mappings(older, newer)]


def build(root: Path) -> tuple[Corpus, Predictions, Thresholds]:
    sources = []; records = []; predicted = {}; identity_seen = {}; evidence_predicted = {}; mappings_predicted = {}
    folder = root / "backend/quality_corpus/sources"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ["linear", "actual_relative", "recurrence", "multi_track", "branch", "device", "amendment", "cycle", "conflict", "unsupported"]:
        schedule = (build_pilot_amendment_schedule if name == "amendment" else build_pilot_schedule)(
            uuid4(), f"SYNTHETIC engineering {name}")
        schedule.schedule_metadata.status = ScheduleStatus.APPROVED
        narrative = schedule.evidence[0].source_text + f" Constructed {name} case; no clinical sign-off."
        expected = _expected(amendment=name == "amendment", treatment=name == "actual_relative", branch=name == "branch")
        blockers = set()
        if name == "branch":
            event = next(row for row in schedule.events if row.code == "IMAGING_BASELINE")
            event.conditions = [ComparisonCondition(operator="EQUALS", left=LiteralOperand(value=1), right=LiteralOperand(value=2))]
            schedule.claim_evidence.append(ClaimEvidence(evidence_id=schedule.evidence[0].id,
                claim_type="CONDITION", claim_entity_type="EVENT", claim_entity_id=event.id, claim_path="conditions"))
            narrative += " Imaging only if the explicit condition 1 equals 2."
        if name == "cycle":
            evidence = schedule.evidence[0]
            event = Event(code="CYCLE_DAY1", protocol_label="Cycle Day 1", display_name="Cycle Day 1",
                          event_type="VISIT", timing=CycleDayTiming(cycle_reference="CYCLE", cycle={"type": "CURRENT"}, day=1),
                          evidence_refs=[evidence.id])
            block = RepeatBlock(code="CYCLES", protocol_label="Cycles", display_name="Cycles", event_codes=[event.code],
                                dependency_mode="NOMINAL", resume_mode="NOMINAL", evidence_refs=[evidence.id],
                                cycle_plan=CyclePlan(code="CYCLE", start_reference=AnchorReference(code="BASELINE"),
                                  length={"value": 21, "unit": "DAY"}, dependency_mode="NOMINAL",
                                  termination=RecurrenceTermination(type="COUNT", count=3)))
            claims = [ClaimEvidence(evidence_id=evidence.id, claim_type=kind, claim_entity_type=entity_type,
                                   claim_entity_id=entity.id, claim_path=path)
                      for entity_type, entity, kind, path in [
                          ("EVENT", event, "EVENT_NAME", "display_name"), ("EVENT", event, "TIMING", "timing"),
                          ("REPEAT_BLOCK", block, "REPEAT_BLOCK", "event_codes"),
                          ("REPEAT_BLOCK", block, "DEPENDENCY_MODE", "dependency_mode"),
                          ("REPEAT_BLOCK", block, "CYCLE_PLAN", "cycle_plan")]]
            schedule = UniversalSchedule(schedule_metadata=schedule.schedule_metadata,
                anchors=schedule.anchors, events=[event], repeat_blocks=[block], evidence=[evidence], claim_evidence=claims)
            expected = [ExpectedVisit(event_code=event.code, occurrence_index=n, status="RESOLVED", target=on)
                        for n, on in enumerate([date(2026, 1, 1), date(2026, 1, 22), date(2026, 2, 12)])]
            narrative = "SYNTHETIC: three nominal 21-day cycles from baseline; visit on Day 1 of every cycle."
        if name in {"conflict", "unsupported"}:
            event = next(row for row in schedule.events if row.code == "IMAGING_BASELINE")
            if name == "conflict":
                event.interpretation_status = InterpretationStatus.CONFLICTING
                blockers = {"CONFLICTING_EVIDENCE"}
                narrative += " Conflicting source interpretations have not been resolved."
            else:
                event.conditions = [UnsupportedCondition(source_operator="UNSUPPORTED_SYNTHETIC_RULE")]
                schedule.claim_evidence.append(ClaimEvidence(evidence_id=schedule.evidence[0].id,
                    claim_type="CONDITION", claim_entity_type="EVENT", claim_entity_id=event.id, claim_path="conditions"))
                blockers = {"UNSUPPORTED_OPERATOR"}
                narrative += " The imaging condition uses an unsupported operator; no automatic date is permitted."
            expected = []
        schedule.evidence[0].source_text = narrative
        schedule = _stable(schedule, name)
        source_path = folder / f"{name}.txt"
        source_path.write_bytes((narrative + "\n").encode("utf-8"))
        source = Source(id=f"synthetic-{name}", version="engineering.v1",
            sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(), path=source_path.relative_to(root).as_posix(),
            provenance="Constructed deterministic engineering fixture, not a real protocol",
            license="Project-authored synthetic test data", license_status="VERIFIED", deidentified=True,
            synthetic=True, constructs={name})
        context = PatientContext(patient_id=uuid5(NAMESPACE_URL, f"mtb/quality/patient/{name}"),
                                 schedule_version_id=schedule.schedule_version_id, anchors={"BASELINE": AS_OF})
        if name == "actual_relative":
            context.occurrence_facts = [OccurrenceFact(
                id="synthetic-treatment", event_code="C1D1", occurrence_index=0, kind="TREATMENT",
                value=date(2026, 1, 15), recorded_at=datetime(2026, 1, 15, tzinfo=timezone.utc))]
            context.occurrence_history = [LogicalOccurrenceState(
                event_code="C1D1", occurrence_index=0, original_planned_date=date(2026, 1, 8),
                latest_planned_date=date(2026, 1, 8), actual_treatment_date=date(2026, 1, 15), status="RESOLVED")]
        event = schedule.events[0]
        expected_value = ({"type": "CYCLE_DAY", "cycle_reference": "CYCLE", "cycle": {"type": "CURRENT", "number": None}, "day": 1}
                          if name == "cycle" else {"value": 10 if name == "amendment" else 7, "unit": "DAY"})
        path = "timing" if name == "cycle" else "timing/offset"
        oracle = ExpectedField(rule_id=event.code, path=path, value=expected_value)
        observed = event.timing.model_dump(mode="json") if name == "cycle" else event.timing.offset.model_dump(mode="json")
        sequences = [] if name != "actual_relative" else [_actual_sequence(schedule)]
        first_rule = schedule.events[0].code
        evidence_gold = [ExpectedEvidence(rule_id=first_rule, page=1, quote=narrative)]
        evidence_seen = [ExpectedEvidence(rule_id=first_rule, page=schedule.evidence[0].page_number or 1,
                                          quote=schedule.evidence[0].source_text or "unknown")]
        mappings_gold = _expected_mappings() if name == "amendment" else []
        mappings_seen = (_proposed_mappings(build_pilot_schedule(uuid4(), "SYNTHETIC engineering previous version"), schedule)
                         if name == "amendment" else [])
        record = GoldRecord(id=source.id, source_id=source.id, source_manifest_hash=digest(source.model_dump(mode="json")),
            category="ENGINEERING", fields=[oracle], scenarios=[Scenario(id=name, as_of=AS_OF, horizon=HORIZON,
                schedule=schedule, context=context, expected=expected, expected_validation_codes=blockers)],
            sequences=sequences, identity=ExpectedIdentity(status="MATCH", identifiers=[source.id]),
            evidence=evidence_gold, mappings=mappings_gold)
        sources.append(source); records.append(record)
        identity_seen[record.id] = ExpectedIdentity(status="MATCH", identifiers=[source.id])
        evidence_predicted[record.id] = evidence_seen
        if mappings_seen:
            mappings_predicted[record.id] = mappings_seen
        predicted[record.id] = [ExpectedField(rule_id=event.code, path=path, value=observed)]
    corpus = Corpus(dataset_version="engineering.2026-10-19.v1", sources=sources, records=records)
    predictions = Predictions(versions={"source": "constructed-pilot", "provider": "NONE_ENGINEERING_ONLY"}, fields=predicted,
                              identity=identity_seen, evidence=evidence_predicted, mappings=mappings_predicted)
    return corpus, predictions, Thresholds()


def main():
    root = Path(__file__).resolve().parents[3]
    corpus, predictions, thresholds = build(root)
    folder = root / "backend/quality_corpus"
    for name, value in [("manifest", corpus), ("predictions", predictions), ("thresholds", thresholds)]:
        (folder / f"{name}.json").write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print("Rebuilt synthetic engineering corpus. No clinical signatures or provider extraction claimed.")


if __name__ == "__main__":
    main()
