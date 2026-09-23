import assert from "node:assert/strict";
import test from "node:test";

import {
  compactScheduleWindow, displayPatientState, displayVisitType, toPatientScheduleRows, toProtocolScheduleRows,
} from "../src/features/uctsm/presentation.ts";
import type { ScheduleProjection, UniversalSchedule } from "../src/features/uctsm/types.ts";

test("compact schedule windows preserve zero, asymmetric limits and mixed units", () => {
  assert.equal(compactScheduleWindow("-0 days/+0 days"), "±0 days");
  assert.equal(compactScheduleWindow("-3 days/+3 days"), "±3 days");
  assert.equal(compactScheduleWindow("-1 day/+2 days"), "-1/+2 days");
  assert.equal(compactScheduleWindow("-1 week/+7 days"), "-1 week/+7 days");
  assert.equal(compactScheduleWindow("—"), "—");
});

const schedule = {
  schema_version: "uctsm.v1",
  schedule_version_id: "schedule-1",
  schedule_metadata: { name: "Protocol Schedule", schedule_type: "PRIMARY", version_number: 1, status: "APPROVED" },
  anchors: [],
  evidence: [],
  validation_issues: [],
  events: [{
    id: "event-1",
    code: "FOLLOW_UP",
    protocol_label: "Visit 6",
    display_name: "Follow-up",
    event_type: "TELEPHONE",
    timing: { type: "OFFSET" },
    applicability: [],
    conditions: [{}],
    dependencies: [],
    recurrence: {
      type: "INTERVAL",
      interval: { value: 3, unit: "MONTH" },
      start_reference: { code: "RANDOMIZATION" },
      termination: { type: "EVENT", event_code: "FINAL_VISIT" },
    },
    activities: [{ id: "activity-1", display_name: "Safety Assessment", activity_type: "ASSESSMENT", requiredness: "REQUIRED" }],
    evidence_refs: ["evidence-1"],
    interpretation_status: "CONFIRMED",
    requires_review: false,
  }],
} satisfies UniversalSchedule;

const projections: ScheduleProjection[] = [{
  event_id: "event-1",
  event_code: "FOLLOW_UP",
  title: "Follow-up",
  timing_display: "3 months after Randomization",
  window_display: "-2 weeks/+2 weeks",
  event_type_display: "Telephone",
  activities_display: ["Safety Assessment"],
  condition_display: "Conditional",
  status: "CONFIRMED",
  requires_review: false,
  evidence_refs: ["evidence-1"],
}];

test("protocol adapter renders readable schedule rows without visit-gap fields", () => {
  const [row] = toProtocolScheduleRows(schedule, projections);
  assert.equal(row.visit, "V6+");
  assert.equal(row.timing, "If required by protocol · 3 months after Randomization; Every 3 months until Final Visit");
  assert.equal(row.window, "-2 weeks/+2 weeks");
  assert.equal(row.type, "Telephone");
  assert.deepEqual(row.activities, ["Safety Assessment"]);
  assert.equal(row.appliesTo, "All enrolled patients");
  assert.equal(row.status, "Approved");
  assert.equal("visit_gap_days" in row, false);
});

test("patient adapter displays backend dates and friendly states", () => {
  const protocolRows = toProtocolScheduleRows(schedule, projections);
  const [row] = toPatientScheduleRows({
    patient_id: "patient-1",
    status: "ACTIVE",
    schedule_version_id: "schedule-1",
    events: [{
      id: "patient-event-1",
      event_definition_id: "event-1",
      occurrence_index: 0,
      status: "WAITING_FOR_CONDITION",
      nominal_start_date: "2026-09-01",
      earliest_date: "2026-08-30",
      latest_date: "2026-09-03",
      actual_date: "2026-09-02",
    }],
  }, protocolRows);
  assert.equal(row.expectedDate, "1 Sep 2026");
  assert.equal(row.allowedWindow, "30 Aug 2026 – 3 Sep 2026");
  assert.equal(row.actualDate, "2 Sep 2026");
  // UI specification section 6 status vocabulary: a conditional item that has not
  // been triggered for this patient is "Inactive", not overdue and not pending.
  assert.equal(row.status, "Inactive");
});

test("relative recurrence end preserves its calendar unit and boundary", () => {
  for (const inclusive of [true, false]) {
    const bounded = structuredClone(schedule) as UniversalSchedule;
    bounded.events[0].recurrence = { interval: { value: 2, unit: "WEEK" }, termination: {
      type: "RELATIVE", end_reference: { code: "BASELINE" }, end_offset: { value: 6, unit: "MONTH" }, inclusive,
    } };
    const [row] = toProtocolScheduleRows(bounded, projections);
    assert.ok(row.timing.includes(`Every 2 weeks ${inclusive ? "through" : "before"} 6 months after Baseline`));
  }
});

test("draft rows explain their event and assessment errors without hiding them in a global count", () => {
  const draft = structuredClone(schedule) as UniversalSchedule;
  draft.schedule_metadata.status = "VALIDATION_REQUIRED";
  draft.validation_issues = [
    { id: "issue-1", entity_id: "event-1", issue_code: "AMBIGUOUS_TIMING", severity: "ERROR", blocking: true, status: "OPEN", message: "Awaiting the last dose reference", details: {} },
    { id: "issue-2", entity_id: "activity-1", issue_code: "MISSING_EVIDENCE", severity: "ERROR", blocking: true, status: "OPEN", message: "Assessment source needs review", details: {} },
    { id: "issue-3", entity_id: "other-event", issue_code: "AMBIGUOUS_TIMING", severity: "ERROR", blocking: true, status: "OPEN", message: "Another visit's problem", details: {} },
  ];
  const [row] = toProtocolScheduleRows(draft, projections);
  assert.deepEqual(row.reviewReasons, ["Awaiting the last dose reference", "Assessment source needs review"]);
  assert.equal(row.requiresReview, true);
  assert.notEqual(row.status, "Approved");
});

test("enum labels are presentation-only and human readable", () => {
  assert.equal(displayVisitType("ONSITE/TELEPHONE"), "Site / Telephone");
  assert.equal(displayPatientState("HUMAN_REVIEW_REQUIRED"), "Review required");
  assert.equal(displayPatientState("WAITING_FOR_ANCHOR"), "Awaiting Anchor");
  assert.equal(displayPatientState("PAUSED"), "Paused");
  assert.equal(displayPatientState("CANCELLED"), "Cancelled");
});

test("an unresolved cadence retains source wording and a review requirement", () => {
  const draft = structuredClone(schedule) as UniversalSchedule;
  draft.schedule_metadata.status = "VALIDATION_REQUIRED";
  const wording = "Every 1-2 weeks for 4 weeks, then every 4-8 weeks until 6 months.";
  draft.events[0].timing = { type: "UNRESOLVED", reason: "Variable cadence needs clarification",
    source_reference: { statements: [{ text: wording, page: 19 }] } };
  const [row] = toProtocolScheduleRows(draft, []);
  assert.deepEqual(row.sourceTiming, [{ text: wording, page: 19 }]);
  assert.equal(row.requiresReview, true);
  assert.ok(row.reviewReasons?.includes("Variable cadence needs clarification"));
  assert.notEqual(row.status, "Approved");
});
