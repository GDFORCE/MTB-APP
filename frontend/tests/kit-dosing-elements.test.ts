// Elements added 30 Oct 2026 from CG03-EOC (weekly chemo dosing tables, rechecks until recovery,
// dose levels that omit Day 8, "lasting 7 days", per-test time limits), CRD/09 (24 h samples on
// the next day) and BP11-301 ("stopped before Week 12", "or as soon as possible"). Constructed
// models; every date worked out by hand. Day 1 = 19 Jan 2026 (screening 5 Jan + 14).
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";

const visit = (id: string, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor: "day1", offset_days: 0, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchors = [
  { id: "scr", label: "Screening", kind: "start", relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null },
  { id: "day1", label: "Day 1", kind: "relative", relative_to: "scr", offset_days: 14, is_day1: true, day_zero: false, set_by_visit: null, estimates: [], range: null },
];
const base = (over: Record<string, unknown>) => ({
  protocol: {}, arms: [], review_questions: [], notes: [], questions: [], rules: [], events: [], anchors,
  measures: [{ id: "anc", label: "ANC", unit: "/mcL", kind: "value", worse: "lower", baseline: null },
    { id: "crcl", label: "CrCl", unit: "ml/min", kind: "value", worse: "lower", baseline: null }],
  ...over,
}) as any;
const lim = (measure: string, op: string, value: number, extra = {}) => ({ measure, op, value, of: "latest", ...extra });
const run = (model: any, rec: Record<string, unknown> = {}) =>
  computeKitSchedule(model, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: {}, today: "2026-01-06", ...rec } as any);
const row = (s: any, key: string) => s.rows.find((r: any) => r.type === "visit" && r.key === key);
const keys = (s: any, id: string) => s.rows.filter((r: any) => r.type === "visit" && r.visitId === id).map((r: any) => r.key);

// Weekly-type cycle: Day 1 every 21 days, Day 8 with a dosing table (CG03-EOC Table 8.1.1.6 A).
const TABLE = [
  { do: "give", if: [lim("anc", ">=", 1500)], note: null },
  { do: "reduce", if: [lim("anc", ">=", 1000)], percent: 50, dose: "gem", note: null },
  { do: "skip", if: [lim("anc", "<", 1000)], note: null },
];
const GEM = { id: "gem", label: "Gemcitabine", start: "1000", visits: ["d1", "d8"], below_lowest: "stop", stop_visits: [], source: null, levels: [
  { id: "1000", label: "1000 mg/m2" }, { id: "800", label: "800 mg/m2" }, { id: "800d1", label: "800 mg/m2 Day 1 only", drop_visits: ["d8"] }] };
const check = (over = {}) => ({ id: "d8_check", text: "ANC for Day 8?", help: null, recheck_days: null, max_wait_days: null, on_timeout: "warn",
  stop_visits: [], source: null, measures: ["anc"], limits: [], dose_changes: [], ...over });
const CYCLE = base({
  doses: [GEM],
  visits: [
    visit("d1", { repeat: { every_days: 21, chain: true, count: 4 }, procedures: [
      { name: "Pregnancy test", if: [], within_before: { days: 3 } }, { name: "Labs", if: [], within_before: { hours: 72 } }, { name: "Vitals", if: [] }] }),
    visit("d8", { follows: "d1", offset_days: 7, repeat: { every_days: 21, chain: true, count: 4 }, check: check({ actions: TABLE }) }),
  ],
});
const on = (key: string, ...entries: Record<string, unknown>[]) => ({ checks: { [key]: entries } });

test("the dosing table decides each Day 8 dose from the value entered", () => {
  const skip = run(CYCLE, on("d8#1", { date: "2026-01-26", values: { anc: 800 } }));
  assert.equal(row(skip, "d8#1").status, "skipped");
  assert.equal(row(skip, "d8#1").date, "2026-01-26", "keeps its planned date");
  assert.ok(row(skip, "d8#1").notes.includes("Skipped on 26 Jan (ANC < 1000 /mcL → skip (not made up)): not made up; later dates stay."));
  assert.equal(row(skip, "d8#1").check.answers[0].outcome, "skip");
  assert.equal(row(skip, "d1#2").date, "2026-02-09", "the next cycle does not move");
  assert.deepEqual(row(skip, "d8#1").doses, [], "a skipped dose is not a dose");

  const half = run(CYCLE, on("d8#1", { date: "2026-01-26", values: { anc: 1200 } }));
  assert.equal(row(half, "d8#1").check.state, "ok");
  assert.deepEqual(row(half, "d8#1").check.reduce, { percent: 50, dose: "gem", why: "ANC ≥ 1000 /mcL → give 50% of the dose this time" });
  assert.equal(row(half, "d8#1").doses[0].percent, 50);
  assert.ok(row(half, "d8#1").notes.some((n: string) => n.startsWith("This time only: 50% of the dose")));
  assert.deepEqual([row(half, "d8#2").doses[0].level, row(half, "d8#2").doses[0].percent], ["1000", null], "later doses keep their level");

  const full = run(CYCLE, on("d8#1", { date: "2026-01-26", values: { anc: 1600 } }));
  assert.deepEqual([row(full, "d8#1").status, row(full, "d8#1").check.state, row(full, "d8#1").check.reduce], ["scheduled", "ok", null]);
});

test("staff may choose a row by hand, and a hold row waits for recovery", () => {
  const s = run(CYCLE, on("d8#2", { date: "2026-02-16", answer: "skip" }));
  assert.equal(row(s, "d8#2").status, "skipped");
  assert.equal(row(s, "d8#2").check.skip.why, "ANC < 1000 /mcL → skip (not made up)");
  const hold = base({ doses: [GEM], visits: [CYCLE.visits[0], visit("d8", { follows: "d1", offset_days: 7, repeat: { every_days: 21, chain: true, count: 4 }, check: check({ actions: [
    { do: "give", if: [lim("anc", ">=", 1500)], note: null }, { do: "hold", if: [lim("anc", "<", 1500)], note: null }] }) })] });
  const held = run(hold, on("d8#1", { date: "2026-01-26", values: { anc: 900 } }));
  assert.equal(row(held, "d8#1").check.state, "held");
  const back = run(hold, on("d8#1", { date: "2026-01-26", values: { anc: 900 } }, { date: "2026-01-28", values: { anc: 1600 } }));
  assert.deepEqual([row(back, "d8#1").check.state, row(back, "d8#1").date], ["resumed", "2026-01-28"]);
});

test("a dose level that omits Day 8 removes the later Day 8 visits", () => {
  const s = run(CYCLE, { doseLevels: { "d1#2": { gem: "800d1" } } });
  assert.deepEqual(keys(s, "d8"), ["d8#1"]);
  const group = s.stoppedBy.find((g: any) => g.rule === "dose:gem:800d1");
  assert.deepEqual(group.visits.map((v: any) => v.key), ["d8#2", "d8#3", "d8#4"]);
  assert.equal(group.label, "Gemcitabine 800 mg/m2 Day 1 only");
  // A Day 8 staff recorded is kept.
  assert.deepEqual(keys(run(CYCLE, { doseLevels: { "d1#2": { gem: "800d1" } }, actuals: { "d8#2": "2026-02-16" } }), "d8"), ["d8#1", "d8#2"]);
});

test("“lasting 7 days or more” counts an unbroken run of the values entered for the visit", () => {
  const model = base({ doses: [GEM], visits: [
    visit("d1", { repeat: { every_days: 21, chain: true, count: 3 }, check: check({ id: "d1_check", text: "ANC ≥ 1500?", limits: [lim("anc", ">=", 1500)],
      dose_changes: [{ dose: "gem", step: -1, to: null, stop: false, if: [lim("anc", "<", 500, { for_days: 7 })], occurrence: null, and_later: false, note: "Grade 4 neutropenia ≥ 7 days" }] }) }),
    visit("d8", { follows: "d1", offset_days: 7 })] });
  const long = run(model, on("d1#2", { date: "2026-02-09", values: { anc: 400 } }, { date: "2026-02-16", values: { anc: 450 } }, { date: "2026-02-17", values: { anc: 1600 } }));
  assert.equal(row(long, "d1#2").doses[0].level, "800");
  assert.equal(row(long, "d1#2").doses[0].changed, "Grade 4 neutropenia ≥ 7 days");
  const short = run(model, on("d1#2", { date: "2026-02-09", values: { anc: 400 } }, { date: "2026-02-12", values: { anc: 450 } }, { date: "2026-02-13", values: { anc: 1600 } }));
  assert.equal(row(short, "d1#2").doses[0].level, "1000");
  const broken = run(model, on("d1#2", { date: "2026-02-09", values: { anc: 400 } }, { date: "2026-02-12", values: { anc: 900 } }, { date: "2026-02-16", values: { anc: 450 } }, { date: "2026-02-17", values: { anc: 1600 } }));
  assert.equal(row(broken, "d1#2").doses[0].level, "1000", "a value in between that did not meet it breaks the run");
});

// Cisplatin held for CrCl < 50: CrCl weekly until ≥ 50; still below after 4 weeks stops cisplatin (CG03-EOC 8.2.6).
const RECHECK = base({
  visits: [visit("cis", { repeat: { every_days: 21, chain: true, count: 6 } }),
    visit("fu", { offset_days: 2, repeat: { every_days: 7, until_check: true }, check: check({ id: "fu_check", text: "Resolved?", measures: [] }) })],
  events: [{ id: "hold", label: "Cisplatin held", repeatable: true, ends_schedule: false, note: null, questions: [], visits: [
    visit("crcl_re", { offset_days: 7, repeat: { every_days: 7, until_check: true }, check: check({ id: "crcl", text: "CrCl ≥ 50?", measures: ["crcl"],
      limits: [lim("crcl", ">=", 50)], max_wait_days: 28, on_timeout: "stop", stop_visits: ["cis"] }) })] }],
});
const HELD = [{ key: "h1", type: "hold", date: "2026-02-01", answers: {} }];

test("a recheck comes back every week until it is met", () => {
  const open = run(RECHECK, { events: HELD, today: "2026-02-02" });
  assert.deepEqual(keys(open, "crcl_re").length, 6);
  assert.equal(row(open, "ev.h1.crcl_re#1").date, "2026-02-08");
  assert.equal(row(open, "ev.h1.crcl_re#1").notes.some((n: string) => n.includes("doesn't say how many")), false);
  const met = run(RECHECK, { events: HELD, today: "2026-02-16", checks: {
    "ev.h1.crcl_re#1": [{ date: "2026-02-08", values: { crcl: 40 } }], "ev.h1.crcl_re#2": [{ date: "2026-02-15", values: { crcl: 55 } }] } });
  assert.deepEqual(keys(met, "crcl_re"), ["ev.h1.crcl_re#1", "ev.h1.crcl_re#2"]);
  assert.deepEqual([row(met, "ev.h1.crcl_re#1").check.state, row(met, "ev.h1.crcl_re#2").check.state], ["again", "met"]);
  assert.ok(row(met, "ev.h1.crcl_re#2").notes.includes("“CrCl ≥ 50?” met: this is the last one."));
  assert.deepEqual(met.warnings, []);
  // A scheduled repeat-until visit answered by hand.
  const fu = run(RECHECK, { checks: { "fu#1": [{ date: "2026-01-21", answer: "no" }], "fu#2": [{ date: "2026-01-28", answer: "yes" }] } });
  assert.deepEqual(keys(fu, "fu"), ["fu#1", "fu#2"]);
});

test("not met within 4 weeks of the first recheck stops cisplatin from then", () => {
  const low = { crcl: 40 };
  const s = run(RECHECK, { events: HELD, today: "2026-03-20", checks: Object.fromEntries([1, 2, 3, 4].map((n) =>
    [`ev.h1.crcl_re#${n}`, [{ date: ["2026-02-08", "2026-02-15", "2026-02-22", "2026-03-01"][n - 1], values: low }]])) });
  assert.ok(s.warnings.includes("CRCL_RE: “CrCl ≥ 50?” not met within 28 days of the first one (by 08 Mar). The protocol stops these visits from then."));
  assert.deepEqual(keys(s, "cis"), ["cis#1", "cis#2", "cis#3"], "cycles after 8 Mar stop (cis#4 was 23 Mar)");
  assert.deepEqual(keys(s, "crcl_re"), ["ev.h1.crcl_re#1", "ev.h1.crcl_re#2", "ev.h1.crcl_re#3", "ev.h1.crcl_re#4", "ev.h1.crcl_re#5"]);
});

test("an assessment can depend on an event logged before Week 12", () => {
  const model = base({
    visits: [visit("w12", { offset_days: 83 }), visit("w24", { offset_days: 167, procedures: [
      { name: "Diary", if: [{ not: { event: "stop", happened: true, before: { visit: "w12", n: null } } }] }, { name: "Vitals", if: [] }] })],
    events: [{ id: "stop", label: "Treatment stopped", repeatable: false, ends_schedule: false, note: null, questions: [], visits: [] }],
  });
  const names = (s: any) => row(s, "w24#1").procedures.map((p: any) => p.name);
  assert.deepEqual(names(run(model)), ["Diary", "Vitals"]);
  assert.deepEqual(names(run(model, { events: [{ key: "e", type: "stop", date: "2026-03-01", answers: {} }] })), ["Vitals"], "stopped before Week 12 (12 Apr)");
  assert.deepEqual(names(run(model, { events: [{ key: "e", type: "stop", date: "2026-05-01", answers: {} }] })), ["Diary", "Vitals"]);
});

test("an early-termination visit whose date has passed is due as soon as possible", () => {
  const model = (asap: boolean) => base({
    anchors: [...anchors, { id: "last", label: "Last dose", kind: "last_visit", last_of: ["dose"], relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null }],
    visits: [visit("dose", { repeat: { every_days: 28, chain: true, count: 3 } }), visit("et", { anchor: "last", offset_days: 28, phase: "Follow-up", ...(asap ? { asap_if_past: true } : {}) })],
    events: [{ id: "wd", label: "Withdrawal", repeatable: false, ends_schedule: true, keeps_visits: ["et"], note: null, questions: [], visits: [] }],
  });
  const given = { actuals: { "dose#1": "2026-01-19", "dose#2": "2026-02-16", "dose#3": "2026-03-16" } };
  const left = { ...given, events: [{ key: "w", type: "wd", date: "2026-05-20", answers: {} }] };
  assert.equal(row(run(model(true), given), "et#1").date, "2026-04-13");
  const late = row(run(model(true), left), "et#1");
  assert.equal(late.date, "2026-05-20");
  assert.ok(late.notes.includes("Planned 13 Apr, which had passed when participation ended (20 May): due as soon as possible."));
  assert.equal(row(run(model(false), left), "et#1").date, "2026-04-13", "without the flag the date stays");
});

test("a test's own time limit and samples on the next day get their dates", () => {
  const s = run(CYCLE);
  assert.deepEqual(row(s, "d1#1").procedures.map((p: any) => p.withinBefore ?? null),
    [{ days: 3, from: "2026-01-16" }, { hours: 72, from: "2026-01-16" }, null]);
  const pk = base({ visits: [visit("d1", { timepoints: [
    { label: "Pre-dose", offset_min: 0, window_before_min: 30, window_after_min: 0, procedures: [] },
    { label: "24 h", offset_min: 1440, window_before_min: 0, window_after_min: 2, procedures: [] }] })] });
  const tp = row(run(pk, { doseTimes: { "d1#1": "09:00" } }), "d1#1").timepoints;
  assert.deepEqual([tp[0].date, tp[1].date, tp[1].at], [undefined, "2026-01-20", "09:00 (+1 d)"]);
});

test("the patient timeline shows a skipped dose as struck through, and the next visit after it", async () => {
  const { patientTimeline } = await import("../src/features/kit/patientSections.ts");
  const part = { startAnchor: "scr", startDate: "2026-01-05", answers: {}, ...on("d8#1", { date: "2026-01-26", values: { anc: 800 } }) };
  const current = run(CYCLE, part);
  const t = patientTimeline({ model: CYCLE, participant: part as any, current, withoutEvents: current, baseline: run(CYCLE),
    today: "2026-01-27", conditionOf: () => null });
  const d8 = t.sections.flatMap((s: any) => s.rows).find((r: any) => r.key === "d8#1");
  assert.deepEqual([d8.dot, d8.meta], ["struck", "Skipped 26 Jan · not made up"]);
  assert.equal(t.next?.key, "d1#2");
});
