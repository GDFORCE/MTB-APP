// History-based visit intervals (scenarios A1-A12) on the synthetic treat-and-extend fixture modelled
// on NCT02130024 (RIVAL). The model is the backend's clean-up of
// backend/tests/fixtures/kit_synthetic/rival_treat_extend.raw.json; its assumptions are written there.
// Every expected date below was worked out by hand from the fixture's rules, not by the engine.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { kitVisitRows } from "../src/features/kit/patientRows.ts";

const FIXTURE = new URL("../../backend/tests/fixtures/kit_synthetic/rival_treat_extend.model.json", import.meta.url);
const base = () => JSON.parse(readFileSync(FIXTURE, "utf8"));
const withRule = (patch: Record<string, unknown>) => {
  const m = base();
  Object.assign(m.visits[0].repeat.adaptive, patch);
  return m;
};

type Step = { n: number; actual?: string; finding?: number; crc?: number; choice?: number };
const at = (n: number) => `inj#${n}`;
/** The participant record: Baseline 5 Jan 2026 (a Monday), injections recorded as listed. */
function record(steps: Step[], extra: Record<string, unknown> = {}) {
  const actuals: Record<string, string> = {}, observations: Record<string, Record<string, any>> = {};
  for (const s of steps) {
    if (s.actual) actuals[at(s.n)] = s.actual;
    const o: Record<string, any> = {};
    if (s.finding != null) o.activity_signs = { value: s.finding, at: "2026-01-01T09:00:00Z", by: "pi-1", id: `f${s.n}` };
    if (s.crc != null) o.crc_weeks = { value: s.crc, at: "2026-01-01T09:00:00Z", by: "crc-1", id: `r${s.n}` };
    if (s.choice != null) o["@interval"] = { value: s.choice, at: "2026-01-01T09:00:00Z", by: "pi-1", id: `c${s.n}` };
    if (Object.keys(o).length) observations[at(s.n)] = o;
  }
  return { startAnchor: "baseline", startDate: "2026-01-05", answers: {}, events: [], actuals, observations, today: "2026-06-10", ...extra };
}
const run = (steps: Step[], model = base(), extra = {}) => computeKitSchedule(model, {}, record(steps, extra) as any);
const row = (s: any, key: string) => s.rows.find((r: any) => r.key === key);
const keys = (s: any) => s.rows.filter((r: any) => r.type === "visit").map((r: any) => r.key);

// The standard history: loading doses on their targets, then two extensions (4 → 6 → 8 weeks).
const LOADING: Step[] = [
  { n: 1, actual: "2026-01-05" }, { n: 2, actual: "2026-02-02" },
  { n: 3, actual: "2026-03-02", finding: 0 }, { n: 4, actual: "2026-04-13", finding: 0 },
];
const at8weeks = (finding: number | undefined, more: Partial<Step> = {}): Step[] =>
  [...LOADING, { n: 5, actual: "2026-06-08", finding, ...more }];

test("loading doses keep the 4-week grid and the first decision extends from 4 to 6 weeks", () => {
  const s = run(LOADING);
  assert.deepEqual([1, 2, 3, 4].map((n) => row(s, at(n)).actual), ["2026-01-05", "2026-02-02", "2026-03-02", "2026-04-13"]);
  assert.equal(row(s, at(4)).planned, "2026-04-13");
  assert.equal(row(s, at(4)).adaptive.intervalDays, 42);
  assert.equal(row(s, at(5)).planned, "2026-06-08");
  assert.equal(row(s, at(5)).adaptive.intervalDays, 56);
});

test("A1 current gap 8 weeks, no activity: the next gap is 10 weeks", () => {
  const r = row(run(at8weeks(0)), at(6));
  assert.equal(r.adaptive.currentDays, 56);
  assert.equal(r.adaptive.intervalDays, 70);
  assert.equal(r.planned, "2026-08-17");
  assert.deepEqual([r.windowStart, r.windowEnd], ["2026-08-10", "2026-08-24"]);
  assert.equal(r.adaptive.capDays, null);
});

test("A2 one activity sign after an extension: the next gap shortens to 6 weeks", () => {
  const r = row(run(at8weeks(1)), at(6));
  assert.equal(r.adaptive.intervalDays, 42);
  assert.equal(r.planned, "2026-07-20");
});

test("A3 two or more signs after an extension: back to 4 weeks", () => {
  for (const signs of [2, 3]) {
    const r = row(run(at8weeks(signs)), at(6));
    assert.equal(r.adaptive.intervalDays, 28);
    assert.equal(r.planned, "2026-07-06");
  }
});

test("A4 limits: a decrease at the minimum stays at 4 weeks and an extension at the maximum stays at 12 weeks", () => {
  const atMin = run([{ n: 1, actual: "2026-01-05" }, { n: 2, actual: "2026-02-02" }, { n: 3, actual: "2026-03-02", finding: 1 }]);
  assert.equal(row(atMin, at(4)).adaptive.intervalDays, 28);
  assert.equal(row(atMin, at(4)).adaptive.limitedBy, "min");
  assert.equal(row(atMin, at(4)).planned, "2026-03-30");
  const atMax = run([...at8weeks(0), { n: 6, actual: "2026-08-17", finding: 0 }, { n: 7, actual: "2026-11-09", finding: 0 }]);
  assert.equal(row(atMax, at(7)).adaptive.intervalDays, 84, "the fourth extension reaches 12 weeks (no activity yet, so no cap)");
  assert.equal(row(atMax, at(8)).adaptive.intervalDays, 84);
  assert.equal(row(atMax, at(8)).adaptive.limitedBy, "max");
  assert.equal(row(atMax, at(8)).planned, "2027-02-01");
});

// A5: extensions at #3, #4, #5 (three attempts), activity at the 10-week interval (#6), then the
// fourth extension attempt at #7: capped at 10 - 2 = 8 weeks.
const A5: Step[] = [...at8weeks(0), { n: 6, actual: "2026-08-17", finding: 1 }, { n: 7, actual: "2026-10-12", finding: 0 }];

test("A5 fourth extension: three earlier attempts and activity at 10 weeks cap it at 8 weeks", () => {
  const s = run(A5);
  assert.equal(row(s, at(7)).adaptive.intervalDays, 56, "activity at 10 weeks shortens to 8");
  const r = row(s, at(8));
  assert.equal(r.adaptive.currentDays, 56);
  assert.equal(r.adaptive.calculatedDays, 56);
  assert.equal(r.adaptive.intervalDays, 56);
  assert.equal(r.adaptive.capDays, 56);
  assert.equal(r.planned, "2026-12-07");
});

test("A5 with several activity intervals the longest one sets the cap", () => {
  // Activity at 4 weeks (#3), 10 weeks (#7) and 8 weeks (#8); the fourth extension attempt is at #9.
  const steps: Step[] = [
    { n: 1, actual: "2026-01-05" }, { n: 2, actual: "2026-02-02" }, { n: 3, actual: "2026-03-02", finding: 1 },
    { n: 4, actual: "2026-03-30", finding: 0 }, { n: 5, actual: "2026-05-11", finding: 0 }, { n: 6, actual: "2026-07-06", finding: 0 },
    { n: 7, actual: "2026-09-14", finding: 1 }, { n: 8, actual: "2026-11-09", finding: 1 }, { n: 9, actual: "2026-12-21", finding: 0 },
    { n: 10, actual: "2027-02-15", finding: 0 },
  ];
  const s = run(steps);
  assert.deepEqual([4, 5, 6, 7, 8, 9].map((n) => row(s, at(n)).planned),
    ["2026-03-30", "2026-05-11", "2026-07-06", "2026-09-14", "2026-11-09", "2026-12-21"]);
  // Longest activity interval 10 weeks -> cap 8 weeks. (The first, 4 weeks, would give 4; the latest, 8 weeks, would give 6.)
  assert.equal(row(s, at(10)).adaptive.intervalDays, 56);
  assert.equal(row(s, at(10)).planned, "2027-02-15");
  assert.equal(row(s, at(11)).adaptive.calculatedDays, 56);
  assert.equal(row(s, at(11)).adaptive.capDays, 56, "the extension to 10 weeks is held at the recorded cap");
  assert.equal(row(s, at(11)).planned, "2027-04-12");
});

test("A6 a later extension keeps the recorded cap and a confirmed finding still shortens the interval", () => {
  const later = run([...A5, { n: 8, actual: "2026-12-07", finding: 0 }]);
  assert.equal(row(later, at(9)).adaptive.capDays, 56);
  assert.equal(row(later, at(9)).adaptive.intervalDays, 56);
  assert.equal(row(later, at(9)).planned, "2027-02-01");
  const shorter = run([...A5, { n: 8, actual: "2026-12-07", finding: 1 }]);
  assert.equal(row(shorter, at(9)).adaptive.intervalDays, 42);
  assert.equal(row(shorter, at(9)).planned, "2027-01-18");
});

test("A7 a missing finding leaves the next injection pending while fixed visits stay dated", () => {
  const s = run(at8weeks(undefined));
  const r = row(s, at(6));
  assert.equal(r.status, "waiting");
  assert.equal(r.planned, null);
  assert.deepEqual(r.needs.map((n: any) => [n.input, n.at, n.roles]), [["activity_signs", "inj#5", ["pi"]]]);
  assert.equal(row(s, at(7)), undefined, "nothing after the pending injection is generated");
  assert.equal(row(s, "m12#1").planned, "2027-01-04");
  assert.equal(row(s, "m24#1").planned, "2028-01-03");
  // The patient sees the fixed visits, never an undated injection.
  const patient = kitVisitRows({ model: base(), site_answers: {}, participant: record(at8weeks(undefined)), workflow_version: 1,
    patient: { id: "p", trial_id: "t" }, trial: {} } as any);
  assert.ok(patient.some((x: any) => x.id === "m12#1"));
  assert.ok(!patient.some((x: any) => x.id === at(6)));
});

// Under a precedence that needs the recommendation, every decision needs one: the earlier ones agree.
const agreed = (steps: Step[]) => steps.map((x) => (x.n === 3 ? { ...x, crc: 6 } : x.n === 4 ? { ...x, crc: 8 } : x));

test("A7 a missing recommendation keeps the booking pending when the recommendation decides it", () => {
  const model = withRule({ precedence: "recommendation" });
  const first = row(run(at8weeks(0), model), at(4));
  assert.equal(first.planned, null, "the first decision already waits for it (the visit itself is recorded)");
  assert.deepEqual(first.needs.map((n: any) => [n.input, n.at, n.roles]), [["crc_weeks", "inj#3", ["pi", "crc"]]]);
  const waiting = row(run(agreed(at8weeks(0)), model), at(6));
  assert.equal(waiting.status, "waiting");
  assert.deepEqual(waiting.needs.map((n: any) => n.input), ["crc_weeks"]);
  const booked = row(run(agreed(at8weeks(0, { crc: 10 })), model), at(6));
  assert.equal(booked.adaptive.intervalDays, 70);
  assert.equal(booked.planned, "2026-08-17");
});

test("A7 incomplete history: a gap in earlier findings leaves every later interval pending", () => {
  const steps: Step[] = [{ n: 1, actual: "2026-01-05" }, { n: 2, actual: "2026-02-02" }, { n: 3, actual: "2026-03-02", finding: 0 },
    { n: 4, actual: "2026-04-13" }, { n: 5, actual: "2026-06-08", finding: 0 }];
  const s = run(steps);
  assert.equal(row(s, at(5)).status, "done");
  assert.equal(row(s, at(5)).actual, "2026-06-08");
  assert.equal(row(s, at(5)).planned, null);
  const next = row(s, at(6));
  assert.equal(next.status, "waiting");
  assert.deepEqual(next.needs.map((n: any) => [n.input, n.at]), [["activity_signs", "inj#4"]]);
  assert.equal(row(s, "m12#1").planned, "2027-01-04");
});

test("A8 a differing recommendation follows the approved precedence", () => {
  const calculated = row(run(at8weeks(0, { crc: 12 })), at(6));
  assert.equal(calculated.adaptive.intervalDays, 70);
  assert.equal(calculated.adaptive.recommendedDays, 84);
  assert.ok(calculated.notes.some((n: string) => n.includes("12 weeks") && n.includes("calculated 10 weeks")));
  const review = withRule({ precedence: "review" });
  const asked = row(run(agreed(at8weeks(0, { crc: 12 })), review), at(6));
  assert.equal(asked.status, "waiting");
  assert.deepEqual(asked.needs.map((n: any) => [n.input, n.roles, n.options]), [["@interval", ["pi"], [70, 84]]]);
  const chosen = row(run(agreed(at8weeks(0, { crc: 12, choice: 84 })), review), at(6));
  assert.equal(chosen.adaptive.intervalDays, 84);
  assert.equal(chosen.planned, "2026-08-31");
  const same = row(run(agreed(at8weeks(0, { crc: 10 })), review), at(6));
  assert.equal(same.planned, "2026-08-17", "no choice is asked when both agree");
  const shorter = row(run(agreed(at8weeks(0, { crc: 8 })), withRule({ precedence: "shorter" })), at(6));
  assert.equal(shorter.planned, "2026-08-03");
});

test("A9 correcting an earlier finding re-dates the next injection and keeps recorded visits", () => {
  const before = run(at8weeks(0));
  const after = run(at8weeks(1));
  for (const n of [1, 2, 3, 4, 5]) assert.deepEqual(row(after, at(n)).actual, row(before, at(n)).actual);
  assert.equal(row(before, at(6)).planned, "2026-08-17");
  assert.deepEqual([row(after, at(6)).planned, row(after, at(6)).windowStart, row(after, at(6)).windowEnd],
    ["2026-07-20", "2026-07-13", "2026-07-27"]);
  // Already done under the old finding: the actual stays, the new target shows it as a deviation.
  const done = run([...at8weeks(1), { n: 6, actual: "2026-08-17" }]);
  assert.equal(row(done, at(6)).actual, "2026-08-17");
  assert.equal(row(done, at(6)).planned, "2026-07-20");
  assert.equal(row(done, at(6)).deviation, "outside_window");
});

test("A10 target, booking, actual and deviation stay distinct", () => {
  const inside = run(at8weeks(0), base(), { bookings: { [at(6)]: "2026-08-20" } });
  const r = row(inside, at(6));
  assert.deepEqual([r.planned, r.booked, r.actual, r.bookingStatus, r.deviation], ["2026-08-17", "2026-08-20", null, "in_window", undefined]);
  const outside = row(run(at8weeks(0), base(), { bookings: { [at(6)]: "2026-08-28" } }), at(6));
  assert.deepEqual([outside.planned, outside.booked, outside.bookingStatus], ["2026-08-17", "2026-08-28", "outside_window"]);
  const late = run([...at8weeks(0), { n: 6, actual: "2026-08-28", finding: 0 }], base(), { bookings: { [at(6)]: "2026-08-28" } });
  const done = row(late, at(6));
  assert.deepEqual([done.planned, done.booked, done.actual, done.deviation], ["2026-08-17", "2026-08-28", "2026-08-28", "outside_window"]);
  assert.equal(row(late, at(7)).planned, "2026-11-20", "the next interval counts from the actual date");
});

test("A11 an injection inside the Month 12 window is combined with it; Month 24 stays baseline-anchored", () => {
  const inside = run([...at8weeks(0), { n: 6, actual: "2026-08-17", finding: 0 }, { n: 7, actual: "2026-11-09", finding: 1 }]);
  const combined = row(inside, at(8));
  assert.equal(combined.planned, "2027-01-18");
  assert.deepEqual(combined.combined, [{ key: "m12#1", code: "M12", name: "Month 12 assessment", planned: "2027-01-04",
    windowStart: "2026-12-21", windowEnd: "2027-01-18" }]);
  assert.equal(row(inside, "m12#1"), undefined, "Month 12 is not listed a second time");
  const names = combined.procedures.map((p: any) => p.name);
  assert.deepEqual(names, ["BCVA", "OCT", "Disease activity assessment", "Intravitreal injection",
    "Multimodal imaging for the reading centre", "Month 12 questionnaire"]);
  assert.equal(new Set(names).size, names.length);
  const outside = run([...at8weeks(0), { n: 6, actual: "2026-08-17", finding: 0 }, { n: 7, actual: "2026-11-09", finding: 0 }]);
  assert.equal(row(outside, "m12#1").planned, "2027-01-04");
  assert.equal(row(outside, at(8)).planned, "2027-02-01");
  assert.equal(row(outside, at(8)).combined, undefined);
  for (const s of [inside, outside]) {
    assert.equal(row(s, "m24#1").planned, "2028-01-03");
    assert.deepEqual([row(s, "m24#1").windowStart, row(s, "m24#1").windowEnd], ["2027-12-20", "2028-01-17"]);
    const imaging = s.rows.filter((x: any) => x.type === "visit" && x.planned && x.planned < "2027-06-01"
      && x.procedures.some((p: any) => p.name === "Multimodal imaging for the reading centre"));
    assert.equal(imaging.length, 1, "one Month 12 imaging task either way");
  }
});

test("A12 the same inputs in any order give the same calendar, with no duplicate visits", () => {
  const steps = [...A5, { n: 8, actual: "2026-12-07", finding: 1, crc: 6 }];
  const forward = record(steps);
  const reversed = { ...forward, observations: Object.fromEntries(Object.entries(forward.observations).reverse()),
    actuals: Object.fromEntries(Object.entries(forward.actuals).reverse()) };
  const a = computeKitSchedule(base(), {}, forward as any), b = computeKitSchedule(base(), {}, reversed as any);
  assert.deepEqual(a, b);
  assert.deepEqual(computeKitSchedule(base(), {}, forward as any), a, "computing again changes nothing");
  assert.equal(new Set(keys(a)).size, keys(a).length);
});

test("a blocked interval rule never dates the injections it governs", () => {
  const model = withRule({ precedence: null, blocked: "the protocol does not say which input decides the booking" });
  const s = run(at8weeks(0, { crc: 12 }), model);
  assert.equal(row(s, at(3)).planned, "2026-03-02", "loading doses are fixed");
  const r = row(s, at(4));
  assert.equal(r.status, "done");
  assert.equal(r.planned, null);
  assert.match(r.relative ?? "", /not approved/);
  assert.equal(row(s, at(6)).planned, null);
  assert.equal(row(s, "m12#1").planned, "2027-01-04");
});
