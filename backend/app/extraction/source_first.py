"""Selectable source-first extraction; shares UCTSM assembly and date semantics.

Map -> bounded page reconstruction -> table reconciliation -> grouped semantic
claims -> deterministic source coverage -> independent source audit. Selection
is explicit until paired corpus evaluation supports a default change.
"""
from __future__ import annotations

import json
import re

from app.extraction.claude_provider import ClaudeExtractionProvider
from app.extraction.graph import DocumentPage, ExtractionResult, run_extraction
from app.domain.schedule.timing import UnresolvedTiming
from app.extraction.llm_client import LLMClient, ExtractionModelError, parse_json_object
from app.extraction.prompts import CATEGORY_PROMPTS, DOCUMENT_STRUCTURE, EVIDENCE
from app.extraction.source_grounding import page_packet
from app.extraction.source_inventory import (
    Contract, ScheduleGroup, SourceUnit, ask, finding, reconstruct_sources, reconcile_sources,
)


# Dependent groups remain ordered. Every existing domain category is retained.
CLAIM_GROUPS = (
    ("protocol_metadata", "epochs", "arms_cohorts_populations", "anchors", "events"),
    ("event_types", "timing", "conditions", "applicability", "dependencies", "dependency_modes", "recurrence", "repeat_blocks"),
    ("activities", "activity_timing", "activity_conditions", "qualifiers"),
    ("conditional_actions", "conditional_resolution", "confinement"),
)


class SourceAudit(Contract):
    pages_checked: list[int]
    findings: list[str]


class SourceFirstProvider(ClaudeExtractionProvider):
    def __init__(self, client: LLMClient, group: ScheduleGroup, units: list[SourceUnit]):
        super().__init__(client, schedule_name=group.label)
        self.group = group
        self.units = [unit for unit in units if unit.id in group.source_ids]
        self.answers: dict[str, list[dict]] = {}
        self.active_category: str | None = None
        self._system += (
            "\nExtract ONLY this reconciled participant schedule: " + group.label
            + ". Its stable visit inventory is: " + json.dumps([visit.model_dump() for visit in group.visits])
            + ". Reuse these visit codes exactly. Do not create a second copy for a continuation table."
        )

    def _ask(self, prompt: str, state: dict) -> str:
        if prompt == DOCUMENT_STRUCTURE:
            return json.dumps({"sections": [{"title": unit.table or "Schedule source", "page": unit.page}
                for unit in self.units], "tables": []})
        if prompt == EVIDENCE:
            return json.dumps({"evidence": [{
                "ref": unit.id, "page_number": unit.page,
                "evidence_type": "FOOTNOTE" if unit.kind == "FOOTNOTE" else "TABLE_CELL" if unit.table else "SECTION",
                "table_title": unit.table, "row_identifier": unit.row, "column_identifier": unit.column,
                "source_text": unit.text,
            } for unit in self.units]})
        if self.active_category:
            return json.dumps({"claims": self.answers[self.active_category]})
        return super()._ask(prompt, state)

    def extract_claims(self, category: str, state: dict) -> list[dict]:
        if category not in self.answers:
            group = next(group for group in CLAIM_GROUPS if category in group)
            prompt = (
                "SOURCE_SEMANTICS: Extract these related categories together: " + ", ".join(group)
                + ". Return ONE JSON object with exactly these category keys, each mapping to its claims ARRAY. "
                "Return an explicit empty array when a category is absent. Every claim needs evidence_ids. "
                "Each source ASSESSMENT cell must produce an activity on its particular visit. "
                "Retain every source VISIT, TIMING assertion, and FOOTNOTE; report ambiguity through unresolved "
                "domain values. Interpret nonempty assessment marks using the table legend. "
                "Each reconciled visit has timing_source_ids: use those passages to compile THAT visit's "
                "timing and cite them. Do not replace fixed timepoints with a generic phase. "
                "When an overview assessment cell is expanded by a detailed table, cite BOTH the "
                "overview source and the detailed cell on the corresponding activities; an overview "
                "is not an extra execution of those assessments. Read explicit time-axis origins such "
                "as Hour 0 as reference instants. If the source does not equate that origin to a named "
                "clinical event, retain its neutral source label instead of assuming First Dose or "
                "Randomization. Reference codes used by later timing claims must be defined in anchors. "
                "Restrictions on one activity (for example pregnancy testing in a stated population) "
                "must remain activity conditions, not restrict attendance at the entire visit. "
                "An origin anchor supplied at enrollment must not derive from the same event whose timing "
                "uses that anchor: this creates a circular dependency. Set source_event_code only for an "
                "explicitly derived date, not merely because an anchor and visit happen on the same day. "
                "Classify activity_type as ADMINISTRATIVE for consent, registration and administrative tasks; "
                "use clinical types for assessments and interventions. Never infer patient dates.\n"
                + "Reconstructed source inventory:\n" + json.dumps([unit.model_dump() for unit in self.units])
                + "\nCategory contracts (take the claims array from each contract):\n"
                + "\n".join(f"CATEGORY {name}\n{CATEGORY_PROMPTS[name]}" for name in group)
                + "\nFINAL RESPONSE FORMAT: The category contracts above show the shape of each claim. "
                "Do NOT use their outer claims wrapper. Your outer object must have precisely these keys: "
                + json.dumps({name: [] for name in group})
                + ". Replace each [] with that category's claim objects. Include empty arrays explicitly."
            )
            accepted: dict[str, list[dict]] = {}
            failure = ""
            try:
                for attempt in range(2):
                    # Transport failures do not trigger a format retry. A quota
                    # failure must not consume another attempted model call.
                    raw = super()._ask(self._with_evidence(prompt), state)
                    try:
                        answer = parse_json_object(raw)
                        for key in group:
                            value = answer.get(key)
                            if key not in accepted and isinstance(value, list) and all(
                                isinstance(item, dict) and isinstance(item.get("candidate"), dict) for item in value):
                                accepted[key] = value
                        failure = "Missing or invalid categories: " + ", ".join(key for key in group if key not in accepted)
                    except ExtractionModelError as error:
                        failure = str(error)
                    if len(accepted) == len(group):
                        break
                    pending = [key for key in group if key not in accepted]
                    prompt += ("\nFORMAT REPAIR: " + failure + ". The only categories still required are "
                        + json.dumps(pending) + ". Return a JSON object with those keys and their claims arrays. "
                        "Do not discard valid extracted information from other categories. Accepted categories "
                        "and their stable identifiers for context:\n" + json.dumps(accepted))
            except Exception as error:
                failure = str(error)
            pending = [key for key in group if key not in accepted]
            if pending:
                self._issues.append(finding("SOURCE_SEMANTIC_GROUP_FAILED",
                    "Some schedule details could not be extracted; valid categories were retained.", categories=pending, error=failure).model_dump(mode="json"))
            self.answers.update({key: accepted.get(key, []) for key in group})
        self.active_category = category
        try:
            claims = super().extract_claims(category, state)
        finally:
            self.active_category = None
        # All semantic groups have now run. Resolve missing origin definitions
        # once, with source evidence, before assembly; never synthesize an anchor
        # merely because a timing expression happens to reference its name.
        if category == CLAIM_GROUPS[-1][-1]:
            claims.extend(self._recover_missing_anchors({**state, "claims": [*state.get("claims", []), *claims]}))
        return claims

    def _recover_missing_anchors(self, state: dict) -> list[dict]:
        anchors = [claim["candidate"] for claim in state.get("claims", [])
            if claim["candidate"].get("_category") == "anchors"]
        known = {anchor.get("code") for anchor in anchors}
        references: set[str] = set()

        def collect(value):
            if isinstance(value, dict):
                if value.get("kind") == "ANCHOR" and isinstance(value.get("code"), str):
                    references.add(value["code"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        for claim in state.get("claims", []):
            collect(claim["candidate"])
        missing = references - known
        # The shared assembler already resolves a unique exact source label to
        # its established code. Do not create a duplicate anchor for that label.
        normalize = lambda value: re.sub(r"\s+", " ", value or "").strip().casefold()
        missing = {code for code in missing if sum(any(normalize(code) == normalize(anchor.get(key))
            for key in ("protocol_label", "display_name")) for anchor in anchors) != 1}
        if not missing:
            return []
        try:
            answer = parse_json_object(super()._ask(self._with_evidence(
                "SOURCE_ANCHOR_RECOVERY: Timing rules reference these undefined anchors: "
                + json.dumps(sorted(missing))
                + ". Re-read the source to determine whether it explicitly defines these reference dates. "
                "Return anchor claims ONLY for those supported by cited source passages, using the exact "
                "requested codes. A reference in generated JSON is not source evidence. If the protocol "
                "does not establish an anchor, return no claim for it. Do not choose a default baseline, "
                "invent a date, or change existing definitions. An enrollment-supplied origin must not "
                "derive from an event that itself depends on that origin.\n"
                + CATEGORY_PROMPTS["anchors"]), state))
            raw = answer.get("claims")
            if not isinstance(raw, list):
                raise ExtractionModelError("Anchor recovery did not return a claims array")
            candidates = [item for item in raw if isinstance(item, dict)
                and isinstance(item.get("candidate"), dict) and item["candidate"].get("code") in missing]
            # Duplicate definitions need review; never choose one by response order.
            counts = {code: sum(item["candidate"].get("code") == code for item in candidates) for code in missing}
            self.answers["anchor_recovery"] = [item for item in candidates if counts[item["candidate"]["code"]] == 1]
            self.active_category = "anchor_recovery"
            repaired = super().extract_claims("anchors", state)
            for index, claim in enumerate(repaired):
                claim["claim_id"] = f"source-anchor-recovery-{index}"
            return repaired
        except Exception as error:
            self._issues.append(finding("SOURCE_ANCHOR_RECOVERY_FAILED",
                "Missing reference dates could not be recovered from the source.",
                anchors=sorted(missing), error=str(error)).model_dump(mode="json"))
            return []
        finally:
            self.active_category = None


def source_coverage(provider: SourceFirstProvider, result: ExtractionResult) -> tuple[list[dict], list]:
    schedule = result.schedule
    if schedule is None:
        return [], []
    expected = {
        "VISIT": {"EVENT_NAME"}, "ASSESSMENT": {"ACTIVITY"},
        "TIMING": {"TIMING", "ACTIVITY_TIMING", "RECURRENCE", "ANCHOR", "EPOCH", "DEPENDENCY", "CONDITIONAL_ACTION"},
        "MODE": {"EVENT_TYPE"}, "APPLICABILITY": {"APPLICABILITY", "CONDITION", "ACTIVITY_CONDITION", "CONDITIONAL_ACTION"},
        "FOOTNOTE": {"QUALIFIER"},
    }
    coverage, issues = [], []
    event_by_entity = {event.id: event.code for event in schedule.events}
    for event in schedule.events:
        event_by_entity.update({activity.id: event.code for activity in event.activities})
    normalize = lambda value: re.sub(r"\W+", "", value or "").casefold()
    for unit in provider.units:
        evidence_id = provider._evidence_ids.get(unit.id)
        retained = [claim for claim in schedule.claim_evidence if claim.evidence_id == evidence_id
            and claim.claim_type in expected.get(unit.kind, set())]
        state = "CONTEXT" if unit.kind == "CONTEXT" else "RETAINED" if retained else "UNRESOLVED"
        if unit.kind in {"VISIT", "ASSESSMENT"} and retained:
            expected_visits = {visit.code for visit in provider.group.visits if unit.id in visit.source_ids}
            if not expected_visits:
                expected_visits = {visit.code for visit in provider.group.visits if unit.visit_label
                    and normalize(unit.visit_label) in {normalize(visit.label), normalize(visit.code)}}
            actual_visits = {event_by_entity.get(claim.claim_entity_id) for claim in retained}
            if not expected_visits or expected_visits != actual_visits:
                state = "UNRESOLVED"
                issues.append(finding("SOURCE_CELL_WRONG_VISIT", "A source visit or assessment could not be matched to the correct visit(s).",
                    source_id=unit.id, expected=sorted(expected_visits), actual=sorted(code for code in actual_visits if code)))
        coverage.append({"source_id": unit.id, "page": unit.page, "table": unit.table,
            "row": unit.row, "column": unit.column, "kind": unit.kind, "status": state,
            "entity_ids": sorted({str(claim.claim_entity_id) for claim in retained})})
        if state == "UNRESOLVED":
            issues.append(finding("SOURCE_CONTENT_UNMAPPED",
                f"Source {unit.kind.lower()} on page {unit.page} has no retained schedule rule.",
                source_id=unit.id, text=unit.text, row=unit.row, column=unit.column))
    actual_codes = {event.code for event in schedule.events}
    expected_codes = {visit.code for visit in provider.group.visits}
    if actual_codes != expected_codes:
        issues.append(finding("SOURCE_VISIT_SET_CHANGED", "Extracted visits differ from the reconciled source inventory.",
            missing=sorted(expected_codes - actual_codes), unexpected=sorted(actual_codes - expected_codes)))
    for visit in provider.group.visits:
        event = next((event for event in schedule.events if event.code == visit.code), None)
        if event is None:
            continue
        for source_id in visit.timing_source_ids:
            evidence_id = provider._evidence_ids.get(source_id)
            if not any(claim.evidence_id == evidence_id and claim.claim_entity_id == event.id
                and claim.claim_type in {"TIMING", "RECURRENCE"} for claim in schedule.claim_evidence):
                issues.append(finding("SOURCE_VISIT_TIMING_UNMAPPED", "A visit's stated timing was not retained on that visit.",
                    visit=visit.code, source_id=source_id))
    return coverage, issues


def retain_unresolved_source_wording(provider: SourceFirstProvider, result: ExtractionResult) -> None:
    """Keep source rules readable when the compiler cannot safely date them."""
    if result.schedule is None:
        return
    visits = {visit.code: visit for visit in provider.group.visits}
    for event in result.schedule.events:
        if not isinstance(event.timing, UnresolvedTiming) or event.code not in visits:
            continue
        identifiers = set(visits[event.code].source_ids)
        linked = [unit for unit in provider.units if unit.id in identifiers and unit.kind in {"TIMING", "VISIT"}]
        # Timing passages precede visit headers; preserve each distinct quote.
        linked.sort(key=lambda unit: unit.kind != "TIMING")
        statements, seen = [], set()
        for unit in linked:
            if unit.text in seen:
                continue
            seen.add(unit.text)
            statements.append({"text": unit.text, "page": unit.page, "source_id": unit.id})
        if statements:
            event.timing.source_reference["statements"] = statements


def run_source_first(client: LLMClient, pages: list[DocumentPage], *, document_hash: str,
                     schedule_name: str = "Schedule of Assessments") -> list[tuple[dict | None, ExtractionResult]]:
    try:
        units, source_issues, trace = reconstruct_sources(client, pages)
        if not units:
            return [(None, ExtractionResult(schedule=None, issues=source_issues or [finding(
                "SOURCE_SCHEDULE_NOT_FOUND", "No schedule could be reconstructed.")], extraction_trace=trace))]
        reconciliation, grouping_issues = reconcile_sources(client, units)
        source_issues.extend(grouping_issues)
        if not reconciliation.schedules:
            return [(None, ExtractionResult(schedule=None, issues=[*source_issues, finding(
                "SOURCE_GROUPING_FAILED", "Source tables could not be reconciled into a schedule.")], extraction_trace=trace))]
    except Exception as error:
        return [(None, ExtractionResult(schedule=None, issues=[finding(
            "SOURCE_RECONSTRUCTION_FAILED", "The source inventory could not be built; retry extraction.", error=str(error))], extraction_trace=[]))]
    results = []
    for group in reconciliation.schedules:
        option = {"id": group.id, "label": group.label, "description": "Reconciled source tables"}
        try:
            group_pages = {unit.page for unit in units if unit.id in group.source_ids}
            provider = SourceFirstProvider(page_packet(client, [page for page in pages if page.page_number in group_pages]), group, units)
            result = run_extraction(provider, document_hash=document_hash, pages=pages)
            retain_unresolved_source_wording(provider, result)
            coverage, coverage_issues = source_coverage(provider, result)
            result.issues.extend([*source_issues, *coverage_issues])
            if result.schedule is not None:
                result.schedule.schedule_metadata.name = group.label
                result.schedule.schedule_metadata.extensions.append({"type": "source_coverage.v1", "items": coverage})
            result.extraction_trace = [*trace,
                {"node": "SOURCE_RECONCILIATION", "schedule": group.model_dump()},
                *result.extraction_trace, {"node": "SOURCE_COVERAGE", "items": coverage}]
            results.append((option, result))
        except Exception as error:
            results.append((option, ExtractionResult(schedule=None, issues=[finding(
                "SCHEDULE_OPTION_FAILED", f"Extraction failed for {group.label}.", error=str(error))], extraction_trace=trace)))
    # Independent reading checks omission and placement, not just JSON validity.
    try:
        audit = ask(client, SourceAudit,
            "SOURCE_AUDIT: Independently re-read the ORIGINAL protocol, including schedule tables and "
            "referenced narrative/footnotes. Compare it to ALL of these extracted schedules. Find missing "
            "or extra visits, incorrect schedule splits/merges, assessment marks attached to the wrong visit, "
            "historical study descriptions or patient counts incorrectly converted into visits, "
            "trial-level statistical monitoring or stopping boundaries incorrectly treated as participant events, "
            "incorrect timing/windows, lost footnotes, unsupported administrative/clinical classification, "
            "and conflicts between tables and prose. Agreement between extracted objects is not evidence. "
            "Verify every visit against operative instructions for THIS protocol, not background "
            "literature. List each discrepancy in findings with physical source page and visit/assessment. "
            "Check the whole document for omitted schedules; report any unreadable page or limit. "
            "pages_checked must list every physical page inspected. Empty findings means you found no "
            "discrepancy, not certified accuracy. Do not make corrections or invent dates.\n"
            + json.dumps([result.schedule.model_dump(mode="json") if result.schedule else {"failed": option}
                          for option, result in results]))
        audit_issues = [finding("SOURCE_AUDIT_DISCREPANCY", message) for message in audit.findings]
        if set(audit.pages_checked) != {page.page_number for page in pages}:
            audit_issues.append(finding("SOURCE_AUDIT_INCOMPLETE", "The independent source audit did not inspect every document page.", checked=audit.pages_checked))
    except Exception as error:
        audit_issues = [finding("SOURCE_AUDIT_FAILED", "The independent source audit failed; retry extraction.", error=str(error))]
    failures = [issue for _, result in results if result.schedule is None for issue in result.issues]
    for _, result in results:
        result.issues.extend(audit_issues)
        if result.schedule is not None:
            result.issues.extend(failures)
        result.extraction_trace.append({"node": "SOURCE_AUDIT", "findings": len(audit_issues)})
    return results
