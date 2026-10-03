import assert from "node:assert/strict";
import test from "node:test";

import {
  ANCHOR_DATE_KINDS, NO_WINDOW_STATED, anchorSourceType, anchorStatusText,
  conditionScopes, displayIssueCode, displayPatientState, eventWindowText, issueHeading,
  patientWindowText, plannedAnchorNote, recurrencePlanText, ruleScopeBadge,
  toConditionalRequirementRows, toPatientScheduleRows, toProtocolConditionalRows,
  toProtocolScheduleRows, toRecordableAnchors, toRepeatRuleRows, toTimelineEntries,
  validateAnchorEntry,
} from "../src/features/uctsm/presentation.ts";
import type {
  PatientScheduleEvent, ScheduleEvent, UniversalSchedule,
} from "../src/features/uctsm/types.ts";

function event(overrides: Partial<ScheduleEvent>): ScheduleEvent {
  return {
    id: "event-1", code: "POST_OP", protocol_label: "Post-op", display_name: "Post-op visit",
    event_type: "SITE_VISIT", timing: { type: "OFFSET" }, applicability: [], conditions: [],
    dependencies: [], activities: [], evidence_refs: [], interpretation_status: "CONFIRMED",
    requires_review: false, ...overrides,
  };
}

function schedule(events: ScheduleEvent[], extra: Partial<UniversalSchedule> = {}): UniversalSchedule {
  return {
    schema_version: "uctsm.v1", schedule_version_id: "v1",
    schedule_metadata: { name: "S", schedule_type: "PRIMARY", version_number: 1, status: "APPROVED" },
    anchors: [{ id: "anchor-surgery", code: "SURGERY", display_name: "Surgery Date", status: "RESOLVED" }],
    events, evidence: [], validation_issues: [], ...extra,
  };
}

function patientRow(patientEvent: Partial<PatientScheduleEvent>, events = [event({})]) {
  const definition = schedule(events);
  const protocolRows = toProtocolScheduleRows(definition, []);
  const [row] = toPatientScheduleRows({
    patient_id: "p", status: "ACTIVE", schedule_version_id: "v1",
    events: [{
      id: "pe-1", event_definition_id: "event-1", occurrence_index: 0, status: "RESOLVED",
      ...patientEvent,
    }],
  }, protocolRows, { anchorNames: { SURGERY: "Surgery Date" } });
  return row;
}

// --- Doc H2: WAITING_FOR_ASSIGNMENT ------------------------------------------

test("a visit waiting for randomisation names the dimension and never shows a date", () => {
  const row = patientRow({
    status: "WAITING_FOR_ASSIGNMENT",
    // Even a stray date on the payload is not shown: this visit cannot be due.
    nominal_start_date: "2026-10-29", earliest_date: "2026-10-28", latest_date: "2026-10-30",
    explanation: { waiting_for_assignment: ["ARM"], reason: "waiting for the patient's ARM assignment" },
  });
  assert.equal(row.status, "Waiting for assignment");
  assert.equal(row.expectedDate, "Waiting for randomisation/assignment: Arm");
  assert.equal(row.allowedWindow, "—");
  assert.equal(row.expectedIso, null);
  assert.equal(row.waitingKind, "ASSIGNMENT");
  assert.equal(row.provisional, false);
  assert.notEqual(row.status, displayPatientState("WAITING_FOR_CONDITION"));
});

test("an assignment wait with several dimensions lists each, undated in the timeline", () => {
  const row = patientRow({
    status: "WAITING_FOR_ASSIGNMENT",
    explanation: { waiting_for_assignment: ["ARM", "COHORT"] },
  });
  assert.equal(row.expectedDate, "Waiting for randomisation/assignment: Arm, Cohort");
  const [entry] = toTimelineEntries([row], "2026-09-29");
  assert.equal(entry.undated, true);
  assert.equal(entry.past, false);
});

// --- Doc C6 / D2: planned vs actual anchors -----------------------------------

test("a visit dated from a planned anchor is labelled provisional", () => {
  const row = patientRow({
    status: "RESOLVED", nominal_start_date: "2026-12-07",
    earliest_date: "2026-11-30", latest_date: "2026-12-14",
    explanation: { anchor_basis: { SURGERY: "PLANNED", BASELINE: "ACTUAL" } },
  });
  assert.equal(row.expectedDate, "7 Dec 2026");
  assert.equal(row.dateNote, "Provisional — based on planned Surgery date");
  assert.equal(row.provisional, true);
  assert.equal(row.status, "Scheduled (provisional)");
  const [entry] = toTimelineEntries([row], "2026-09-29");
  assert.equal(entry.detail, "Provisional — based on planned Surgery date");
});

test("a visit dated only from actual anchors is not provisional", () => {
  const row = patientRow({
    status: "RESOLVED", nominal_start_date: "2027-01-16",
    explanation: { anchor_basis: { SURGERY: "ACTUAL" } },
  });
  assert.equal(row.dateNote, null);
  assert.equal(row.provisional, false);
  assert.equal(row.status, "Scheduled");
});

test("a post-op visit with only a planned surgery date waits for surgery to be recorded", () => {
  const row = patientRow({
    status: "WAITING_FOR_ANCHOR",
    explanation: { waiting_for_actual_anchor: ["SURGERY"] },
  });
  assert.equal(row.expectedDate, "Waiting for Surgery to be recorded (planned date not used)");
  assert.equal(row.status, "Waiting for actual date");
  assert.equal(row.waitingKind, "ACTUAL_ANCHOR");
  assert.equal(row.allowedWindow, "—");
  assert.equal(row.expectedIso, null);
});

test("an ordinary anchor wait keeps its existing wording", () => {
  const row = patientRow({
    status: "WAITING_FOR_ANCHOR",
    explanation: { timing: { reference: { kind: "ANCHOR", code: "SURGERY" } } },
  });
  assert.equal(row.expectedDate, "Awaiting Surgery Date");
  assert.equal(row.status, "Awaiting Anchor");
  assert.equal(row.waitingKind, "ANCHOR");
});

test("a protocol row flags timing that may count from a planned anchor", () => {
  const preOp = event({
    timing: {
      type: "WITHIN", direction: "BEFORE", duration: { value: 14, unit: "DAY" },
      reference: { kind: "ANCHOR", code: "SURGERY", accepts: "PLANNED_OR_ACTUAL" },
    },
  });
  assert.equal(plannedAnchorNote(preOp), "May be dated from a planned Surgery date");
  const postOp = event({
    timing: { type: "OFFSET", offset: { value: 30, unit: "DAY" }, reference: { kind: "ANCHOR", code: "SURGERY", accepts: "ACTUAL" } },
  });
  assert.equal(plannedAnchorNote(postOp), null);
  assert.equal(toProtocolScheduleRows(schedule([preOp]), [])[0].plannedAnchorNote,
    "May be dated from a planned Surgery date");
});

test("recording a reference date requires an explicit planned/actual choice", () => {
  assert.equal(validateAnchorEntry({ anchorId: "a", date: "2026-12-14", kind: null }),
    "Say whether this is the planned date or the actual date.");
  assert.equal(validateAnchorEntry({ anchorId: "a", date: "14/12/2026", kind: "ACTUAL" }),
    "Enter the date as YYYY-MM-DD.");
  assert.equal(validateAnchorEntry({ anchorId: "", date: "2026-12-14", kind: "ACTUAL" }),
    "Choose which reference date this is.");
  assert.equal(validateAnchorEntry({ anchorId: "a", date: "2026-12-14", kind: "PLANNED" }), null);
  // Source types the backend classifies (anchor_status.py PLANNED/ACTUAL_SOURCE_TYPES).
  assert.equal(anchorSourceType("PLANNED"), "PLANNED_PROCEDURE");
  assert.equal(anchorSourceType("ACTUAL"), "ACTUAL_EVENT");
  assert.deepEqual(ANCHOR_DATE_KINDS.map((item) => item.label), ["Actual date", "Planned date"]);
});

test("a confirmed planned anchor still reads as a plan, and is still recordable", () => {
  assert.equal(anchorStatusText({ status: "PLANNED", confirmed: true }), "Planned date (plan confirmed)");
  assert.equal(anchorStatusText({ status: "PLANNED", confirmed: false }), "Planned date");
  const recordable = toRecordableAnchors([
    { anchor_code: "SURGERY", display_name: "Surgery Date", status: "PLANNED", value: "2026-12-14", reason: "", confirmed: true },
    { anchor_code: "BASELINE", display_name: "Baseline", status: "CONFIRMED", value: "2026-10-01", reason: "", confirmed: true },
    { anchor_code: "UNKNOWN", display_name: "Unknown", status: "AWAITING_EVENT", reason: "", confirmed: false },
  ], [{ id: "anchor-surgery", code: "SURGERY", display_name: "Surgery Date" },
    { id: "anchor-baseline", code: "BASELINE", display_name: "Baseline" }]);
  assert.deepEqual(recordable, [{
    id: "anchor-surgery", code: "SURGERY", label: "Surgery Date", status: "Planned date (plan confirmed)",
  }]);
});

// --- Doc B2-B4: windows ------------------------------------------------------------

test("the event window is used first, symmetric or asymmetric", () => {
  const symmetric = event({ window: { before: { value: 14, unit: "DAY" }, after: { value: 14, unit: "DAY" } } });
  const lateOnly = event({ window: { before: { value: 0, unit: "DAY" }, after: { value: 3, unit: "DAY" } } });
  assert.equal(eventWindowText(symmetric.window), "±14 days");
  assert.equal(eventWindowText(lateOnly.window), "-0/+3 days");
  // The event window wins over a timing-derived projection display.
  const [row] = toProtocolScheduleRows(schedule([lateOnly]), [{
    event_id: "event-1", event_code: "POST_OP", title: "Post-op visit", timing_display: "Day 15",
    window_display: "-1 day/+1 day", event_type_display: "Site Visit", activities_display: [],
    status: "CONFIRMED", requires_review: false, evidence_refs: [],
  }]);
  assert.equal(row.window, "-0/+3 days");
});

test("the projection window is the fallback, and no window is never invented", () => {
  const [withProjection] = toProtocolScheduleRows(schedule([event({})]), [{
    event_id: "event-1", event_code: "POST_OP", title: "Post-op visit", timing_display: "Day 15",
    window_display: "-2 days/+2 days", event_type_display: "Site Visit", activities_display: [],
    status: "CONFIRMED", requires_review: false, evidence_refs: [],
  }]);
  assert.equal(withProjection.window, "-2 days/+2 days");
  const [none] = toProtocolScheduleRows(schedule([event({ window: null })]), []);
  assert.equal(none.window, NO_WINDOW_STATED);
  assert.equal(NO_WINDOW_STATED, "No window stated");
  assert.equal(eventWindowText(null), null);
  assert.equal(eventWindowText({ before: { value: 1, unit: "" }, after: { value: 1, unit: "DAY" } }), null);
});

test("a one-sided patient window reads as words, never an ellipsis or dangling dash", () => {
  assert.equal(patientWindowText("2026-10-19", "2026-10-22"), "19 Oct 2026 – 22 Oct 2026");
  assert.equal(patientWindowText("2026-10-19", null), "From 19 Oct 2026");
  assert.equal(patientWindowText(null, "2026-10-22"), "By 22 Oct 2026");
  assert.equal(patientWindowText(null, null), "—");
  assert.equal(patientWindowText(null, null, { dated: true, protocolStatesWindow: false }), NO_WINDOW_STATED);
  assert.equal(patientWindowText("2026-10-19", "2026-10-19", { protocolStatesWindow: false }), NO_WINDOW_STATED);
  assert.equal(patientWindowText("2026-10-19", "2026-10-19", { protocolStatesWindow: true }), "19 Oct 2026 only");
  for (const text of [patientWindowText("2026-10-19", null), patientWindowText(null, "2026-10-22")]) {
    assert.equal(text.includes("…"), false);
    assert.equal(text.includes("—"), false);
  }
});

test("a patient row applies the window: asymmetric bounds and none stated", () => {
  const lateOnly = [event({ window: { before: { value: 0, unit: "DAY" }, after: { value: 3, unit: "DAY" } } })];
  assert.equal(patientRow({
    nominal_start_date: "2026-10-19", earliest_date: "2026-10-19", latest_date: "2026-10-22",
  }, lateOnly).allowedWindow, "19 Oct 2026 – 22 Oct 2026");
  assert.equal(patientRow({ nominal_start_date: "2026-10-19", latest_date: "2026-10-22" }, lateOnly)
    .allowedWindow, "By 22 Oct 2026");
  assert.equal(patientRow({ nominal_start_date: "2026-10-19" }).allowedWindow, NO_WINDOW_STATED);
});

// --- Doc I8: trial-level rules -------------------------------------------------

test("trial, site and country rules are badged and cannot be confirmed for a patient", () => {
  assert.equal(ruleScopeBadge("TRIAL"), "Trial-level — not applied to patients");
  assert.equal(ruleScopeBadge("SITE"), "Site-level — not applied to patients");
  assert.equal(ruleScopeBadge("COUNTRY"), "Country-level — not applied to patients");
  for (const scope of ["PATIENT", "ARM", "COHORT", undefined, null]) {
    assert.equal(ruleScopeBadge(scope), null);
  }
  const definition = schedule([], {
    conditional_definitions: [
      {
        id: "c1", code: "FATAL_TOX", protocol_label: "Fatal toxicity", display_name: "Fatal toxicity",
        scope: "TRIAL", actions: [{ action_type: "STOP_BLOCK", target_code: "TREATMENT" }],
        requires_review: false, interpretation_status: "CONFIRMED", evidence_refs: [],
      },
      {
        id: "c2", code: "GRADE3", protocol_label: "Grade 3", display_name: "Grade 3 toxicity",
        actions: [{ action_type: "PAUSE_BLOCK", target_code: "TREATMENT" }],
        requires_review: false, interpretation_status: "CONFIRMED", evidence_refs: [],
      },
    ],
  });
  const [trial, patient] = toProtocolConditionalRows(definition);
  assert.equal(trial.scopeBadge, "Trial-level — not applied to patients");
  assert.equal(trial.trialLevel, true);
  assert.equal(trial.timing, "Never changes a patient's visits");
  assert.equal(patient.scopeBadge, null);
  assert.equal(patient.trialLevel, false);

  // The patient-conditions payload carries no scope; the schedule supplies it.
  const [row] = toConditionalRequirementRows([{
    condition_code: "FATAL_TOX", display_name: "Fatal toxicity", protocol_label: "Fatal toxicity",
    state: "NOT_OCCURRED", requires_review: false, interpretation_status: "CONFIRMED",
    actions: [{ action_type: "STOP_BLOCK", target_code: "TREATMENT" }], evidence_refs: [],
  }], conditionScopes(definition));
  assert.equal(row.scopeBadge, "Trial-level — not applied to patients");
  assert.equal(row.trialLevel, true);
});

// --- validator issue codes ----------------------------------------------------------

test("new validator issue codes read as human headings", () => {
  assert.equal(displayIssueCode("AMBIGUOUS_WINDOW"), "Window has no single target date");
  assert.equal(displayIssueCode("PLANNED_ANCHOR_AFTER_EVENT"), "Visit after the event may use a planned date");
  assert.equal(displayIssueCode("TRIAL_LEVEL_RULE_NOT_APPLIED_TO_PATIENTS"), "Trial-level rule — not applied to patients");
  assert.equal(displayIssueCode("UNCLEAR_DEPENDENCY_MODE"),
    "Cycle/visit dependency not stated (planned or actual previous date)");
  assert.equal(displayIssueCode("SOMETHING_NEW"), "Something New");
  assert.equal(issueHeading({ issue_code: "AMBIGUOUS_WINDOW", blocking: true }),
    "Blocks approval · Window has no single target date");
  assert.equal(issueHeading({ issue_code: "PLANNED_ANCHOR_AFTER_EVENT", blocking: false }),
    "Review · Visit after the event may use a planned date");
});

// --- Doc E9: phased follow-up -------------------------------------------------------

test("a phased follow-up reads as its cadences in order", () => {
  assert.equal(recurrencePlanText({
    type: "SEGMENTED",
    segments: [
      { interval: { value: 3, unit: "MONTH" }, end_offset: { value: 24, unit: "MONTH" }, end_inclusive: true },
      { interval: { value: 6, unit: "MONTH" }, end_offset: { value: 60, unit: "MONTH" }, end_inclusive: true },
      { interval: { value: 12, unit: "MONTH" } },
    ],
    termination: { type: "HORIZON" },
  }), "Every 3 months to year 2, then every 6 months to year 5, then yearly; continues while the patient remains on study");
  // No termination and an open final phase: said, never invented (PHASED_SEGMENT_UNBOUNDED).
  assert.equal(recurrencePlanText({ segments: [{ interval: { value: 1, unit: "YEAR" } }] }),
    "Yearly; end not stated");
  assert.equal(recurrencePlanText({
    segments: [
      { interval: { value: 2, unit: "WEEK" }, count: 4 },
      { interval: { value: 1, unit: "MONTH" }, end_offset: { value: 18, unit: "MONTH" }, end_inclusive: false },
    ],
  }), "Every 2 weeks for 4 visits, then every month to month 18");
  const [rule] = toRepeatRuleRows([{
    event_code: "FOLLOW_UP", cadence: "every 3 months", termination: "while on study",
    materialized_count: 4, continues: true,
    segments: ["every 3 months to year 2", "every 6 months to year 5", "yearly"],
  }]);
  // Same ", then " join as evaluator.py plan_cadence_text.
  assert.equal(rule.cadence, "every 3 months to year 2, then every 6 months to year 5, then yearly");
  assert.deepEqual(rule.segments, ["every 3 months to year 2", "every 6 months to year 5", "yearly"]);
});
