import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import {
  anchorDateProblems, anchorPositions, answer, countedFromText, dmyToIso, enrolmentAnchors, enterableAnchors, formatDmyInput, isOverdue, isoToDmy, kitStatus, logEvent, nextVisit, progress, recordVisit, removeEvent, setAnchorDate,
} from "../src/features/kit/participant.ts";

const base = { startAnchor: "screening", startDate: "2026-11-02", answers: { seq: "AB" }, events: [], actuals: {}, doseTimes: {} };

test("recording and clearing a visit date leaves the rest of the record alone", () => {
  const done = recordVisit(base, "scr#1", "2026-11-03");
  assert.deepEqual(done.actuals, { "scr#1": "2026-11-03" });
  assert.deepEqual(base.actuals, {});
  assert.deepEqual(recordVisit(done, "scr#1", null).actuals, {});
});

test("answers, anchor dates and events are added and removed", () => {
  const p = setAnchorDate(answer(base, "runin_ok", "yes"), "surgery", "2027-01-28");
  assert.deepEqual(p.answers, { seq: "AB", runin_ok: "yes", "@surgery": "2027-01-28" });
  assert.deepEqual(answer(p, "runin_ok", null).answers, { seq: "AB", "@surgery": "2027-01-28" });
  const logged = logEvent(p, { type: "pd", date: "2027-02-15", answers: {} }, "k1");
  assert.equal(logged.events.length, 1);
  assert.equal(removeEvent(logged, "k1").events.length, 0);
});

const row = (over: Record<string, unknown>) => ({
  type: "visit", key: "v#1", status: "scheduled", date: "2026-11-10", windowEnd: "2026-11-12", ...over,
}) as any;

test("next visit is the first still-open one; overdue means its window closed", () => {
  const schedule = { rows: [row({ key: "a", status: "done" }), row({ key: "b", date: "2026-11-01", windowEnd: "2026-11-03" }), row({ key: "c" })] } as any;
  assert.equal(nextVisit(schedule, "2026-11-05")?.key, "c");
  assert.equal(isOverdue(schedule.rows[1], "2026-11-05"), true);
  assert.equal(isOverdue(schedule.rows[2], "2026-11-05"), false);
  assert.deepEqual(progress(schedule), { done: 1, total: 3 });
});

test("form dates are DD/MM/YYYY and must exist", () => {
  assert.equal(formatDmyInput("02112026"), "02/11/2026");
  assert.equal(dmyToIso("02/11/2026"), "2026-11-02");
  assert.equal(dmyToIso("30/02/2026"), null);
  assert.equal(isoToDmy("2026-11-02"), "02/11/2026");
});

test("list status: overdue beats active; an ended or fully done schedule is completed", () => {
  const today = "2026-11-05";
  assert.equal(kitStatus({ rows: [row({ date: "2026-11-01", windowEnd: "2026-11-02" })] } as any, today), "overdue");
  assert.equal(kitStatus({ rows: [row({})] } as any, today), "active");
  assert.equal(kitStatus({ rows: [row({ status: "done" })] } as any, today), "completed");
  assert.equal(kitStatus({ rows: [row({})], ended: { event: "wd", date: today, cancelled: 1 } } as any, today), "completed");
  assert.equal(kitStatus({ rows: [] } as any, today), "no_visits");
});

// Screening is "within 28 days before Day 1", so the real gap differs per patient: an entered
// randomisation date must win over the one counted from screening.

const anchorDef = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], ...over,
});
const visitDef = (id: string, anchor: string, offset: number) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null,
});
const screenRand = {
  protocol: {}, arms: [], questions: [], review_questions: [], notes: [], rules: [],
  anchors: [
    anchorDef("screening", "start", { label: "Screening" }),
    anchorDef("surgery", "ask", { label: "Surgery" }),
    anchorDef("rand", "relative", { label: "Randomisation", relative_to: "screening", offset_days: 28, is_day1: true }),
    anchorDef("pd", "ask", { label: "Progression" }),
  ],
  visits: [visitDef("scr", "screening", 0), visitDef("d1", "rand", 0), visitDef("w2", "rand", 14)],
  events: [{ id: "prog", label: "Progression", repeatable: false, ends_schedule: false, sets_anchor: "pd", note: null, questions: [], visits: [] }],
} as any;
const dateOf = (s: any, id: string) => s.rows.find((r: any) => r.visitId === id)?.date;

test("an entered randomisation date replaces the one counted from screening", () => {
  const counted = computeKitSchedule(screenRand, {}, { startAnchor: "screening", startDate: "2026-11-02", answers: {} } as any);
  assert.equal(dateOf(counted, "d1"), "2026-11-30");
  const entered = computeKitSchedule(screenRand, {}, { startAnchor: "screening", startDate: "2026-11-02", answers: { "@rand": "2026-11-12" } } as any);
  assert.equal(dateOf(entered, "scr"), "2026-11-02");
  assert.equal(dateOf(entered, "d1"), "2026-11-12");
  assert.equal(dateOf(entered, "w2"), "2026-11-26");
});

test("dates the site can enter: Day 1 first, never the start date or one an event sets", () => {
  assert.deepEqual(enterableAnchors(screenRand).map((a) => a.id), ["rand", "surgery"]);
  assert.match(countedFromText(screenRand, screenRand.anchors[2])!, /Screening \+ 28 days/);
  assert.equal(countedFromText(screenRand, screenRand.anchors[1]), null);
});

test("randomisation before screening is refused; on or after it is fine", () => {
  assert.deepEqual(anchorDateProblems(screenRand, "screening", "2026-11-02", { "@rand": "2026-10-30" }),
    { rand: "Randomisation can't be before Screening." });
  assert.deepEqual(anchorDateProblems(screenRand, "screening", "2026-11-02", { "@rand": "2026-11-02" }), {});
});

test("a screening date counted back from Day 1 can't be entered after Day 1", () => {
  const backwards = { ...screenRand, anchors: [
    anchorDef("d1", "start", { label: "Day 1", is_day1: true }),
    anchorDef("scr", "relative", { label: "Screening", relative_to: "d1", offset_days: -28 }),
  ] } as any;
  assert.deepEqual(anchorDateProblems(backwards, "d1", "2026-11-30", { "@scr": "2026-12-02" }), { scr: "Screening can't be after Day 1." });
  assert.deepEqual(anchorDateProblems(backwards, "d1", "2026-11-30", { "@scr": "2026-11-10" }), {});
});

test("dates at or before Day 1 are asked when the patient is added; later ones go on the schedule", () => {
  // Shaped like the ORION sample: surgery is planned after Day 1 (earliest arm: Day 1 + 21),
  // adjuvant cycle 1 counts from surgery, run-in starts between screening and Day 1.
  const m = { ...screenRand, anchors: [
    anchorDef("screening", "start", { label: "Screening" }),
    anchorDef("runin", "relative", { label: "Run-in", relative_to: "screening", offset_days: 7 }),
    anchorDef("rand", "relative", { label: "Randomisation", relative_to: "screening", offset_days: 14, is_day1: true }),
    anchorDef("surgery", "ask", { label: "Surgery", estimates: [
      { when: [{ question: "arm", in: ["A"] }], relative_to: "rand", offset_days: 77 },
      { when: [{ question: "arm", in: ["B"] }], relative_to: "rand", offset_days: 21 }] }),
    anchorDef("adj", "relative", { label: "Adjuvant C1", relative_to: "surgery", offset_days: 42 }),
    anchorDef("pd", "ask", { label: "Progression" }),
  ] } as any;
  assert.deepEqual(anchorPositions(m), { screening: 0, runin: 7, rand: 14, surgery: 35, adj: 77, pd: null });
  assert.deepEqual(enrolmentAnchors(m).map((a) => a.id), ["rand", "runin"]);
});

test("a date set by a visit takes that visit's place in the plan", () => {
  const m = { ...screenRand, anchors: [
    anchorDef("screening", "start", { label: "Screening" }),
    anchorDef("d1", "ask", { label: "Day 1", is_day1: true, set_by_visit: "d1v" }),
    anchorDef("p2", "ask", { label: "Period 2", set_by_visit: "p2v" }),
  ], visits: [visitDef("d1v", "screening", 21), visitDef("p2v", "d1", 56)] } as any;
  assert.deepEqual(anchorPositions(m), { screening: 0, d1: 21, p2: 77 });
  assert.deepEqual(enrolmentAnchors(m).map((a) => a.id), ["d1"]);
});
