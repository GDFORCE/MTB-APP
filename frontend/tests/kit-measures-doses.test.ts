// Values measured at visit checks and dose levels: the Yes/No worked out from entered values (a
// number, a multiple of baseline, the worst so far), and dose reductions from a check, a logged
// event or staff. Constructed models, expected dates and levels worked out by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { parseMeasured, recordCheck, setBaseline, setDoseLevel } from "../src/features/kit/participant.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const MEASURES = [
  { id: "anc", label: "ANC", unit: "× 10^9/L", kind: "value", worse: "lower", baseline: null },
  { id: "cr", label: "Creatinine", unit: null, kind: "value", worse: "higher", baseline: "Screening value" },
  { id: "neut", label: "Neuropathy", unit: null, kind: "grade", worse: "higher", baseline: null },
];
const DOSE = {
  id: "dx", label: "Drug X", levels: [{ id: "0", label: "100 mg" }, { id: "-1", label: "75 mg" }, { id: "-2", label: "50 mg" }],
  start: "0", visits: ["cyc"], below_lowest: "warn", stop_visits: [], source: null,
};
const check = (over: Record<string, unknown> = {}) => ({
  id: "ok", text: "ANC ≥ 1.5?", help: null, recheck_days: 7, max_wait_days: null, on_timeout: "warn", stop_visits: [], source: null,
  measures: ["anc"], limits: [{ measure: "anc", op: ">=", value: 1.5 }], dose_changes: [], ...over,
});
const model = (ck: Record<string, unknown> = {}, extra: Record<string, unknown> = {}, dose: Record<string, unknown> = {}) => ({
  protocol: {}, arms: [], review_questions: [], notes: [], rules: [], events: [], questions: [],
  measures: MEASURES, doses: [{ ...DOSE, ...dose }],
  anchors: [anchor("scr", "start"), anchor("d1", "relative", { relative_to: "scr", offset_days: 14, is_day1: true })],
  visits: [visit("scr", "scr", 0), visit("cyc", "d1", 0, { repeat: { every_days: 21, chain: true, count: 4 }, check: check(ck) })],
  ...extra,
}) as any;
// Screening 5 Jan 2026, Day 1 19 Jan; cycles every 21 days: 19 Jan, 9 Feb, 2 Mar, 23 Mar.
const run = (m: any, rec: Record<string, unknown> = {}) =>
  computeKitSchedule(m, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: {}, today: "2026-02-10", ...rec } as any);
const cyc = (s: any) => s.rows.filter((r: any) => r.type === "visit" && r.visitId === "cyc");
const dates = (s: any) => cyc(s).map((r: any) => r.date);
const levels = (s: any) => cyc(s).map((r: any) => r.doses[0]?.level ?? null);
const low = (date: string, anc: number) => ({ date, values: { anc } });

test("entered values work the answer out: below the limit holds the cycle, above it resumes", () => {
  const held = run(model(), { checks: { "cyc#2": [low("2026-02-09", 0.8)] } });
  assert.deepEqual(dates(held), ["2026-01-19", null, null, null]);
  const c = cyc(held)[1].check;
  assert.equal(c.state, "held");
  assert.deepEqual(c.answers[0], { date: "2026-02-09", values: { anc: 0.8 }, result: "no", by: "values", failed: ["ANC ≥ 1.5 × 10^9/L"] });
  assert.deepEqual(c.limits, ["ANC ≥ 1.5 × 10^9/L"]);
  assert.match(cyc(held)[1].notes.join(" "), /Held: ANC ≥ 1\.5 × 10\^9\/L not met on 09 Feb/);
  const resumed = run(model(), { checks: { "cyc#2": [low("2026-02-09", 0.8), low("2026-02-16", 1.6)] } });
  assert.deepEqual(dates(resumed), ["2026-01-19", "2026-02-16", "2026-03-09", "2026-03-30"]);
  assert.equal(cyc(resumed)[1].check.state, "resumed");
});

test("a limit against baseline waits for the baseline; staff can answer by hand meanwhile", () => {
  const m = model({ measures: ["cr"], limits: [{ measure: "cr", op: "<=", baseline_factor: 1.5 }], text: "Creatinine ≤ 1.5 × baseline?" });
  const noBase = run(m, { checks: { "cyc#2": [{ date: "2026-02-09", values: { cr: 1.4 } }] } });
  const c = cyc(noBase)[1].check;
  assert.equal(c.state, "ask");
  assert.equal(c.answers[0].result, null);
  assert.deepEqual(c.needsBaseline, ["Creatinine"]);
  const byHand = run(m, { checks: { "cyc#2": [{ date: "2026-02-09", values: { cr: 1.4 }, answer: "yes" }] } });
  assert.deepEqual([cyc(byHand)[1].check.state, cyc(byHand)[1].check.answers[0].by], ["ok", "staff"]);
  const ok = run(m, { baselines: { cr: 1.0 }, checks: { "cyc#2": [{ date: "2026-02-09", values: { cr: 1.4 } }] } });
  assert.deepEqual([cyc(ok)[1].check.state, cyc(ok)[1].check.answers[0].by, cyc(ok)[1].check.needsBaseline], ["ok", "values", []]);
  const over = run(m, { baselines: { cr: 1.0 }, checks: { "cyc#2": [{ date: "2026-02-09", values: { cr: 1.6 } }] } });
  assert.equal(cyc(over)[1].check.state, "held");
});

test("a dose reduction from the worst value during the hold applies to that cycle and later ones", () => {
  const m = model({ dose_changes: [{ if: [{ measure: "anc", of: "worst_here", op: "<", value: 0.5 }], dose: "dx", step: -1, to: null, stop: false, occurrence: null, and_later: false, note: null }] });
  const s = run(m, { checks: { "cyc#2": [low("2026-02-09", 0.4), low("2026-02-16", 1.6)] } });
  assert.deepEqual(levels(s), ["0", "-1", "-1", "-1"]);
  assert.equal(cyc(s)[1].doses[0].levelLabel, "75 mg");
  assert.match(cyc(s)[1].notes.join(" "), /Drug X: 75 mg \(changed: worst ANC < 0\.5 × 10\^9\/L\)/);
  const mild = run(m, { checks: { "cyc#2": [low("2026-02-09", 0.9), low("2026-02-16", 1.6)] } });
  assert.deepEqual(levels(mild), ["0", "0", "0", "0"]);
});

test("how long recovery took decides the reduction", () => {
  const m = model({ dose_changes: [{ if: [{ held_days: { op: ">", value: 7 } }], dose: "dx", step: -1, to: null, stop: false, occurrence: null, and_later: false, note: "Recovery took more than 7 days" }] });
  assert.deepEqual(levels(run(m, { checks: { "cyc#2": [low("2026-02-09", 0.8), low("2026-02-16", 1.6)] } })), ["0", "0", "0", "0"]);
  const slow = run(m, { checks: { "cyc#2": [low("2026-02-09", 0.8), low("2026-02-17", 1.6)] } });
  assert.deepEqual(levels(slow), ["0", "-1", "-1", "-1"]);
  assert.match(cyc(slow)[1].notes.join(" "), /Recovery took more than 7 days/);
});

test("a change for the second occurrence skips the first", () => {
  const m = model({ dose_changes: [{ if: [{ measure: "anc", of: "worst_here", op: "<", value: 1 }], dose: "dx", step: -1, to: null, stop: false, occurrence: 2, and_later: true, note: null }] });
  const s = run(m, { checks: {
    "cyc#2": [low("2026-02-09", 0.8), low("2026-02-16", 1.6)],
    "cyc#3": [low("2026-03-09", 0.7), low("2026-03-16", 1.7)],
  } });
  assert.deepEqual(levels(s), ["0", "0", "-1", "-1"]);
});

test("below the lowest level: stops the drug's visits only when the protocol says so", () => {
  const change = { dose_changes: [{ if: [{ measure: "anc", of: "worst_here", op: "<", value: 0.5 }], dose: "dx", step: -1, to: null, stop: false, occurrence: null, and_later: false, note: null }] };
  const rec = { checks: { "cyc#2": [low("2026-02-09", 0.4), low("2026-02-16", 1.6)] } };
  const stop = run(model(change, {}, { start: "-2", below_lowest: "stop" }), rec);
  assert.deepEqual(dates(stop), ["2026-01-19"]);
  assert.equal(stop.stoppedBy[0].rule, "dose:dx");
  assert.deepEqual(stop.stoppedBy[0].visits.map((v: any) => v.key), ["cyc#2", "cyc#3", "cyc#4"]);
  const warn = run(model(change, {}, { start: "-2" }), rec);
  assert.deepEqual(levels(warn), ["-2", "-2", "-2", "-2"]);
  assert.match(warn.warnings.join(" "), /would go below its lowest level/);
});

test("worst grade so far can discontinue the drug", () => {
  const m = model({
    measures: ["neut"], limits: [{ measure: "neut", op: "<=", value: 1 }],
    dose_changes: [{ if: [{ measure: "neut", of: "worst_so_far", op: ">=", value: 4 }], dose: "dx", step: null, to: null, stop: true, occurrence: null, and_later: false, note: "Grade 4 at any time" }],
  });
  const s = run(m, { checks: { "cyc#2": [{ date: "2026-02-09", values: { neut: 4 } }, { date: "2026-02-23", values: { neut: 1 } }] } });
  assert.deepEqual(dates(s), ["2026-01-19"]);
  assert.match(s.stoppedBy[0].effect, /Drug X stopped: Grade 4 at any time/);
});

test("a rule on a logged event lowers the doses after it", () => {
  const extra = {
    events: [{ id: "ae", label: "Adverse event", repeatable: true, ends_schedule: false, note: null, visits: [],
      questions: [{ id: "grade", label: "Grade", text: "Grade?", kind: "choice", scope: "event", options: [{ value: "3", label: "3" }, { value: "4", label: "4" }] }] }],
    rules: [{ id: "ae3", label: "Grade 3 AE", when: { event: "ae" }, if: [{ event_question: "grade", in: ["3"] }], stop_visits: [], stop_all: false,
      same_day: "keep", starts: null, blocks: [], allowed: null, effect: "", source: null, dose_changes: [{ dose: "dx", step: -1, to: null, stop: false }] }],
  };
  const s = run(model({}, extra), { events: [{ key: "e1", type: "ae", date: "2026-02-20", answers: { grade: "3" } }] });
  assert.deepEqual(levels(s), ["0", "0", "-1", "-1"]);
  const g4 = run(model({}, extra), { events: [{ key: "e1", type: "ae", date: "2026-02-20", answers: { grade: "4" } }] });
  assert.deepEqual(levels(g4), ["0", "0", "0", "0"]);
});

test("a level staff set holds from that dose on; an unstated start stays unknown", () => {
  const s = run(model(), { doseLevels: { "cyc#2": { dx: "-2" } } });
  assert.deepEqual(levels(s), ["0", "-2", "-2", "-2"]);
  assert.deepEqual(cyc(s).map((r: any) => r.doses[0].manual), [false, true, false, false]);
  const unknown = run(model({}, {}, { start: null }));
  assert.deepEqual(levels(unknown), [null, null, null, null]);
});

test("record helpers keep values, baselines and dose levels tidy", () => {
  const p: any = { startAnchor: "scr", startDate: "2026-01-05", answers: {} };
  const a = recordCheck(p, "cyc#2", "2026-02-09", null, { anc: 0.8 });
  assert.deepEqual(a.checks, { "cyc#2": [{ date: "2026-02-09", values: { anc: 0.8 } }] });
  assert.equal(recordCheck(p, "cyc#2", "2026-02-09", null, {}), p);
  assert.deepEqual(setBaseline(p, "cr", 1.1).baselines, { cr: 1.1 });
  assert.equal("baselines" in setBaseline(setBaseline(p, "cr", 1.1), "cr", null), false);
  assert.deepEqual(setDoseLevel(p, "cyc#2", "dx", "-1").doseLevels, { "cyc#2": { dx: "-1" } });
  assert.equal("doseLevels" in setDoseLevel(setDoseLevel(p, "cyc#2", "dx", "-1"), "cyc#2", "dx", null), false);
  assert.deepEqual([parseMeasured("1,5", "value"), parseMeasured("3", "grade"), parseMeasured("2.5", "grade"), parseMeasured("x", "value")], [1.5, 3, null, null]);
});
