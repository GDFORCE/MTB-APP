// "Check in at least 12 hours prior to dosing" (CRD/09): staff record the Day 0 check-in time, and
// the Day 1 dose may not start before check-in + 12 h. Constructed model, times worked out by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { formatTimeInput, setVisitTime, validTime } from "../src/features/kit/participant.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Period", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const MODEL = {
  protocol: {}, arms: [], review_questions: [], notes: [], questions: [], rules: [], events: [],
  anchors: [anchor("scr", "start"), anchor("p1", "relative", { relative_to: "scr", offset_days: 7, is_day1: true }),
    anchor("p2", "ask", { estimates: [{ when: [], relative_to: "p1", offset_days: 21 }] })],
  visits: [
    visit("d0", "p1", -1, { arrive_before: { visit: "d1", hours: 12 } }), visit("d1", "p1", 0),
    visit("p2d0", "p2", -1, { arrive_before: { visit: "p2d1", hours: 12 } }), visit("p2d1", "p2", 0),
  ],
} as any;
// Period I: Day 0 = 11 Jan 2026, Day 1 = 12 Jan.
const run = (rec: Record<string, unknown> = {}) =>
  computeKitSchedule(MODEL, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: { "@p2": "2026-02-02" }, ...rec } as any);
const row = (s: any, id: string) => s.rows.find((r: any) => r.type === "visit" && r.visitId === id);

test("before check-in the rule is shown and the right visits ask for a time", () => {
  const s = run();
  assert.equal(row(s, "d0").timeKind, "checkin");
  assert.equal(row(s, "d1").timeKind, "dose");
  assert.ok(row(s, "d0").notes.includes("Record the check-in time: the D1 dose must be at least 12 h after it."));
  assert.ok(row(s, "d1").notes.includes("Dose at least 12 h after the D0 check-in."));
});

test("a check-in time gives the earliest dose time, per period", () => {
  const s = run({ doseTimes: { "d0#1": "20:30", "p2d0#1": "18:00" } });
  assert.ok(row(s, "d1").notes.includes("Dose from 08:30, 12 Jan (12 h after the D0 check-in)."));
  assert.ok(row(s, "p2d1").notes.includes("Dose from 06:00, 02 Feb (12 h after the P2D0 check-in)."));
  assert.equal(row(s, "d0").notes.some((n: string) => n.startsWith("Record the check-in time")), false);
  assert.equal(row(s, "d0").time, "20:30");
});

test("a dose time too soon after check-in is a deviation; one in time is not", () => {
  const early = run({ doseTimes: { "d0#1": "20:30", "d1#1": "07:00" } });
  assert.deepEqual(early.warnings, ["D1: dosed at 07:00 on 12 Jan, 10.5 h after the D0 check-in; the protocol asks for at least 12 h."]);
  assert.deepEqual(run({ doseTimes: { "d0#1": "20:30", "d1#1": "09:00" } }).warnings, []);
});

test("a check-in recorded on another day counts from that day", () => {
  const s = run({ actuals: { "d0#1": "2026-01-12" }, doseTimes: { "d0#1": "01:00" } });
  assert.ok(row(s, "d1").notes.includes("Dose from 13:00, 12 Jan (12 h after the D0 check-in)."));
});

test("time helpers", () => {
  assert.equal(formatTimeInput("2030"), "20:30");
  assert.equal(formatTimeInput("9"), "9");
  assert.deepEqual([validTime("20:30"), validTime("24:00"), validTime("9:30")], ["20:30", null, null]);
  const p: any = { startAnchor: "scr", startDate: "2026-01-05", answers: {} };
  assert.deepEqual(setVisitTime(p, "d0#1", "20:30").doseTimes, { "d0#1": "20:30" });
  assert.deepEqual(setVisitTime(setVisitTime(p, "d0#1", "20:30"), "d0#1", null).doseTimes, {});
});
