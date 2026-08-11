"""Bounded evaluator/optimizer agent for protocol schedule extraction."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from protocol_extraction import (
    MAX_OUTPUT_TOKENS,
    ExtractedSchedule,
    expand_schedule,
)

log = logging.getLogger(__name__)


class ScheduleAuditIssue(BaseModel):
    """One evidence-backed problem found by the verification pass."""

    severity: Literal["critical", "major", "minor"]
    category: Literal[
        "missing_visit",
        "extra_visit",
        "timing",
        "window",
        "cycle_structure",
        "activity",
        "visit_type",
        "arm_or_period",
        "source_conflict",
        "overall_schedule",
        "other",
    ]
    finding: str = Field(
        description="Precisely what is wrong or missing in the candidate schedule.")
    evidence: str = Field(
        description="Protocol page/section/table evidence supporting the finding.")
    repair_instruction: str = Field(
        description="The smallest evidence-supported change needed to fix the issue.")


class ScheduleAccuracyDimension(BaseModel):
    """One independently judged dimension of schedule fidelity."""

    applicable: bool = Field(
        description="False only when the protocol contains nothing to score in this dimension.")
    accuracy: float | None = Field(
        ge=0,
        le=1,
        description="Estimated correctness for this dimension, or null when not applicable.")
    passed: bool = Field(
        description="True only when this dimension is sufficiently accurate for a review draft.")
    checked_items: list[str] = Field(
        default_factory=list,
        description="Page-cited facts actually compared with the candidate.")
    summary: str = ""

    @property
    def accepted(self) -> bool:
        if not self.applicable:
            return True
        return self.passed and self.accuracy is not None and self.accuracy >= 0.90


class ScheduleAudit(BaseModel):
    """Independent semantic audit of an extracted schedule against its PDF."""

    approved: bool = Field(
        description="True only when no critical or major evidence-backed issue remains.")
    confidence: float = Field(
        ge=0, le=1,
        description="Confidence in the audit evidence, not schedule accuracy.")
    visit_coverage: ScheduleAccuracyDimension
    timing: ScheduleAccuracyDimension
    windows: ScheduleAccuracyDimension
    visit_types: ScheduleAccuracyDimension
    procedure_mapping: ScheduleAccuracyDimension
    overall_schedule: ScheduleAccuracyDimension
    verified_items: list[str] = Field(
        default_factory=list,
        description="Important schedule facts explicitly checked against the PDF.")
    issues: list[ScheduleAuditIssue] = Field(default_factory=list)
    summary: str = ""

    @field_validator("approved")
    @classmethod
    def approval_is_boolean(cls, value):
        return bool(value)

    @property
    def accepted(self) -> bool:
        dimensions = (
            self.visit_coverage,
            self.timing,
            self.windows,
            self.visit_types,
            self.procedure_mapping,
            self.overall_schedule,
        )
        return (
            self.approved
            and all(dimension.accepted for dimension in dimensions)
            and not any(issue.severity in ("critical", "major") for issue in self.issues)
        )

    def accuracy_scores(self) -> dict[str, float | None]:
        return {
            "visit_coverage": self.visit_coverage.accuracy,
            "timing": self.timing.accuracy,
            "windows": self.windows.accuracy,
            "visit_types": self.visit_types.accuracy,
            "procedure_mapping": self.procedure_mapping.accuracy,
            "overall_schedule": self.overall_schedule.accuracy,
        }


class ScheduleDocumentMap(BaseModel):
    """Locations and high-level structure found before any schedule is built."""

    has_schedule: bool = Field(
        description="Whether the document contains a real visit schedule.")
    schedule_kind: str = Field(
        description="Likely structure: linear, cyclic, crossover, multi_arm, "
                    "multi_phase, intra_day, or none.")
    schedule_locations: list[str] = Field(
        default_factory=list,
        description="Page-cited schedule tables, flow charts, and appendices.")
    supporting_locations: list[str] = Field(
        default_factory=list,
        description="Page-cited dosing/design sections that define schedule rules.")
    arms_and_periods: list[str] = Field(
        default_factory=list,
        description="Distinct arms, cohorts, periods, washouts, and extensions.")
    baseline_anchor: str = Field(
        default="",
        description="The event treated as study Day 1/day offset zero.")
    notes: list[str] = Field(default_factory=list)


class ScheduleTimingEvidence(BaseModel):
    """Page-cited timing facts collected independently of visit construction."""

    visit_timing: list[str] = Field(default_factory=list)
    visit_windows: list[str] = Field(default_factory=list)
    cycle_rules: list[str] = Field(default_factory=list)
    relative_timing: list[str] = Field(default_factory=list)
    open_ended_rules: list[str] = Field(default_factory=list)
    conflicts_or_unknowns: list[str] = Field(default_factory=list)


class ScheduleVisitEvidence(BaseModel):
    """Page-cited visit-column, activity, and footnote facts."""

    visit_columns: list[str] = Field(default_factory=list)
    special_visits: list[str] = Field(default_factory=list)
    activity_assignments: list[str] = Field(default_factory=list)
    table_footnotes: list[str] = Field(default_factory=list)
    arm_period_differences: list[str] = Field(default_factory=list)
    conflicts_or_unknowns: list[str] = Field(default_factory=list)


_DISCOVERY_PROMPT = """You are the discovery stage of a clinical-protocol schedule
pipeline. Do not build a visit schedule yet. Locate every Schedule of Assessments,
Activities, Events, flow chart, and relevant appendix in the PDF. Then locate the study
design, treatment, dosing, and follow-up sections that define cadence. Map arms, cohorts,
periods, washouts, extensions, and the baseline/randomization anchor. Cite a page, section,
table, or nearby heading for every location. If this document has no visit schedule, say
so explicitly. Return only the requested discovery schema."""

_TIMING_PROMPT = """You are the timing specialist in a decomposed protocol-analysis
pipeline. Do not construct the final schedule and do not extract activity matrices.
Using the attached PDF and the supplied document map, collect every explicit visit day,
week, month, hour, window, cycle length/count, repetition range, relative-time rule, and
open-ended cadence. Search beyond the schedule table in dosing and treatment prose.
Preserve conflicts and unknowns instead of guessing. Every fact must carry a page,
section, table, footnote, or nearby-label citation. Return only the requested schema."""

_VISIT_EVIDENCE_PROMPT = """You are the visit-matrix specialist in a decomposed
protocol-analysis pipeline. Do not calculate final absolute offsets or construct the final
schedule. Using the attached PDF and supplied document map, inventory every visit column
and its protocol label, including screening, baseline, early termination, unscheduled,
safety follow-up, telephone, and hourly visits. Capture activities per column, table
footnotes, conditional activities, and genuine arm/period differences. Preserve unreadable
or conflicting evidence rather than guessing. Cite a page, table, footnote, or nearby label
for every fact. Return only the requested schema."""

_SYNTHESIS_PROMPT = """You are the synthesis stage of a decomposed clinical-protocol
schedule pipeline. Build the complete schedule from the attached PDF and the three
page-cited evidence packets in the user message. The evidence guides you but the PDF is
authoritative.

Use absolute day offsets with Day 1 = 0; screening before baseline is negative. Preserve
real visits whose timing is unknown with a null day offset. Use relative_to and
relative_offset_days for timing against another visit. Use hour offsets only for genuine
intra-day schedules. Preserve asymmetric windows.

For collapsed cycles, emit one repeating_blocks entry and let the server expand it; do
not enumerate repeated cycles manually. Keep explicitly different Cycle 1 visits outside
the block. Use separate blocks when cadence changes. Put cycle-specific procedures in
conditional_activities. Duplicate visits by arm only when timing genuinely differs, and
label crossover periods, washouts, and extensions. Include early termination, unscheduled,
telephone, and safety follow-up visits when present. Never invent missing facts: record
uncertainties or evidence conflicts in assumptions and cite source locations in
source_notes. If discovery shows no schedule and the PDF confirms it, return schedule_kind
none with no visits. Return only the requested schedule schema."""


_AUDIT_PROMPT = """You are the independent quality-control reviewer for a clinical-trial
visit schedule. Compare the candidate JSON against the attached protocol PDF from scratch.
Do not trust the candidate and do not merely critique its formatting.

Score these dimensions INDEPENDENTLY:
1. VISIT COVERAGE — is every Schedule of Assessments/Activities/Events column represented,
   including screening, baseline, early termination, unscheduled and safety follow-up?
2. TIMING — are all days, weeks, months, hours, relative offsets, cycle lengths/counts,
   repeating blocks, arms, periods and crossover/washout timing correct?
3. WINDOWS — is every symmetric or asymmetric +/- visit window preserved correctly?
4. VISIT TYPE — is each site, virtual, telephone, home, unscheduled, and other visit type
   classified correctly from the protocol?
5. PROCEDURE MAPPING — is each assessment/procedure attached to the correct visit column,
   including conditional-cycle activities and table footnotes?
6. OVERALL SCHEDULE — is the entire generated schedule structurally correct end to end,
   with no invented, omitted, or duplicated visits, activities, or timing values?

For each applicable dimension, list what was checked, assign its own accuracy, and pass it
only at 0.90 or higher. Mark a dimension not applicable only after confirming the protocol
contains no such information. Overall schedule accuracy is a separate end-to-end judgment,
NOT an average. For example, 95% procedure-mapping accuracy does not imply 95% overall
schedule accuracy. A strong dimension must never compensate for a weak one.

Also check treatment-plan prose elsewhere in the PDF that defines cadence or maximum
cycles. `confidence` measures confidence in your evidence review; it is not an accuracy
score. Set `approved` true only if every applicable dimension passes and no critical or
major evidence-backed issue remains.

Report an issue only when the PDF provides evidence. Cite a page, section, table heading,
footnote, or exact nearby label in every issue. Minor uncertainty belongs in an issue
rather than being silently treated as fact."""

_REPAIR_PROMPT = """You are the schedule correction specialist. Re-read the attached PDF,
especially every location cited by the audit, and return a COMPLETE replacement schedule
matching the requested schema.

Apply every evidence-backed audit repair. Preserve candidate facts that the audit did not
challenge. Search nearby footnotes and cross-referenced treatment-plan sections for each
missing fact. Never accept an audit claim blindly: if it conflicts with the PDF, retain
the PDF-supported value and explain the conflict in assumptions. Never invent a value.
Use repeating_blocks for collapsed cycles; do not manually enumerate them."""


Generate = Callable[..., Awaitable[BaseModel]]


class ExtractionAgentState(TypedDict, total=False):
    pdf_bytes: bytes
    document_map: ScheduleDocumentMap
    timing_evidence: ScheduleTimingEvidence
    visit_evidence: ScheduleVisitEvidence
    candidate: ExtractedSchedule
    audit: ScheduleAudit
    refinement_count: int
    max_refinements: int
    result: ExtractedSchedule


def build_schedule_extraction_graph(generate: Generate):
    """Compile discovery -> evidence -> synthesis -> audit -> repair."""

    async def discover_node(state: ExtractionAgentState):
        document_map = await generate(
            state["pdf_bytes"],
            "Map the protocol sections needed to reconstruct its visit schedule.",
            ScheduleDocumentMap,
            system_instruction=_DISCOVERY_PROMPT,
            max_tokens=3500,
        )
        return {"document_map": document_map, "refinement_count": 0}

    async def timing_node(state: ExtractionAgentState):
        timing = await generate(
            state["pdf_bytes"],
            "DOCUMENT MAP:\n" + state["document_map"].model_dump_json(),
            ScheduleTimingEvidence,
            system_instruction=_TIMING_PROMPT,
            max_tokens=5000,
        )
        return {"timing_evidence": timing}

    async def visit_evidence_node(state: ExtractionAgentState):
        visits = await generate(
            state["pdf_bytes"],
            "DOCUMENT MAP:\n" + state["document_map"].model_dump_json(),
            ScheduleVisitEvidence,
            system_instruction=_VISIT_EVIDENCE_PROMPT,
            max_tokens=7000,
        )
        return {"visit_evidence": visits}

    async def synthesize_node(state: ExtractionAgentState):
        evidence = (
            "DOCUMENT MAP:\n" + state["document_map"].model_dump_json()
            + "\n\nTIMING EVIDENCE:\n" + state["timing_evidence"].model_dump_json()
            + "\n\nVISIT/ACTIVITY EVIDENCE:\n" + state["visit_evidence"].model_dump_json()
        )
        candidate = await generate(
            state["pdf_bytes"],
            evidence,
            ExtractedSchedule,
            system_instruction=_SYNTHESIS_PROMPT,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        return {"candidate": candidate}

    async def audit_node(state: ExtractionAgentState):
        candidate_json = state["candidate"].model_dump_json(
            exclude={
                "verification_status",
                "verification_confidence",
                "verification_iterations",
                "verification_issues",
                "verification_scores",
            })
        audit = await generate(
            state["pdf_bytes"],
            "Audit this candidate schedule against the PDF:\n\n" + candidate_json,
            ScheduleAudit,
            system_instruction=_AUDIT_PROMPT,
            max_tokens=6000,
        )
        log.info(
            "schedule agent audit: refinement=%d accepted=%s confidence=%.2f issues=%d",
            state.get("refinement_count", 0), audit.accepted,
            audit.confidence, len(audit.issues),
        )
        return {"audit": audit}

    async def refine_node(state: ExtractionAgentState):
        candidate = state["candidate"].model_dump_json(
            exclude={
                "verification_status",
                "verification_confidence",
                "verification_iterations",
                "verification_issues",
                "verification_scores",
            })
        audit = state["audit"].model_dump_json()
        repaired = await generate(
            state["pdf_bytes"],
            "CANDIDATE SCHEDULE:\n" + candidate + "\n\nAUDIT:\n" + audit,
            ExtractedSchedule,
            system_instruction=_REPAIR_PROMPT,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        return {
            "candidate": repaired,
            "refinement_count": state.get("refinement_count", 0) + 1,
        }

    async def finalize_node(state: ExtractionAgentState):
        audit = state["audit"]
        unresolved = [
            f"Verification {issue.severity}: {issue.finding} Evidence: {issue.evidence}"
            for issue in audit.issues
            if issue.severity in ("critical", "major")
        ]
        if not audit.accepted and not unresolved:
            unresolved.append(
                "Verification did not approve this schedule, but returned no "
                "specific major finding. Review the protocol manually before saving.")
        candidate = state["candidate"]
        if unresolved:
            candidate = candidate.model_copy(update={
                "assumptions": list(candidate.assumptions) + unresolved,
            })
        result = expand_schedule(candidate).model_copy(update={
            "verification_status": "verified" if audit.accepted else "needs_review",
            "verification_confidence": audit.confidence,
            "verification_iterations": state.get("refinement_count", 0),
            "verification_issues": [issue.finding for issue in audit.issues],
            "verification_scores": audit.accuracy_scores(),
        })
        return {"result": result}

    def route_after_audit(state: ExtractionAgentState):
        if state["audit"].accepted:
            return "finalize"
        if state.get("refinement_count", 0) >= state["max_refinements"]:
            return "finalize"
        return "refine"

    graph = StateGraph(ExtractionAgentState)
    graph.add_node("discover", discover_node)
    graph.add_node("timing", timing_node)
    graph.add_node("visit_evidence", visit_evidence_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("audit", audit_node)
    graph.add_node("refine", refine_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "discover")
    graph.add_edge("discover", "timing")
    graph.add_edge("timing", "visit_evidence")
    graph.add_edge("visit_evidence", "synthesize")
    graph.add_edge("synthesize", "audit")
    graph.add_conditional_edges(
        "audit", route_after_audit,
        {"refine": "refine", "finalize": "finalize"},
    )
    graph.add_edge("refine", "audit")
    graph.add_edge("finalize", END)
    return graph.compile()


async def run_schedule_extraction_agent(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
) -> ExtractedSchedule:
    graph = build_schedule_extraction_graph(generate)
    final_state = await graph.ainvoke({
        "pdf_bytes": pdf_bytes,
        "max_refinements": max(0, min(max_refinements, 3)),
    })
    return final_state["result"]
