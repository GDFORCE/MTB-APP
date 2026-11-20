// Generated calculations in the scheduler: the interpreter agrees with the backend on the shared
// vectors, and a calculation the analysis wrote (validated by the backend) produces or re-dates
// visits from what has happened, with no protocol-specific code. The model is the backend's clean-up
// of backend/tests/fixtures/kit_synthetic/gen_pk.raw.json. Expected dates are worked out by hand.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { CalcError, evaluateCalc } from "../src/features/kit/calc.ts";
import { PATTERN_HANDLERS, blockedPatternRules } from "../src/features/kit/patterns.ts";
import { kitVisitRows } from "../src/features/kit/patientRows.ts";

const read = (path: string) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const { cases } = read("../../backend/tests/fixtures/calc_vectors.json");
const model = () => read("../../backend/tests/fixtures/kit_synthetic/gen_pk.model.json");
const part = (over: Record<string, unknown> = {}) => ({ startAnchor: "day1", startDate: "2026-01-05", answers: {}, events: [], today: "2026-03-10",
  actuals: { "inj#1": "2026-01-05", "inj#2": "2026-02-03", "inj#3": "2026-03-02" }, withheld: { "inj#3": "IOP 32 mm Hg" }, ...over });
const rows = (m: any, p: any) => Object.fromEntries(computeKitSchedule(m, {}, p).rows.map((r: any) => [r.key, r]));

for (const c of cases) {
  test(`shared vector: ${c.name}`, () => {
    if (c.error) assert.throws(() => evaluateCalc(c.program, c.facts || {}, c.this), CalcError);
    else assert.deepEqual(evaluateCalc(c.program, c.facts || {}, c.this), c.expect);
  });
}

test("the rule has no handler: only its generated calculation dates the samples", () => {
  const m = model();
  assert.equal(PATTERN_HANDLERS[m.pattern_rules[0].pattern], undefined);
  assert.deepEqual(blockedPatternRules(m), []);
  const r = rows(m, part());
  assert.deepEqual([r["pk#1"].planned, r["pk#1"].windowStart, r["pk#1"].windowEnd], ["2026-01-12", "2026-01-11", "2026-01-13"]);
  assert.equal(r["pk#2"].planned, "2026-02-10");
  assert.equal(r["pk#3"], undefined, "withheld: no sample");
  assert.equal(r["inj#3"].withheld, "IOP 32 mm Hg");
  assert.deepEqual([4, 5, 6].map((n) => [r[`pk#${n}`].status, r[`pk#${n}`].relative]),
    [4, 5, 6].map((n) => ["waiting", `7 days after injection ${n}, once given`]));
  assert.equal(r["pk#1"].generated.rule, "pk_after_given");
});

test("a dose skipped by its check also counts as not given", () => {
  const m = model();
  m.visits[0].check = { id: "ok", text: "IOP below 30?", help: null, recheck_days: null, max_wait_days: null, on_timeout: "warn",
    stop_visits: [], source: null, actions: [{ do: "skip", if: [], percent: null, note: null }] };
  const r = rows(m, part({ withheld: {}, checks: { "inj#2": [{ date: "2026-02-02", answer: "skip" }] } }));
  assert.equal(r["pk#2"], undefined);
  assert.equal(r["pk#3"].planned, "2026-03-09");
});

test("rules and ending events still apply to generated occurrences, and recorded ones stay", () => {
  const m = model();
  m.events = [{ id: "stop", label: "Treatment stopped", repeatable: false, ends_schedule: false, sets_anchor: null, questions: [], visits: [], note: null }];
  m.rules = [{ id: "r_stop", label: "Stopping treatment stops sampling", when: { event: "stop" }, stop_visits: ["pk", "inj"], stop_all: false,
    same_day: "keep", starts: null, blocks: [], allowed: null, effect: "No further samples.", source: null }];
  const r = rows(m, part({ actuals: { "inj#1": "2026-01-05", "inj#2": "2026-02-03", "pk#1": "2026-01-12" }, withheld: {},
    events: [{ key: "s1", type: "stop", date: "2026-02-20", answers: {} }] }));
  assert.equal(r["pk#1"].actual, "2026-01-12");
  assert.equal(r["pk#2"].planned, "2026-02-10", "before the stop");
  assert.equal(r["pk#3"], undefined);
  assert.equal(r["inj#3"], undefined);
});

test("a calculation that is not usable never dates its visit from the nominal fields", () => {
  const m = model();
  m.pattern_rules[0].blocked = "its own test fails";
  delete m.pattern_rules[0].generated;
  const s = computeKitSchedule(m, {}, part() as any);
  assert.ok(!s.rows.some((x: any) => x.type === "visit" && x.visitId === "pk"));
  assert.ok(s.warnings.some((w: string) => w.includes("not dated until the calculation")));
  assert.deepEqual(blockedPatternRules(m).map((x: any) => x.id), ["pk_after_given"]);
});

test("a generated date calculation re-dates each occurrence of an existing visit", () => {
  const m = model();
  // "Each follow-up call on the first Wednesday at least 10 days after the previous injection."
  m.visits.push({ id: "call", code: "CALL", name: "Follow-up call", phase: "Treatment", modality: "phone", anchor: "day1", offset_days: 0,
    window_before: 0, window_after: 0, follows: "inj", follows_occurrence: null, include_if: [], procedures: [{ name: "Call", if: [] }],
    repeat: { every_days: 28, count: 3, chain: false, open_ended: false, stages: [], overrides: [] }, timepoints: [], condition: null,
    duration_days: null, min_gap: null, prep: null, check: null });
  m.pattern_rules.push({ id: "wed", pattern: "FIRST_WEDNESDAY_AFTER", label: "First Wednesday at least 10 days after the injection",
    visits: ["call"], params: {}, quote: "", page: null, output: { kind: "date" }, inputs: [],
    program: { let: { d: { op: "add_days", args: [{ var: "this.base" }, 10] } }, in: { record: {
      date: { op: "add_days", args: [{ var: "d" }, { op: "%", args: [{ op: "-", args: [3, { op: "weekday", args: [{ var: "d" }] }] }, 7] }] },
      window_after: 2 } } },
    generated: { calc_version: 1, sha256: "x", tests: [], reads: [] } });
  const r = rows(m, part({ withheld: {} }));
  // Injections on Mon 5 Jan, Tue 3 Feb, Mon 2 Mar: +10 days = Thu 15 Jan, Fri 13 Feb, Thu 12 Mar → the next Wednesdays.
  assert.deepEqual(["call#1", "call#2", "call#3"].map((k) => [r[k].planned, r[k].windowEnd]),
    [["2026-01-21", "2026-01-23"], ["2026-02-18", "2026-02-20"], ["2026-03-18", "2026-03-20"]]);
});

test("the same record always gives the same calendar, and the patient sees only dated samples", () => {
  const m = model();
  const a = computeKitSchedule(m, {}, part() as any), b = computeKitSchedule(m, {}, part() as any);
  assert.deepEqual(a, b);
  const keys = a.rows.map((x: any) => x.key);
  assert.equal(new Set(keys).size, keys.length);
  const patient = kitVisitRows({ model: m, site_answers: {}, participant: part(), workflow_version: 1, patient: { id: "p", trial_id: "t" }, trial: {} } as any)
    .map((x: any) => x.id);
  assert.ok(patient.includes("pk#1") && patient.includes("pk#2") && !patient.includes("pk#4"));
});
