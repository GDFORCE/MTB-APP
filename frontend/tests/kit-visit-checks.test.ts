// Visit checks: a yes/no question asked at every occurrence before it goes ahead ("ANC ≥ 1.0?").
// "No" holds the occurrence until a "Yes"; no "Yes" within the protocol's limit warns, or stops the
// visits when the protocol says so. Constructed models, expected dates worked out by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { recordCheck, removeCheck } from "../src/features/kit/participant.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const CHECK = { id: "anc", text: "ANC ≥ 1.0 × 10^9/L?", help: null, recheck_days: 7, max_wait_days: 14, on_timeout: "stop", stop_visits: [], source: "Section 6.4, p. 31" };
const model = (check: Record<string, unknown> = {}, repeat: Record<string, unknown> = {}) => ({
  protocol: {}, arms: [], review_questions: [], notes: [], rules: [], events: [], questions: [],
  anchors: [anchor("scr", "start", { label: "Screening" }), anchor("d1", "relative", { label: "Day 1", relative_to: "scr", offset_days: 14, is_day1: true })],
  visits: [
    visit("screen", "scr", 0),
    visit("cyc", "d1", 0, { repeat: { every_days: 21, chain: true, count: 4, ...repeat }, check: { ...CHECK, ...check } }),
    visit("fu", "d1", 120),
  ],
}) as any;
// Screening 5 Jan 2026, Day 1 19 Jan; cycles every 21 days: 19 Jan, 9 Feb, 2 Mar, 23 Mar.
const run = (m: any, checks: Record<string, unknown> = {}, today = "2026-02-10") =>
  computeKitSchedule(m, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: {}, checks, today } as any);
const rows = (s: any, id: string) => s.rows.filter((r: any) => r.type === "visit" && r.visitId === id);
const dates = (s: any, id: string) => rows(s, id).map((r: any) => r.date);

test("with no answers the check changes nothing and is asked at every cycle", () => {
  const s = run(model());
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", "2026-02-09", "2026-03-02", "2026-03-23"]);
  assert.deepEqual(rows(s, "cyc").map((r: any) => r.check.state), ["ask", "ask", "ask", "ask"]);
  assert.equal(rows(s, "fu")[0].check, null);
});

test("a No holds the cycle without a date until a Yes; chained cycles wait with it", () => {
  const s = run(model(), { "cyc#2": [{ date: "2026-02-09", answer: "no" }] });
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", null, null, null]);
  const held = rows(s, "cyc")[1];
  assert.equal(held.status, "waiting");
  assert.equal(held.check.state, "held");
  assert.equal(held.check.nextCheck, "2026-02-16");
  assert.equal(held.check.deadline, "2026-02-23");
  assert.match(held.notes.join(" "), /Held: .* was No on 09 Feb\. Next check 16 Feb\. Needs a Yes by 23 Feb\./);
  assert.deepEqual(dates(s, "fu"), ["2026-05-19"]);
  assert.equal(s.stoppedBy.length, 0);
});

test("a Yes after a No becomes the cycle's date and later chained cycles move", () => {
  const s = run(model(), { "cyc#2": [{ date: "2026-02-16", answer: "yes" }, { date: "2026-02-09", answer: "no" }] });
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", "2026-02-16", "2026-03-09", "2026-03-30"]);
  assert.equal(rows(s, "cyc")[1].check.state, "resumed");
  assert.equal(rows(s, "cyc")[1].check.delayDays, 7);
  assert.match(rows(s, "cyc")[1].notes.join(" "), /Delayed 7 days/);
});

test("a Yes on the first check keeps the planned date", () => {
  const s = run(model(), { "cyc#2": [{ date: "2026-02-08", answer: "yes" }] });
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", "2026-02-09", "2026-03-02", "2026-03-23"]);
  assert.equal(rows(s, "cyc")[1].check.state, "ok");
});

test("a fixed-grid repeat keeps its later dates while one cycle is held", () => {
  const s = run(model({}, { chain: false }), { "cyc#2": [{ date: "2026-02-09", answer: "no" }] });
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", null, "2026-03-02", "2026-03-23"]);
});

test("still No past the waiting limit stops the remaining cycles when the protocol says so", () => {
  const s = run(model(), { "cyc#2": [{ date: "2026-02-09", answer: "no" }] }, "2026-03-01");
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19"]);
  assert.equal(s.stoppedBy.length, 1);
  assert.equal(s.stoppedBy[0].rule, "check:cyc");
  assert.equal(s.stoppedBy[0].date, "2026-02-23");
  assert.deepEqual(s.stoppedBy[0].visits.map((v: any) => v.key), ["cyc#2", "cyc#3", "cyc#4"]);
  assert.match(s.warnings.join(" "), /no Yes to .* within 14 days of the planned date \(by 23 Feb\)\. The protocol stops these visits/);
  assert.deepEqual(dates(s, "fu"), ["2026-05-19"], "visits the check doesn't name carry on");
});

test("a Yes that comes after the limit still stops the cycles", () => {
  const s = run(model(), { "cyc#2": [{ date: "2026-02-09", answer: "no" }, { date: "2026-02-27", answer: "yes" }] }, "2026-03-01");
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19"]);
  assert.equal(s.stoppedBy[0].rule, "check:cyc");
});

test("with no stated consequence the limit only warns and nothing stops", () => {
  const s = run(model({ on_timeout: "warn" }), { "cyc#2": [{ date: "2026-02-09", answer: "no" }] }, "2026-03-01");
  assert.deepEqual(dates(s, "cyc"), ["2026-01-19", null, null, null]);
  assert.equal(s.stoppedBy.length, 0);
  assert.match(s.warnings.join(" "), /Check what the protocol says next/);
});

test("stop_visits stop other visits with the check", () => {
  const s = run(model({ stop_visits: ["fu"] }), { "cyc#2": [{ date: "2026-02-09", answer: "no" }] }, "2026-03-01");
  assert.deepEqual(dates(s, "fu"), []);
});

test("recordCheck keeps answers in date order and removeCheck clears the last one", () => {
  const p: any = { startAnchor: "scr", startDate: "2026-01-05", answers: {} };
  const a = recordCheck(recordCheck(p, "cyc#2", "2026-02-16", "yes"), "cyc#2", "2026-02-09", "no");
  assert.deepEqual(a.checks!["cyc#2"], [{ date: "2026-02-09", answer: "no" }, { date: "2026-02-16", answer: "yes" }]);
  const b = removeCheck(removeCheck(a, "cyc#2", 0), "cyc#2", 0);
  assert.deepEqual(b.checks, {});
});
