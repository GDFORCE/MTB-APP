import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalState, buildAddVisitRequest, buildEditRequest, buildQuestionRequest, buildWindow,
  extractionBadge, exportFileName, filterCounts, filterRows, groupRows, recoveryNotice,
  reviewErrorMessage, isStaleRevision, rowAccessibilityLabel, statusIndicator, timingFormFromRule,
} from "../src/features/uctsm/visitSchedule.ts";
import type { ReviewRow, ReviewView, SharedQuestion } from "../src/features/uctsm/visitSchedule.ts";
import { anchorDateSources } from "../src/features/uctsm/anchorEntry.ts";

/**
 * The Visit Schedule review screen's pure logic (UI-02..UI-12, PT-01). The
 * rows here are synthetic backend review-view rows, not protocol findings.
 */

function row(id: string, status: "PENDING" | "READY", extra: Partial<ReviewRow> = {}): ReviewRow {
  return {
    id, code: id.toUpperCase(), visit_name: `Visit ${id}`, protocol_label: id, event_type: "VISIT",
    timing_summary: "28 days after Randomisation", timing_supported: true,
    window_summary: "Not stated", window_status: "NOT_STATED", applicability_summary: "All participants",
    review_status: status, pending_reasons: status === "PENDING" ? ["Waiting on a shared question"] : [],
    issue_refs: [], question_refs: [], issues: [], group: "SCHEDULED", origin: "EXTRACTED",
    details: {
      activities: [], anchors: [], recurrence: null, constraints: [], evidence: [],
      visit_mode: null, allowed_visit_modes: [], activation: "SCHEDULED", manual: null,
    },
    ...extra,
  };
}

const ROWS = [row("a", "READY"), row("b", "PENDING"), row("c", "PENDING"), row("d", "READY", { group: "REPEATING" })];

function view(extra: Partial<ReviewView> = {}): ReviewView {
  return {
    view_version: "mtb.review-view.v1", draft_version_id: "v1", schedule_name: "Synthetic", version_number: 2,
    status: "VALIDATION_REQUIRED", revision: "rev-0001", semantic_fingerprint: "f",
    extraction_status: { status: "EXTRACTED", retry_available: false },
    source_coverage_status: { status: "NOT_RECORDED", blocking: false, message: "" },
    summary: { displayed_rows: 4, pending_rows: 2, ready_rows: 2, blocking_issues: 5, warnings: 0,
      shared_questions: 1, open_shared_questions: 1, document_level_issues: 1 },
    groups: [
      { id: "SCHEDULED", label: "Scheduled visits", row_ids: ["a", "b", "c"] },
      { id: "REPEATING", label: "Repeating visits", row_ids: ["d"] },
    ],
    rows: ROWS, shared_review_questions: [], document_level_issues: [],
    can_approve: false, approval_blockers: ["5 blocking finding(s) must be resolved."], anchors: [],
    ...extra,
  };
}

test("filter counts are unique rows, not issue counts", () => {
  const counts = filterCounts([...ROWS, ROWS[1]]); // a duplicate id is still one row
  assert.deepEqual(counts, { ALL: 4, PENDING: 2, READY: 2 });
  // Five blocking findings do not become five pending rows.
  assert.notEqual(counts.PENDING, view().summary.blocking_issues);
});

test("filters return only matching rows and keep group order", () => {
  assert.deepEqual(filterRows(ROWS, "PENDING").map((item) => item.id), ["b", "c"]);
  assert.deepEqual(filterRows(ROWS, "READY").map((item) => item.id), ["a", "d"]);
  assert.equal(filterRows(ROWS, "ALL").length, 4);
  const groups = groupRows(view(), filterRows(ROWS, "READY"));
  assert.deepEqual(groups.map((group) => [group.id, group.rows.map((item) => item.id)]),
    [["SCHEDULED", ["a"]], ["REPEATING", ["d"]]]);
});

test("status is carried by a word and an icon, not colour alone", () => {
  assert.equal(statusIndicator(ROWS[1]).label, "Pending");
  assert.equal(statusIndicator(ROWS[1]).icon, "alert");
  assert.equal(statusIndicator(ROWS[0]).label, "Ready");
  const label = rowAccessibilityLabel(ROWS[1], false);
  assert.match(label, /Pending/);
  assert.match(label, /Window: Not stated/);
  assert.match(label, /Expand details/);
  assert.match(rowAccessibilityLabel(ROWS[1], true), /Collapse details/);
});

test("approval eligibility comes only from the backend view", () => {
  assert.deepEqual(approvalState(view()), { enabled: false, reason: "5 blocking finding(s) must be resolved." });
  assert.deepEqual(approvalState(view({ can_approve: true, approval_blockers: [] })), { enabled: true, reason: "" });
  assert.equal(approvalState(view({ status: "APPROVED", can_approve: true })).enabled, false);
});

test("partial or failed extraction produces a recovery notice", () => {
  assert.equal(recoveryNotice(view()), null);
  const partial = view({ extraction_status: { status: "PARTIAL", reason: "Table 3 could not be read.", retry_available: true } });
  assert.match(recoveryNotice(partial)!, /Part of the protocol.*Table 3/);
  assert.equal(extractionBadge(partial).label, "Partial extraction");
  assert.equal(extractionBadge(view({ status: "APPROVED" })).label, "Approved");
});

test("a label edit sends only the label", () => {
  const built = buildEditRequest(ROWS[0], {
    visitName: "Visit a (clinic)", timing: null, window: null, confirmRow: false, rationale: "Typo in source",
  }, "rev-0001");
  assert.deepEqual(built.body, {
    expected_revision: "rev-0001", rationale: "Typo in source",
    changes: { display_name: "Visit a (clinic)" }, confirm_row: false,
  });
});

test("an edit needs a rationale and a real change", () => {
  assert.match(buildEditRequest(ROWS[0], { visitName: "Visit a", timing: null, window: null, confirmRow: false, rationale: "x" }, "r").error!, /Nothing/);
  assert.match(buildEditRequest(ROWS[0], { visitName: "Other", timing: null, window: null, confirmRow: false, rationale: " " }, "r").error!, /why/);
  assert.ok(buildEditRequest(ROWS[0], { visitName: "Visit a", timing: null, window: null, confirmRow: true, rationale: "checked" }, "r").body);
});

test("an empty window means Not stated and a half window is refused", () => {
  assert.deepEqual(buildWindow({ before: "", after: "", unit: "DAY" }), { window: null });
  assert.match(buildWindow({ before: "2", after: "", unit: "DAY" }).error!, /both sides/);
  assert.deepEqual(buildWindow({ before: "0", after: "3", unit: "DAY" }).window,
    { before: { value: 0, unit: "DAY" }, after: { value: 3, unit: "DAY" } });
});

test("Add Visit never defaults a window and keeps calendar units", () => {
  const built = buildAddVisitRequest({
    visitName: "Month 3 call", rationale: "Amendment 2",
    timing: { kind: "OFFSET", anchor: "BASELINE", value: "3", unit: "MONTH" },
    window: { before: "", after: "", unit: "DAY" }, activities: "AE review, ConMeds",
  }, "rev-9");
  assert.equal(built.error, undefined);
  assert.deepEqual(built.body!.timing, { type: "OFFSET", reference: { kind: "ANCHOR", code: "BASELINE" }, offset: { value: 3, unit: "MONTH" } });
  assert.equal("window" in built.body!, false);
  assert.deepEqual(built.body!.activities, ["AE review", "ConMeds"]);
  assert.match(buildAddVisitRequest({
    visitName: "Day 0", rationale: "x", timing: { kind: "PROTOCOL_DAY", anchor: "BASELINE", value: "0", unit: "DAY" },
    window: { before: "", after: "", unit: "DAY" }, activities: "",
  }, "r").error!, /no Day 0/);
});

test("a shared question answer names its action, anchor and reason", () => {
  const question: SharedQuestion = {
    id: "missing-anchor:SURGERY", kind: "MISSING_ANCHOR", title: "t", blocking: true, issues: [],
    issue_refs: [], affected_row_ids: ["b", "c"], affected_rows: [], actions: ["DEFINE_ANCHOR", "REMAP_ANCHOR"],
  };
  assert.match(buildQuestionRequest(question, { action: "REMAP_ANCHOR", rationale: "r" }, "rev").error!, /Choose an anchor/);
  assert.match(buildQuestionRequest(question, { action: "CHOOSE_ORIGIN", anchorCode: "X", rationale: "r" }, "rev").error!, /Choose how/);
  assert.deepEqual(buildQuestionRequest(question, { action: "REMAP_ANCHOR", anchorCode: "BASELINE", rationale: "sec 6" }, "rev").body,
    { expected_revision: "rev", rationale: "sec 6", action: "REMAP_ANCHOR", anchor_code: "BASELINE", display_name: undefined, anchor_type: undefined });
});

test("structured backend errors become readable text", () => {
  const stale = { response: { status: 409, data: { detail: { code: "STALE_REVISION", message: "changed" } } } };
  assert.equal(isStaleRevision(stale), true);
  assert.match(reviewErrorMessage(stale), /changed this draft/);
  assert.equal(reviewErrorMessage({ response: { data: { detail: { code: "APPROVAL_BLOCKED", reasons: ["A.", "B."] } } } }), "A. B.");
  assert.equal(reviewErrorMessage({ response: { data: { detail: "plain" } } }), "plain");
  assert.equal(reviewErrorMessage({ response: { data: { detail: [{ msg: "bad unit" }] } } }), "bad unit");
});

test("export file names carry the draft or approved label", () => {
  assert.equal(exportFileName(view(), "csv"), "visit-schedule-v2-draft.csv");
  assert.equal(exportFileName(view({ status: "APPROVED" }), "json"), "visit-schedule-v2-approved.json");
});

test("only simple timing rules prefill the edit form", () => {
  assert.deepEqual(timingFormFromRule({ type: "OFFSET", reference: { kind: "ANCHOR", code: "B" }, offset: { value: 3, unit: "MONTH" } }, "B"),
    { kind: "OFFSET", anchor: "B", value: "3", unit: "MONTH" });
  assert.equal(timingFormFromRule({ type: "RANGE" }, "B"), null);
});

test("PT-01: every entered anchor date needs an explicit planned or actual choice", () => {
  const sent = { SURGERY: "2026-11-02", RANDOMISATION: "2026-10-01" };
  assert.deepEqual(anchorDateSources(sent, { SURGERY: "PLANNED" }),
    { sources: { SURGERY: "PLANNED" }, missing: ["RANDOMISATION"] });
  assert.deepEqual(anchorDateSources(sent, { SURGERY: "PLANNED", RANDOMISATION: "ACTUAL", OTHER: "ACTUAL" }),
    { sources: { SURGERY: "PLANNED", RANDOMISATION: "ACTUAL" }, missing: [] });
});

// --- extraction ledger + WP2 constraints/feasibility ------------------------------------

import {
  citationLine, coverageCountsText, dispositionSummary, missingReferenceLine, questionResolution,
} from "../src/features/uctsm/visitSchedule.ts";
import { toFeasibilityView, toPatientScheduleRows } from "../src/features/uctsm/presentation.ts";

test("statement dispositions and coverage counts read as words, zeros omitted", () => {
  assert.equal(dispositionSummary({ COMPILED: 12, PENDING_INTERPRETATION: 2, OUT_OF_SCOPE_NON_SCHEDULE: 3, INVALID: 0 }),
    "12 compiled · 2 waiting for interpretation · 3 not schedule rules");
  assert.equal(coverageCountsText({ READ: 38, NOT_SELECTED: 2, UNREADABLE: 0 }), "38 read · 2 not selected");
});

test("citations show page, verification state and quote", () => {
  assert.equal(citationLine({ page: 7, quote: "Day 1 is first dose", verification: "TEXT_MATCHED" }),
    "Page 7 · quote found in the document — “Day 1 is first dose”");
  assert.match(citationLine({ page: null, verification: "REJECTED" }), /NOT found/);
  assert.match(missingReferenceLine({ kind: "APPENDIX", label: "B", state: "MISSING", required: true, from_pages: [12] }),
    /Appendix B not in the uploaded document \(referenced on page 12\) — required/);
});

test("an unsupported-operator question offers no acknowledge path", () => {
  const unsupported = questionResolution({ kind: "UNSUPPORTED_OPERATOR", actions: [], acknowledgeable: false });
  assert.deepEqual(unsupported.actions, []);
  assert.match(unsupported.note, /Cannot be acknowledged/);
  // Even if an action were sent, a non-acknowledgeable question shows none.
  assert.deepEqual(questionResolution({ kind: "UNSUPPORTED_OPERATOR", actions: ["CONFIRM_ANCHOR"], acknowledgeable: false }).actions, []);
  assert.deepEqual(questionResolution({ kind: "ANCHOR", actions: ["CONFIRM_ANCHOR"] }).actions, ["CONFIRM_ANCHOR"]);
});

const bound = (value: string | null, extra: Record<string, unknown> = {}) => ({ value, inclusive: true, sources: [], provisional: false, ...extra });

test("AT-18: disjoint permitted intervals are shown separately, never merged", () => {
  const view = toFeasibilityView({
    id: "e", event_definition_id: "d", occurrence_index: 0, status: "RESOLVED", feasibility: "FEASIBLE",
    feasible_intervals: [
      { lower: bound("2026-11-01"), upper: bound("2026-11-05") },
      { lower: bound("2026-11-20"), upper: bound(null) },
    ],
  } as any)!;
  assert.equal(view.intervals.length, 2);
  assert.match(view.intervals[0], /on or after .*, on or before /);
  assert.match(view.intervals[1], /no latest date/);
  assert.equal(view.headline, "2 separate permitted periods");
});

test("INCOMPLETE names each missing input and INFEASIBLE names the conflict", () => {
  const incomplete = toFeasibilityView({
    id: "e", event_definition_id: "d", occurrence_index: 0, status: "WAITING_FOR_ANCHOR",
    explanation: { feasibility: { status: "INCOMPLETE", intervals: [{ lower: bound("2026-11-01"), upper: bound(null, { provisional: true }) }],
      missing_inputs: [{ kind: "ANCHOR", code: "RT_END", accepts: "ACTUAL", reason: "NOT_RECORDED" }], conflicting_constraints: [] } },
  } as any, { RT_END: "RT end" })!;
  assert.deepEqual(incomplete.missing, ["Waiting for RT end (actual)"]);
  assert.equal(incomplete.headline, "Waiting for RT end (actual)");
  assert.match(incomplete.intervals[0], /may still change/);

  const infeasible = toFeasibilityView({
    id: "e", event_definition_id: "d", occurrence_index: 0, status: "BLOCKED", feasibility: "INFEASIBLE",
    conflicting_constraints: ["scan-late", "scan-early"],
  } as any)!;
  assert.equal(infeasible.headline, "No date satisfies all rules");
  assert.deepEqual(infeasible.conflicts, ["scan-late", "scan-early"]);
});

test("patient rows carry feasibility and never show a date when nothing is feasible", () => {
  const [row] = toPatientScheduleRows({
    patient_id: "p", status: "ACTIVE", events: [{
      id: "e1", event_definition_id: "d1", occurrence_index: 0, status: "BLOCKED",
      nominal_start_date: "2026-11-02", feasibility: "INFEASIBLE", conflicting_constraints: ["a", "b"],
    }],
  } as any, []);
  assert.equal(row.expectedDate, "No date satisfies all rules");
  assert.equal(row.status, "No feasible date");
  assert.equal(row.feasibility!.conflicts.length, 2);
  const [legacy] = toPatientScheduleRows({ patient_id: "p", status: "ACTIVE", events: [{
    id: "e2", event_definition_id: "d2", occurrence_index: 0, status: "RESOLVED", nominal_start_date: "2026-11-02" }] } as any, []);
  assert.equal(legacy.feasibility, null);
});

// --- WP3: process transitions, occurrence facts, assignment, occurrence lines ------------

import {
  buildAssignmentRequest, buildOccurrenceFactRequest, buildProcessTransitionRequest,
} from "../src/features/uctsm/patientActions.ts";
import { occurrenceFactLines } from "../src/features/uctsm/presentation.ts";

test("a process transition preview names one process and is pending, never applied", () => {
  const built = buildProcessTransitionRequest({
    target: "DRUG_A", toState: "HELD", reason: "DECISION", effectiveAt: "2026-11-02", authority: "PI", note: "Grade 3 ALT",
  }, "2027-11-02");
  assert.deepEqual(built.body, {
    horizon: "2027-11-02", reason: "Grade 3 ALT",
    process_transition: { target: { type: "PROCESS", process_code: "DRUG_A" }, to_state: "HELD",
      effective_at: "2026-11-02", reason: "DECISION", authority: "PI" },
  });
  assert.match(buildProcessTransitionRequest({ target: "PATIENT", toState: "HELD", reason: "DECISION", effectiveAt: "2026-11-02", authority: "", note: "x" }, "h").error!, /only stop/);
  assert.match(buildProcessTransitionRequest({ target: "DRUG_A", toState: "STOPPED", reason: "WITHDRAWAL_OF_CONSENT", effectiveAt: "2026-11-02", authority: "", note: "x" }, "h").error!, /whole participation/);
  assert.match(buildProcessTransitionRequest({ target: "DRUG_A", toState: "HELD", reason: "DECISION", effectiveAt: "02/11/2026", authority: "", note: "x" }, "h").error!, /YYYY-MM-DD/);
});

test("occurrence facts need a date when booked or performed and carry the superseded record", () => {
  assert.match(buildOccurrenceFactRequest({ logicalKey: "V1:0", kind: "BOOKED", value: "", reason: "r" }).error!, /YYYY-MM-DD/);
  assert.deepEqual(buildOccurrenceFactRequest({ logicalKey: "V1:0", kind: "MISSED", value: "", reason: "no show" }).body,
    { logical_key: "V1:0", kind: "MISSED", reason: "no show" });
  assert.deepEqual(buildOccurrenceFactRequest({ logicalKey: "V1:0", kind: "PERFORMED", value: "2027-01-15", reason: "typo",
    supersedesOccurrenceId: "occ-1" }).body,
    { logical_key: "V1:0", kind: "PERFORMED", reason: "typo", value: "2027-01-15", supersedes_occurrence_id: "occ-1" });
});

test("an assignment change can carry an effective date", () => {
  assert.deepEqual(buildAssignmentRequest({ dimension: "ARM", value: "B", effectiveFrom: "2026-12-01", reason: "crossover" }, "h").body,
    { assignment: { ARM: "B" }, horizon: "h", reason: "crossover", assignment_effective_from: "2026-12-01" });
  assert.equal("assignment_effective_from" in buildAssignmentRequest({ dimension: "ARM", value: "B", effectiveFrom: "", reason: "r" }, "h").body!, false);
});

test("occurrence lines show session identity, process state, booking flag and performed time", () => {
  const lines = occurrenceFactLines({
    status: "CANCELLED",
    calendar_session: { session: 31, kind: "REPLACEMENT", replaces_session: 7 },
    process_state: { process: "DRUG_A", state: "STOPPED", effective_at: "2026-12-20" },
    booked_time: "2027-06-01", booked_outside_permitted: true, actual_time: "2027-06-02",
    condition_episode: { condition: "NEUTROPENIA", episode: 1 },
  }, { DRUG_A: "Drug A" });
  assert.deepEqual(lines, [
    "Session 31 — replaces missed session 7",
    "Drug A: Stopped from 2026-12-20",
    "Episode 2 of NEUTROPENIA",
    "Booked 2027-06-01 — Booked outside the permitted window (the rule has not moved)",
    "Performed 2027-06-02",
  ]);
  assert.deepEqual(occurrenceFactLines({ status: "MISSED", calendar_session: { session: 7, kind: "SLOT" } }),
    ["Session 7 — missed (keeps its number)"]);
  assert.deepEqual(occurrenceFactLines({ held_after_resolution: "NEUTROPENIA" }),
    ["Still on hold: NEUTROPENIA resolved, but a resume must be recorded"]);
});
