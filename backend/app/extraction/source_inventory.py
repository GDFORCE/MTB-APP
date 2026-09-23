"""Source reconstruction and reconciliation contracts, before schedule semantics.

An inventory is a model reading, not ground truth. Its coverage report proves
which inventoried assertions survived assembly; the final source audit checks
the independent question of whether the inventory itself missed anything.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.schedule.models import ValidationIssue
from app.extraction.graph import DocumentPage
from app.extraction.llm_client import ExtractionModelError, LLMClient, parse_json_object
from app.extraction.source_grounding import collective_count_visit, normalized_quote, page_packet, referenced_pages


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRegion(Contract):
    label: str
    pages: list[int] = Field(min_length=1)
    kind: Literal["TABLE", "NARRATIVE", "FOOTNOTES"]


class SourceMap(Contract):
    regions: list[SourceRegion]
    uncertainties: list[str]


class SourceUnit(Contract):
    id: str = ""  # assigned by code, never trusted from model output
    page: int = Field(ge=1)
    table: str | None = None
    row: str | None = None
    column: str | None = None
    kind: Literal["VISIT", "ASSESSMENT", "TIMING", "MODE", "APPLICABILITY", "FOOTNOTE", "CONTEXT"]
    scope: Literal["PATIENT_SCHEDULE", "TRIAL_MONITORING", "BACKGROUND", "UNRESOLVED"] = "UNRESOLVED"
    text: str = Field(min_length=1)
    visit_label: str | None = None
    activity_label: str | None = None
    marker: str | None = None


class SourceBatch(Contract):
    pages_read: list[int]
    units: list[SourceUnit]
    uncertainties: list[str]


class VisitIdentity(Contract):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    timing_source_ids: list[str] = Field(default_factory=list)


class ScheduleGroup(Contract):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    # Verbatim evidence is required before splitting participant timelines.
    assignment_evidence_ids: list[str]
    source_ids: list[str] = Field(min_length=1)
    visits: list[VisitIdentity] = Field(min_length=1)


class Reconciliation(Contract):
    schedules: list[ScheduleGroup]
    uncertainties: list[str]


def finding(code: str, message: str, **details) -> ValidationIssue:
    return ValidationIssue(issue_code=code, severity="ERROR", blocking=True,
        message=message, details={"origin": "EXTRACTION", **details})


class SourceContractError(ValueError):
    """The model answered, but its source response was incomplete or malformed."""


def ask(client: LLMClient, contract: type[Contract], instructions: str) -> Contract:
    # Transport failures are distinct from output failures: splitting a packet
    # cannot fix an unavailable provider or exhausted quota.
    raw = client.complete(
        system="Read the supplied clinical protocol faithfully. Never invent source content. Return JSON only.",
        prompt=instructions + "\nReturn this exact JSON schema:\n" + json.dumps(contract.model_json_schema()),
    )
    try:
        return contract.model_validate(parse_json_object(raw))
    except (ExtractionModelError, ValidationError) as error:
        raise SourceContractError(str(error)) from error


def reconstruct_sources(client: LLMClient, pages: list[DocumentPage]) -> tuple[list[SourceUnit], list[ValidationIssue], list[dict]]:
    source_map = ask(client, SourceMap,
        "SOURCE_MAP: Locate ALL schedule tables, continuations, footnotes, and narrative timing/"
        "recurrence/anchor/applicability instructions throughout the document. Include referenced sections, "
        "screening, treatment, follow-up, substudies, and intra-day sampling FOR THE CURRENT PROTOCOL. "
        "Exclude schedules, outcomes and patient counts describing prior studies, background literature, "
        "statistical stopping-boundary tables, sample-size calculations or trial-level interim analyses, "
        "or bibliographic references. A page can mix background and current instructions: retain only the "
        "current protocol's operative instructions. Use physical PDF page numbers, not printed page labels. "
        "Do not split schedules yet. Include uncertainties explicitly.")
    known = {page.page_number: page for page in pages}
    selected = {number for region in source_map.regions for number in region.pages}
    issues = [finding("SOURCE_MAP_UNCERTAIN", message) for message in source_map.uncertainties]
    unknown = selected - known.keys()
    if unknown:
        issues.append(finding("SOURCE_PAGE_INVALID", "Source map names pages outside this document.", pages=sorted(unknown)))
    selected &= known.keys()
    # Recover obvious schedule headings even if the model's map omitted them.
    # This is a retrieval backstop, not a claim to detect every layout.
    for page in pages:
        if re.search(r"schedule\s+of\s+(?:assessments|activities|events|visits)|study\s+schedule", page.text, re.I):
            selected.add(page.page_number)
    selected, missing_references = referenced_pages(pages, selected)
    issues.extend(finding("SOURCE_REFERENCE_MISSING",
        f"The protocol references {item['reference']} on physical page {item['page']}, but its heading was not found in this upload. Check whether the attachment is missing.",
        **item) for item in missing_references)
    if not selected:
        return [], [*issues, finding("SOURCE_SCHEDULE_NOT_FOUND", "No schedule source was located in this document.")], []
    # Include adjacent pages so continuation headers and footnotes retain context.
    selected |= {neighbor for number in list(selected) for neighbor in (number - 1, number + 1) if neighbor in known}
    ordered = sorted(selected)
    units: list[SourceUnit] = []
    excluded: list[dict] = []
    trace = [{"node": "SOURCE_MAP", "pages": ordered, "document_pages": len(pages)}]
    packets = [ordered[offset:offset + 3] for offset in range(0, len(ordered), 3)]
    while packets:
        numbers = packets.pop(0)
        try:
            batch = ask(page_packet(client, [known[number] for number in numbers]), SourceBatch,
                "SOURCE_RECONSTRUCTION: Read ONLY these physical pages: " + json.dumps(numbers) + ". "
                "First distinguish CURRENT protocol instructions from historical study descriptions. "
                "Do not create visits, assessments or timing units from background studies, literature "
                "results, patient counts, or references. Adjacent pages are context, not necessarily "
                "schedule pages. Patient cohorts/counts are never visits. On a mixed page, extract only "
                "the current protocol's operative requirements; report uncertainty about scope. "
                "Set scope for EVERY unit: PATIENT_SCHEDULE for an individual participant's visits, "
                "assessments, anchors and conditions; TRIAL_MONITORING for sample size, aggregate stopping "
                "boundaries or interim analysis; BACKGROUND for prior research; UNRESOLVED if unclear. "
                "Do not turn trial monitoring into participant visits or administrative activities. "
                "Patient-specific discontinuation and safety criteria ARE patient schedule context. "
                "Reconstruct table row/column relationships, including merged headers and continuation tables. "
                "Generic section headings such as 'During study' or 'Treatment assessments' are CONTEXT, "
                "not one visit covering the entire phase. For narrative schedules, create one VISIT unit "
                "for EACH distinct stated assessment occasion, with its own literal timepoint as the label. "
                "For example, 'ECG on Day 7, Week 4 and Week 12' gives THREE occasions and an ECG assessment "
                "for each. A repeating assessment gives a distinct repeating occasion; retain its complete "
                "cadence and phase boundaries as TIMING. Never collapse independent assessment cadences "
                "into a generic 'during study' visit. This identifies source occasions, not calendar dates. "
                "List each visit header as VISIT. List EVERY marked assessment cell separately as ASSESSMENT, "
                "with its activity_label, visit_label, full hierarchical row/column labels, table name and marker. "
                "Do not create units for blank assessment cells. Copy exact text for each unit; a cell may just be X. "
                "Use label fields for combined parent/child headers; do not put a synthesized combined "
                "label in the verbatim text field. Omit optional fields that have no value to keep the "
                "response compact, but never omit a marked cell to shorten the answer. "
                "Preserve all timing/window prose as TIMING, attendance values (Clinic, Telephone, etc.) as MODE, "
                "arm/cohort/conditional applicability instructions as APPLICABILITY, and every footnote definition as FOOTNOTE. "
                "Use CONTEXT for identity, arm/cohort, assignment and other supporting passages. "
                "Narrative-only schedules must also yield visits and assessments. Do not calculate dates or "
                "collapse different cycle days. Read table layout from the attached PDF when available. "
                "Report unreadable cells, ambiguous header associations and contradictory sources in uncertainties. "
                "pages_read must list every requested page actually inspected.")
            if set(batch.pages_read) != set(numbers):
                issues.append(finding("SOURCE_PAGES_UNREAD", "Not every requested source page was inspected.", requested=numbers, read=batch.pages_read))
            for unit in batch.units:
                if unit.page not in numbers:
                    issues.append(finding("SOURCE_PAGE_INVALID", "A source unit is outside its reconstruction batch.", page=unit.page))
                    continue
                unit.id = f"source-{unit.page}-{len(units) + len(excluded) + 1}"
                page = known[unit.page]
                if page.text_status == "text" and normalized_quote(unit.text) not in normalized_quote(page.text):
                    issues.append(finding("SOURCE_QUOTE_UNVERIFIED", "A reconstructed passage does not match its page text.", source_id=unit.id, page=unit.page))
                count_as_visit = collective_count_visit(unit.visit_label or (unit.text if unit.kind == "VISIT" else ""))
                if count_as_visit or unit.scope in {"TRIAL_MONITORING", "BACKGROUND"}:
                    excluded.append({**unit.model_dump(), "exclusion_reason": "Population count is not a patient visit" if count_as_visit else unit.scope})
                    if count_as_visit and unit.scope not in {"TRIAL_MONITORING", "BACKGROUND"}:
                        issues.append(finding("SOURCE_NON_PATIENT_CONTENT", "A population count was proposed as a patient visit and has been excluded. Check the source classification.", source_id=unit.id, page=unit.page))
                    continue
                if unit.scope == "UNRESOLVED":
                    issues.append(finding("SOURCE_SCOPE_UNRESOLVED", "A passage has not been established as a patient schedule requirement.", source_id=unit.id, page=unit.page))
                units.append(unit)
            issues.extend(finding("SOURCE_LAYOUT_UNCERTAIN", message, pages=numbers) for message in batch.uncertainties)
            trace.append({"node": "SOURCE_RECONSTRUCTION", "pages": numbers, "units": len(batch.units)})
        except Exception as error:
            if isinstance(error, SourceContractError) and len(numbers) > 1:
                # At most 2*n-1 attempts for an n-page packet; successful
                # neighboring packets stay intact and are not read again.
                midpoint = len(numbers) // 2
                packets[0:0] = [numbers[:midpoint], numbers[midpoint:]]
                trace.append({"node": "SOURCE_BATCH_SPLIT", "pages": numbers,
                    "reason": str(error)[:1000]})
                continue
            issues.append(finding("SOURCE_BATCH_FAILED", "A source reconstruction batch failed; retry extraction.", pages=numbers, error=str(error)))
    trace.append({"node": "SOURCE_EXCLUSIONS", "items": excluded})
    return units, issues, trace


def reconcile_sources(client: LLMClient, units: list[SourceUnit]) -> tuple[Reconciliation, list[ValidationIssue]]:
    result = ask(client, Reconciliation,
        "SOURCE_RECONCILIATION: Compare the reconstructed source tables and narrative below. "
        "Every visit must be an encounter, sampling occasion or recurring obligation in the CURRENT "
        "protocol. Historical study results and patient counts are not visits. If inventory content "
        "describes trial-level statistical stopping boundaries, it must not become a participant visit. "
        "belongs to background literature, report that scope error in uncertainties rather than "
        "converting it into a visit. "
        "Build visit identities from ASSESSMENT and TIMING passages as well as VISIT headers. Source "
        "reconstruction may have called a broad section a visit; that does not mean its distinct timepoints "
        "can be collapsed. A phase such as 'During study' is not an executable visit. Enumerated fixed "
        "timepoints must become distinct occasions; unrelated repeating assessment cadences must stay "
        "distinct repeating rules. Use a verbatim timepoint/cadence as the label when there is no named visit. "
        "Populate timing_source_ids with the source IDs defining EACH visit's own timing, not a whole "
        "phase's mixed timing instructions. They must also be in that visit's source_ids. Empty means "
        "the source states no timing for that visit; never leave it empty when timing is stated. "
        "Continuation, screening, treatment and follow-up tables for one participant timeline form ONE schedule. "
        "Arms and cohorts are usually applicability within that schedule. Create separate schedules ONLY when "
        "independent participant assignment is explicitly established; cite those CONTEXT source IDs in "
        "assignment_evidence_ids. One schedule may leave that list empty. "
        "Every source unit must belong to at least one schedule; shared context may belong to several. "
        "Reconcile duplicate appearances of the SAME visit into one stable visit code with all its source IDs. "
        "Keep distinct cycle days and sampling time points distinct. Each VISIT source must be linked to a visit. "
        "visits.source_ids must include the VISIT headers AND each ASSESSMENT belonging to that visit. "
        "Every ASSESSMENT must attach to at least one occasion, including recurring obligations without "
        "named visit headers. An overview table may summarize a detailed sampling table: link each "
        "overview assessment source ID to the corresponding detailed occasions as well as their own "
        "cell sources, not to a redundant phase visit. Retain truly continuous monitoring separately "
        "when the source requires it. Explicit source links take precedence over overview header labels. "
        "Report conflicts, do not silently choose.\n"
        + json.dumps([unit.model_dump() for unit in units]))
    issues = reconciliation_issues(result, units)
    missing = [issue for issue in issues if issue.issue_code in {"SOURCE_VISIT_UNMAPPED", "SOURCE_ASSESSMENT_UNMAPPED"}]
    if missing:
        try:
            repaired = ask(client, Reconciliation,
                "SOURCE_RECONCILIATION_REPAIR: The proposed schedule inventory omitted source occasions "
                "or assessments. Re-read these units and return the complete reconciliation. Preserve "
                "existing schedule IDs, visit codes, source links and assignment evidence. Add the missing "
                "assessment links and occasions supported by source. Distinct recurring assessment "
                "cadences require their own obligations, even when timing cannot yet be compiled. "
                "Do not invent timing, split schedules or drop existing visits to pass a check.\n"
                + "Omissions:\n" + json.dumps([issue.model_dump(mode="json") for issue in missing])
                + "\nCurrent reconciliation:\n" + result.model_dump_json()
                + "\nSource units:\n" + json.dumps([unit.model_dump() for unit in units]))
            repaired_issues = reconciliation_issues(repaired, units)
            previous = {group.id: group for group in result.schedules}
            updated = {group.id: group for group in repaired.schedules}
            preserved = previous.keys() == updated.keys() and all(
                set(group.source_ids) <= set(updated[key].source_ids)
                and group.assignment_evidence_ids == updated[key].assignment_evidence_ids
                and all(any(new.code == visit.code and set(visit.source_ids) <= set(new.source_ids)
                    and set(visit.timing_source_ids) <= set(new.timing_source_ids)
                    for new in updated[key].visits) for visit in group.visits)
                for key, group in previous.items())
            if preserved and not repaired_issues:
                return repaired, [issue for issue in issues if issue.issue_code == "SOURCE_GROUPING_UNCERTAIN"]
            issues.append(finding("SOURCE_RECONCILIATION_REPAIR_REJECTED",
                "The reconciliation repair did not preserve a complete, consistent source inventory."))
        except Exception as error:
            issues.append(finding("SOURCE_RECONCILIATION_REPAIR_FAILED",
                "Missing source occasions could not be recovered; review the source.", error=str(error)))
    return result, issues


def reconciliation_issues(result: Reconciliation, units: list[SourceUnit]) -> list[ValidationIssue]:
    issues = [finding("SOURCE_GROUPING_UNCERTAIN", message) for message in result.uncertainties]
    by_id = {unit.id: unit for unit in units}
    assigned: set[str] = set()
    if len({group.id for group in result.schedules}) != len(result.schedules) or len({group.label for group in result.schedules}) != len(result.schedules):
        raise ValueError("Reconciled schedules must have distinct identifiers and labels.")
    for group in result.schedules:
        ids = set(group.source_ids)
        assigned |= ids
        if ids - by_id.keys():
            issues.append(finding("SOURCE_REFERENCE_INVALID", "Schedule grouping references unknown sources.", schedule=group.id))
        if len(result.schedules) > 1 and (not group.assignment_evidence_ids or any(
            key not in ids or key not in by_id or by_id[key].kind != "CONTEXT" for key in group.assignment_evidence_ids)):
            issues.append(finding("SOURCE_SPLIT_UNSUPPORTED", "Separate schedules need source evidence of independent participant assignment.", schedule=group.id))
        visit_ids = {key for visit in group.visits for key in visit.source_ids}
        expected = {key for key in ids if key in by_id and by_id[key].kind == "VISIT"}
        if expected - visit_ids:
            issues.append(finding("SOURCE_VISIT_UNMAPPED", "Some source visits were lost during table reconciliation.", source_ids=sorted(expected - visit_ids)))
        normalize = lambda value: re.sub(r"\W+", "", value or "").casefold()
        missing_assessments = [key for key in ids if key in by_id and by_id[key].kind == "ASSESSMENT"
            and key not in visit_ids and not any(by_id[key].visit_label and
                normalize(by_id[key].visit_label) in {normalize(visit.label), normalize(visit.code)}
                for visit in group.visits)]
        if missing_assessments:
            issues.append(finding("SOURCE_ASSESSMENT_UNMAPPED",
                "Some source assessments have no visit or recurring occasion in the reconciled inventory.",
                source_ids=sorted(missing_assessments)))
        if visit_ids - ids or len({visit.code for visit in group.visits}) != len(group.visits):
            issues.append(finding("SOURCE_VISIT_IDENTITY_INVALID", "Visit identities have conflicting codes or sources outside their schedule.", schedule=group.id))
        for visit in group.visits:
            if set(visit.timing_source_ids) - set(visit.source_ids):
                issues.append(finding("SOURCE_TIMING_REFERENCE_INVALID", "A visit's timing points outside its linked source passages.", visit=visit.code))
    if set(by_id) - assigned:
        issues.append(finding("SOURCE_UNASSIGNED", "Some table cells or passages were not assigned to a schedule.", source_ids=sorted(set(by_id) - assigned)))
    return issues
