import assert from "node:assert/strict";
import test from "node:test";

import {
  cycleDayLabel, cycleDependencyModeText, cycleNumberFromAnchor, cycleStartBasisText,
  cycleWaitingReason, displayIssueCode, displayWaitingReason, issueHeading, phaseLabel,
  planSegmentTexts, toCyclePlanRows, toPatientScheduleRows, toProtocolScheduleRows,
  toTimelineEntries,
} from "../src/features/uctsm/presentation.ts";
import type {
  CycleExplanation, PatientScheduleEvent, ScheduleEvent, UniversalSchedule,
} from "../src/features/uctsm/types.ts";

const cycleEvent: ScheduleEvent = {
  id: "event-c", code: "CXD8", protocol_label: "Day 8", display_name: "Day 8 visit",
  event_type: "SITE_VISIT",
  timing: { type: "CYCLE_DAY", cycle_reference: "CYCLE", cycle: { type: "CURRENT" }, day: 8 },
  applicability: [], conditions: [], dependencies: [], activities: [], evidence_refs: [],
  interpretation_status: "CONFIRMED", requires_review: false,
};

function schedule(extra: Partial<UniversalSchedule> = {}): UniversalSchedule {
  return {
    schema_version: "uctsm.v1", schedule_version_id: "v1",
    schedule_metadata: { name: "S", schedule_type: "PRIMARY", version_number: 1, status: "APPROVED" },
    anchors: [], events: [cycleEvent], evidence: [], validation_issues: [], ...extra,
  };
}

const cycle = (overrides: Partial<CycleExplanation>): CycleExplanation => ({
  plan: "CYCLE", number: 3, day: 8, anchor: "CYCLE_3_START", start: "2026-11-16",
  start_basis: "NOMINAL", dependency_mode: "NOMINAL", ...overrides,
});

function row(patientEvent: Partial<PatientScheduleEvent>) {
  const [result] = toPatientScheduleRows({
    patient_id: "p", status: "ACTIVE", schedule_version_id: "v1",
    events: [{
      id: "pe", event_definition_id: "event-c", occurrence_index: 2, status: "RESOLVED",
      ...patientEvent,
    }],
  }, toProtocolScheduleRows(schedule(), []));
  return result;
}

// --- explanation.cycle and dependency_result -------------------------------------

test("a dated cycle visit shows Cycle N Day D and how its start was dated", () => {
  const nominal = row({
    nominal_start_date: "2026-11-23",
    explanation: { cycle: cycle({ start_basis: "NOMINAL" }) },
  });
  assert.equal(nominal.cycleLabel, "Cycle 3 Day 8");
  assert.equal(nominal.cycleBasis, "Nominal grid — not moved by an earlier late cycle");
  assert.deepEqual(nominal.contextLines, ["Cycle 3 Day 8", "Nominal grid — not moved by an earlier late cycle"]);
  assert.equal(nominal.expectedDate, "23 Nov 2026");

  const fromActual = row({
    nominal_start_date: "2026-11-30",
    explanation: { cycle: cycle({ start_basis: "FROM_ACTUAL_PREVIOUS_CYCLE", dependency_mode: "ACTUAL_PREVIOUS_CYCLE_START" }) },
  });
  assert.equal(fromActual.cycleBasis, "Counted from the previous cycle's actual start");
  assert.equal(cycleStartBasisText(cycle({ start_basis: "ACTUAL" })), "Actual start recorded");
  assert.equal(cycleStartBasisText(cycle({ start_basis: "PLANNED" })), "Planned start — cycle not yet recorded as given");
  assert.equal(cycleStartBasisText(cycle({ start_basis: "MANUAL" })), "Start date entered by the site");
  assert.equal(cycleStartBasisText(cycle({ start: null, start_basis: "WAITING_FOR_MANUAL_DATE" })), null);
  assert.equal(cycleDayLabel({ number: 1, day: 1 }), "Cycle 1 Day 1");
});

test("a cycle blocked on the previous cycle's actual start says which cycle must be given", () => {
  const blocked = row({
    status: "BLOCKED", occurrence_index: 3,
    explanation: {
      cycle: cycle({ number: 4, anchor: "CYCLE_4_START", start: null,
        start_basis: "WAITING_FOR_ACTUAL_PREVIOUS_CYCLE", dependency_mode: "ACTUAL_PREVIOUS_CYCLE_START" }),
      reason: "cycle 4 starts 21 days after cycle 3 actually starts; waiting for the actual CYCLE_3_START date",
    },
  });
  assert.equal(blocked.expectedDate, "Waiting for Cycle 3 to be given");
  assert.equal(blocked.status, "Waiting for previous cycle");
  assert.equal(blocked.waitingKind, "PREVIOUS_CYCLE");
  assert.equal(blocked.cycleLabel, "Cycle 4 Day 8");
  assert.equal(blocked.cycleBasis, null);
  assert.equal(blocked.allowedWindow, "—");
  assert.equal(toTimelineEntries([blocked], "2026-09-29")[0].undated, true);
});

test("dependency_result keys name the cycle from its <plan>_<n>_START anchor", () => {
  const event: PatientScheduleEvent = {
    id: "pe", event_definition_id: "event-c", occurrence_index: 0, status: "BLOCKED",
    dependency_result: { mode: "ACTUAL_PREVIOUS_CYCLE_START", cycle_plan: "TX", waiting_for_actual: ["TX_2_START"] },
  };
  assert.equal(cycleWaitingReason(event), "Waiting for Cycle 2 to be given");
  assert.equal(displayWaitingReason(event), "Waiting for Cycle 2 to be given");
  // Nested in the explanation (older payloads) reads the same.
  assert.equal(cycleWaitingReason({
    ...event, dependency_result: undefined,
    explanation: { dependency_result: { cycle_plan: "TX", waiting_for_actual: ["TX_2_START"] } },
  }), "Waiting for Cycle 2 to be given");
  assert.equal(cycleNumberFromAnchor("TX_12_START", "TX"), 12);
  assert.equal(cycleNumberFromAnchor("OTHER_2_START", "TX"), null);
  assert.equal(cycleNumberFromAnchor("SURGERY", null), null);
});

test("a manual cycle start waits for the site to enter it", () => {
  const manual = row({
    status: "WAITING_FOR_ANCHOR",
    explanation: { cycle: cycle({ number: 2, start: null, start_basis: "WAITING_FOR_MANUAL_DATE", dependency_mode: "MANUAL" }) },
    dependency_result: { mode: "MANUAL", cycle_plan: "CYCLE", waiting_for_manual_date: ["CYCLE_2_START"] },
  });
  assert.equal(manual.expectedDate, "Waiting for the Cycle 2 start date to be entered");
  assert.equal(manual.status, "Waiting for cycle start date");
  assert.equal(manual.waitingKind, "MANUAL_DATE");
});

test("a non-cycle actual-previous-event block keeps its existing wording", () => {
  const event: PatientScheduleEvent = {
    id: "pe", event_definition_id: "e", occurrence_index: 0, status: "BLOCKED",
    dependency_result: { mode: "ACTUAL_PREVIOUS_EVENT", waiting_for_actual: ["C2D1"] },
  };
  assert.equal(cycleWaitingReason(event), null);
  assert.equal(displayWaitingReason(event), "Awaiting C2d1 Date");
});

test("an unstated cycle rule is shown as needing review, not dated", () => {
  const unclear = row({
    status: "UNRESOLVED",
    explanation: { cycle: cycle({ number: 2, start: null, start_basis: "UNCLEAR", dependency_mode: "UNCLEAR" }) },
  });
  assert.equal(unclear.expectedDate, "Cycle start rule not stated in the protocol — needs review");
  assert.equal(unclear.status, "Unresolved");
});

// --- RepeatBlock.cycle_plan in protocol review -----------------------------------

test("cycle plans read as length, mode in plain words, and how they end", () => {
  const rows = toCyclePlanRows(schedule({
    repeat_blocks: [
      {
        id: "b1", code: "TX", protocol_label: "Treatment", display_name: "Treatment cycles",
        event_codes: ["CXD8"], dependency_mode: "ACTUAL_PREVIOUS_EVENT",
        cycle_plan: {
          code: "CYCLE", start_reference: { kind: "ANCHOR", code: "C1D1", accepts: "ACTUAL" },
          length: { value: 21, unit: "DAY" }, dependency_mode: "ACTUAL_PREVIOUS_CYCLE_START",
          termination: { type: "COUNT", count: 6 },
        },
      },
      {
        id: "b2", code: "MAINT", protocol_label: "Maintenance", display_name: "Maintenance",
        event_codes: ["CXD8"], dependency_mode: "UNCLEAR",
        cycle_plan: {
          code: "MAINT", start_reference: { code: "MAINT_START" },
          length: { value: 4, unit: "WEEK" }, dependency_mode: "UNCLEAR",
          start_event_code: "MAINT_D1", termination: { type: "HORIZON" },
        },
      },
      { id: "b3", code: "PLAIN", protocol_label: "Plain", display_name: "Plain", event_codes: [], dependency_mode: "NOMINAL" },
    ],
  }));
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    id: "b1", block: "Treatment cycles", length: "21 days",
    startsFrom: "Cycle 1 starts at C1d1",
    mode: "Each cycle starts 21 days after the previous cycle actually started",
    modeUnstated: false,
    ends: "A protocol maximum of 6 cycles",
    dayOneVisit: "No Day-1 visit named — each actual start must be recorded",
  });
  assert.equal(rows[1].mode, "Not stated in the protocol — blocks approval");
  assert.equal(rows[1].modeUnstated, true);
  assert.equal(rows[1].ends, "No stated maximum; the protocol continues while the patient remains on study");
  assert.equal(rows[1].dayOneVisit, "Actual starts read from Maint D1");
  assert.equal(cycleDependencyModeText("NOMINAL", { value: 21, unit: "DAY" }),
    "Fixed grid — every 21 days from Cycle 1 Day 1; a late cycle does not move later cycles");
  assert.equal(cycleDependencyModeText("MANUAL", { value: 21, unit: "DAY" }), "The site enters each cycle's start date");
});

// --- phased follow-up segments ---------------------------------------------------

test("a phased occurrence names its phase, cadence and position", () => {
  const event: PatientScheduleEvent = {
    id: "pe", event_definition_id: "e", occurrence_index: 9, status: "RESOLVED",
    explanation: { recurrence: { index: 9, segment: 2, segment_occurrence: 1, cadence: "every 6 months" } },
  };
  assert.equal(phaseLabel(event), "Phase 2 (every 6 months), visit 2");
  assert.equal(phaseLabel({ ...event, explanation: { recurrence: { index: 3 } } }), null);
  const withPhase = row({
    nominal_start_date: "2029-06-07",
    explanation: { recurrence: { index: 9, segment: 2, segment_occurrence: 1, cadence: "every 6 months" } },
  });
  assert.equal(withPhase.phaseLabel, "Phase 2 (every 6 months), visit 2");
  assert.deepEqual(withPhase.contextLines, ["Phase 2 (every 6 months), visit 2"]);
  assert.equal(toTimelineEntries([withPhase], "2026-09-29")[0].detail, "Phase 2 (every 6 months), visit 2");
});

test("segment phrases match evaluator.py plan_segment_texts", () => {
  assert.deepEqual(planSegmentTexts({
    segments: [
      { interval: { value: 3, unit: "MONTH" }, end_offset: { value: 24, unit: "MONTH" } },
      { interval: { value: 6, unit: "MONTH" }, end_offset: { value: 60, unit: "MONTH" } },
      { interval: { value: 12, unit: "MONTH" } },
      { interval: { value: 1, unit: "MONTH" }, count: 3 },
    ],
  }), ["every 3 months to year 2", "every 6 months to year 5", "yearly", "every month for 3 visits"]);
});

// --- batch-2 validator issue codes -------------------------------------------------

test("batch-2 validator codes read as human headings", () => {
  assert.equal(displayIssueCode("UNRESOLVED_RECURRENCE_BOUNDARY"),
    "Follow-up boundary not stated (does the boundary visit belong here?)");
  assert.equal(displayIssueCode("PHASED_SEGMENT_UNBOUNDED"), "Follow-up phase has no stated end");
  assert.equal(displayIssueCode("DUPLICATE_CYCLE_PLAN"), "Two cycle plans share one code");
  assert.equal(displayIssueCode("CYCLE_START_SOURCE_UNSTATED"), "No Day-1 visit named for actual cycle starts");
  assert.equal(displayIssueCode("DUPLICATE_REPEAT_BLOCK"), "Two repeat block entries share one code");
  assert.equal(issueHeading({ issue_code: "CYCLE_START_SOURCE_UNSTATED", blocking: false }),
    "Review · No Day-1 visit named for actual cycle starts");
});
