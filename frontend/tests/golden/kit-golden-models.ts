// Inputs for the step-0 golden test (kit-golden.test.ts): constructed models in the shapes the other
// kit tests use, the patient records to run each model with, and the records for the three sample
// models (whose saved models the backend golden test writes to ./models/). Not a test file itself.
//
// Every record fixes `today`, so "missed" visits and the next-visit summary never depend on the day
// the test runs.

export type GoldenRecord = {
  startAnchor: string; startDate: string; answers: Record<string, string>; today: string;
  events?: { key: string; type: string; date: string; answers: Record<string, string> }[];
  actuals?: Record<string, string>; doseTimes?: Record<string, string>;
};
export type GoldenCase = { name: string; model: any; site?: Record<string, string>; records: Record<string, GoldenRecord> };

/** "2026-01-01" + n days, as YYYY-MM-DD. */
export const plus = (iso: string, n: number) => new Date(Date.parse(iso + "T00:00:00Z") + n * 864e5).toISOString().slice(0, 10);

const visit = (id: string, phase: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase, modality: "site", anchor, offset_days: offset, window_before: 0, window_after: 0,
  follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [],
  range: null, ...over,
});
const event = (id: string, label: string, over: Record<string, unknown> = {}) => ({
  id, label, repeatable: true, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [], ...over,
});
const rule = (id: string, label: string, when: Record<string, string>, over: Record<string, unknown> = {}) => ({
  id, label, when, stop_visits: [], stop_all: false, same_day: "keep", starts: null, blocks: [], allowed: null,
  effect: label, source: null, if: [], set_answers: null, pause: null, ...over,
});
const repeat = (over: Record<string, unknown>) => ({
  every_days: 0, count: null, start_n: 1, chain: false, open_ended: false, until_anchor: null, overrides: [], ...over,
});
const EMPTY = { protocol: {}, arms: [], questions: [], review_questions: [], notes: [], rules: [], events: [] };
const W3 = { window_before: 3, window_after: 3 };
const rec = (startAnchor: string, startDate: string, over: Partial<GoldenRecord> = {}): GoldenRecord =>
  ({ startAnchor, startDate, answers: {}, today: startDate, ...over });
const ev = (key: string, type: string, date: string, answers: Record<string, string> = {}) => ({ key, type, date, answers });

// ── Rules (kit-rules.test.ts): Injection 2 replaces the rest of Period 1 (SP-102-05) ──────────────
const S1 = "2026-01-01";
const inj2 = rule("inj2", "Injection 2 given", { anchor: "p2d1" }, {
  stop_visits: ["p1w8", "p1w12", "p1w16", "p1w20", "p1w24"], starts: "p2d1",
  allowed: { relative_to: "p1d1", min_days: 29, max_days: 140, not_on_visit: "p1w4" },
  effect: "Later Period 1 visits stop; Period 2 follows Injection 2.", source: "p. 23, footnote 13",
});
const sp102 = {
  ...EMPTY,
  anchors: [anchor("p1d1", "start", { label: "Period 1 Day 1", is_day1: true }), anchor("p2d1", "ask", { label: "Injection 2", set_by_visit: "p2d1v" })],
  visits: [
    visit("p1d1v", "Period 1", "p1d1", 0, W3), visit("p1w4", "Period 1", "p1d1", 28, W3), visit("p1w8", "Period 1", "p1d1", 56, W3),
    visit("p1w12", "Period 1", "p1d1", 84, W3), visit("p1w16", "Period 1", "p1d1", 112, W3), visit("p1w20", "Period 1", "p1d1", 140, W3),
    visit("p1w24", "Period 1", "p1d1", 168, W3), visit("p2d1v", "Period 2", "p2d1", 0, W3), visit("p2w2", "Period 2", "p2d1", 14, W3),
    visit("p2w4", "Period 2", "p2d1", 28, W3),
  ],
  events: [event("aesi", "AESI")],
  rules: [inj2, rule("aesi", "AESI", { event: "aesi" }, { blocks: ["inj2"], effect: "Injection 2 is no longer allowed." })],
};
const stopAll = {
  ...sp102,
  events: [
    event("uns", "Unscheduled", { visits: [visit("uns_fu", "Unscheduled", "p1d1", 30, W3)] }),
    event("wd", "Withdrawal", { repeatable: false, visits: [visit("et", "End", "p1d1", 0, W3)] }),
  ],
  rules: [rule("wd", "Withdrawal", { event: "wd" }, { stop_all: true })],
};

// ── Ending event keeps the follow-up it dates (KCP-330-008) ──────────────────────────────────────
const kcp = {
  ...EMPTY,
  anchors: [anchor("c1d1", "start", { is_day1: true }), anchor("eot", "ask"), anchor("fu", "relative", { relative_to: "eot", offset_days: 30 })],
  visits: [
    visit("c1", "Treatment", "c1d1", 0, { ...W3, repeat: repeat({ every_days: 28, count: 6 }) }),
    visit("final", "Follow-up", "eot", 0, W3), visit("safety", "Follow-up", "fu", 0, W3),
  ],
  events: [event("pd", "Progression", { repeatable: false, ends_schedule: true, sets_anchor: "eot" })],
};

// ── Rule branches (kit-rule-conditions.test.ts) ───────────────────────────────────────────────────
const S2 = "2026-01-05";
const branches = (rules: unknown[]) => ({
  ...EMPTY,
  questions: [
    { id: "arm", label: "Arm", text: "Arm?", help: "", scope: "participant", kind: "choice", options: [{ value: "A", label: "A" }, { value: "B", label: "B" }] },
    { id: "responder", label: "Responder", text: "Responded at C2?", help: "", scope: "decision", kind: "boolean", options: [], ask_at: { visit: "c2" } },
  ],
  anchors: [anchor("day1", "start", { label: "Day 1", is_day1: true }), anchor("stopped", "ask", { label: "Treatment stopped" })],
  visits: [
    visit("c1", "Treatment", "day1", 0), visit("c2", "Treatment", "day1", 28), visit("c3", "Treatment", "day1", 56),
    visit("c4", "Treatment", "day1", 84), visit("safe", "Follow-up", "stopped", 30),
    visit("ltfu", "Follow-up", "stopped", 90, { repeat: repeat({ every_days: 90, count: 2 }) }),
  ],
  events: [event("stop", "Treatment stopped", { sets_anchor: "stopped",
    questions: [{ id: "grade", label: "Grade", text: "Grade?", help: "", kind: "choice", scope: "event",
      options: ["1", "2", "3"].map((v) => ({ value: v, label: v })) }] })],
  rules,
});
const stopRule = (id: string, label: string, stop: string[], cond: unknown[]) => rule(id, label, { event: "stop" }, { stop_visits: stop, if: cond });
const byTiming = branches([
  stopRule("early", "Stopped before Cycle 3", ["c3", "c4", "ltfu"], [{ timing: { visit: "c3", min_days: null, max_days: -1 } }]),
  stopRule("late", "Stopped after Cycle 3", ["c4", "safe"], [{ timing: { visit: "c3", min_days: 0, max_days: null } }]),
]);
const byWhatWho = branches([
  stopRule("g3", "Grade 3: stop treatment", ["c3", "c4"], [{ event_question: "grade", in: ["3"] }]),
  stopRule("a", "Arm A: safety visit only", ["ltfu"], [{ question: "arm", in: ["A"] }]),
  stopRule("b", "Arm B: long-term follow-up only", ["safe"], [{ question: "arm", in: ["B"] }]),
  stopRule("second", "Second stop: end treatment", ["c4"], [{ occurrence: 2 }]),
  stopRule("nr", "Non-responder stops", ["c3", "c4"], [{ question: "responder", in: ["no"] }]),
]);
const stopOn = (n: number, answers: Record<string, string> = {}) => ev(`e${n}`, "stop", plus(S2, n), answers);

// ── Schema extensions (kit-schema-extensions.test.ts) ─────────────────────────────────────────────
const extBase = (over: Record<string, unknown> = {}) => ({
  ...EMPTY,
  questions: [
    { id: "arm", label: "Arm", text: "Arm?", help: "", kind: "choice", scope: "participant", options: [{ value: "A", label: "A" }, { value: "B", label: "B" }] },
    { id: "age", label: "Age", text: "Age?", help: "", kind: "number", scope: "participant", options: [] },
  ],
  anchors: [
    anchor("scr", "start", { label: "Screening" }),
    anchor("d1", "ask", { label: "Day 1", is_day1: true, relative_to: "scr", offset_days: 14,
      estimates: [{ when: [], relative_to: "scr", offset_days: 14 }], range: { relative_to: "scr", min_days: 1, max_days: 28 } }),
  ],
  visits: [visit("screen", "Treatment", "scr", 0), visit("c1", "Treatment", "d1", 0)],
  ...over,
});
const extTiming = extBase({ visits: [
  visit("screen", "Screening", "scr", 0, { window_after: 3, procedures: [
    { name: "Geriatric scale", if: [{ question: "age", op: ">=", value: 65 }] },
    { name: "PK", if: [{ any: [{ question: "arm", in: ["A"] }, { not: { question: "age", op: "<", value: 18 } }] }] },
    { name: "ECG", if: [], need: "if_indicated" }, { name: "Labs", if: [], reuse_within_days: 3 },
  ], prep: { fasting_hours: 8, notes: ["Hold metformin"] } }),
  visit("c1", "Treatment", "d1", 0),
  visit("elder", "Treatment", "scr", 7, { include_if: [{ question: "age", op: ">", value: 64 }] }),
  visit("scan", "Imaging", "scr", 0, { repeat: repeat({ every_days: 56, stages: [{ every_days: 56, until_day: 112 }, { every_days: 84, until_day: null }], max_days: 300 }) }),
  visit("fu", "Follow-up", "scr", 0, { repeat: repeat({ every_days: 30 }) }),
  visit("cyc", "Treatment", "scr", 0, { repeat: repeat({ every_days: 21, count: 3, chain: true, max_delay_days: 7, delay_effect: "Discontinue treatment." }) }),
  visit("ctc", "Treatment", "d1", 0, { repeat: repeat({ every_days: 21, count: 9,
    overrides: [{ n: [], every: 3, from: 3, to: null, add: [{ name: "CT", if: [] }], remove: [], note: null }] }) }),
  visit("dose", "Treatment", "scr", 0, { repeat: repeat({ every_days: 21, count: 2, chain: true }), min_gap: { days: 14, after: null } }),
], events: [event("ae", "Adverse event", { visits: [
  visit("aefu", "Event", "scr", 7, { repeat: repeat({ every_days: 7, count: 4, stages: [{ every_days: 7, until_day: 14 }, { every_days: 28, until_day: null }] }) }),
] })] });
const crossover = extBase({
  events: [event("pd", "Progression", { repeatable: false })],
  rules: [rule("x", "Crossover", { event: "pd" }, { effect: "", set_answers: { arm: "B" } })],
  visits: [visit("screen", "Treatment", "scr", 0),
    visit("a", "Treatment", "scr", 0, { include_if: [{ question: "arm", in: ["A"] }], repeat: repeat({ every_days: 14, count: 4 }) }),
    visit("b", "Treatment", "scr", 0, { include_if: [{ question: "arm", in: ["B"] }], repeat: repeat({ every_days: 14, count: 4 }) })],
});
const pause = (mode: string) => extBase({
  anchors: [...extBase().anchors as unknown[], anchor("res", "ask", { label: "Resumed" })],
  events: [event("hold", "Held", { repeatable: false })],
  rules: [rule("h", "Treatment held", { event: "hold" }, { effect: "", pause: { visits: ["cyc"], resume: "res", mode } })],
  visits: [visit("screen", "Treatment", "scr", 0), visit("cyc", "Treatment", "scr", 0, { repeat: repeat({ every_days: 14, count: 4 }) })],
});
const facts = extBase({
  events: [event("pd", "Progression", { repeatable: false })],
  visits: [
    visit("screen", "Treatment", "scr", 0, { window_after: 3 }),
    visit("call", "Treatment", "scr", 0, { repeat: repeat({ every_days: 30, count: 4 }), include_if: [{ event: "pd", happened: true }] }),
    visit("scan", "Treatment", "scr", 0, { repeat: repeat({ every_days: 30, count: 4 }), include_if: [{ event: "pd", happened: false }] }),
    visit("retest", "Treatment", "scr", 10, { include_if: [{ visit: "screen", n: null, status: "missed" }] }),
    visit("review", "Treatment", "scr", 20, { procedures: [{ name: "Extra ECG", if: [{ visit: "screen", n: null, status: "done" }] }] }),
  ],
});

// ── Entered vs counted dates (kit-participant.test.ts) ────────────────────────────────────────────
const S3 = "2026-11-02";
const screenRand = {
  ...EMPTY,
  anchors: [
    anchor("screening", "start", { label: "Screening" }), anchor("surgery", "ask", { label: "Surgery" }),
    anchor("rand", "relative", { label: "Randomisation", relative_to: "screening", offset_days: 28, is_day1: true }),
    anchor("pd", "ask", { label: "Progression" }),
  ],
  visits: [visit("scr", "Screening", "screening", 0), visit("d1", "Treatment", "rand", 0), visit("w2", "Treatment", "rand", 14),
    visit("postop", "Surgery", "surgery", 30), visit("pdfu", "Follow-up", "pd", 28)],
  events: [event("prog", "Progression", { repeatable: false, sets_anchor: "pd" })],
};

export const GOLDEN_CASES: GoldenCase[] = [
  { name: "test-rules-sp102", model: sp102, records: {
    none: rec("p1d1", S1, { today: plus(S1, 30) }),
    "inj2-day60": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 60) }, today: plus(S1, 70) }),
    "inj2-on-w8-day": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 56) }, today: plus(S1, 70) }),
    "inj2-by-visit-date": rec("p1d1", S1, { actuals: { "p2d1v#1": plus(S1, 100) }, today: plus(S1, 110) }),
    "inj2-too-early": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 20) }, today: plus(S1, 30) }),
    "inj2-on-w4-day": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 28) }, today: plus(S1, 30) }),
    "inj2-before-start": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, -10) } }),
    "aesi-then-inj2": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 60) }, events: [ev("e1", "aesi", plus(S1, 40))], today: plus(S1, 70) }),
    "w12-done-anyway": rec("p1d1", S1, { answers: { "@p2d1": plus(S1, 60) }, actuals: { "p1w12#1": plus(S1, 85) }, today: plus(S1, 90) }),
  } },
  { name: "test-rules-stop-all", model: stopAll, records: {
    "unscheduled-only": rec("p1d1", S1, { events: [ev("u", "uns", plus(S1, 10))], today: plus(S1, 20) }),
    "unscheduled-then-withdrawal": rec("p1d1", S1, { events: [ev("u", "uns", plus(S1, 10)), ev("w", "wd", plus(S1, 30))], today: plus(S1, 40) }),
  } },
  { name: "test-rules-kcp", model: kcp, records: {
    none: rec("c1d1", S1),
    "progression-day70": rec("c1d1", S1, { events: [ev("e", "pd", plus(S1, 70))], today: plus(S1, 75) }),
  } },
  { name: "test-branches-timing", model: byTiming, records: {
    none: rec("day1", S2, { answers: { arm: "A" } }),
    "stop-day41": rec("day1", S2, { answers: { arm: "A" }, events: [stopOn(41)], today: plus(S2, 45) }),
    "stop-day74": rec("day1", S2, { answers: { arm: "A" }, events: [stopOn(74)], today: plus(S2, 80) }),
    "c3-late-stop-day65": rec("day1", S2, { answers: { arm: "A" }, actuals: { "c3#1": plus(S2, 70) }, events: [stopOn(65)], today: plus(S2, 75) }),
  } },
  { name: "test-branches-what-who", model: byWhatWho, records: {
    "grade1-arm-a": rec("day1", S2, { answers: { arm: "A" }, events: [stopOn(41, { grade: "1" })], today: plus(S2, 45) }),
    "grade3-arm-b-responder-no": rec("day1", S2, { answers: { arm: "B", responder: "no" }, events: [stopOn(41, { grade: "3" })], today: plus(S2, 45) }),
    "two-stops": rec("day1", S2, { answers: { arm: "A", responder: "yes" }, events: [stopOn(20, { grade: "1" }), stopOn(60, { grade: "3" })], today: plus(S2, 65) }),
  } },
  { name: "test-ext-timing", model: extTiming, records: {
    none: rec("scr", S2),
    "older-arm-b": rec("scr", S2, { answers: { age: "70", arm: "B", "@d1": "2026-01-20" }, today: "2026-02-01" }),
    "child-arm-a-day1-late": rec("scr", S2, { answers: { age: "12", arm: "A", "@d1": "2026-02-10" }, today: "2026-02-01" }),
    recorded: rec("scr", S2, { answers: { age: "40", arm: "A", "@d1": "2026-01-20" },
      actuals: { "screen#1": S2, "scan#1": "2026-01-07", "cyc#1": S2, "cyc#2": "2026-02-06", "dose#1": S2, "dose#2": "2026-01-15" },
      events: [ev("a1", "ae", "2026-01-12")], today: "2026-02-10" }),
  } },
  { name: "test-ext-crossover", model: crossover, records: {
    "arm-a": rec("scr", S2, { answers: { arm: "A", age: "50" } }),
    "arm-a-progressed": rec("scr", S2, { answers: { arm: "A", age: "50" }, events: [ev("e1", "pd", "2026-01-25")], today: "2026-02-01" }),
  } },
  { name: "test-ext-pause-shift", model: pause("shift"), records: {
    "not-held": rec("scr", S2, { answers: { arm: "A", age: "50" } }),
    held: rec("scr", S2, { answers: { arm: "A", age: "50" }, events: [ev("e1", "hold", "2026-01-10")], today: "2026-01-20" }),
    "held-and-resumed": rec("scr", S2, { answers: { arm: "A", age: "50", "@res": "2026-01-31" }, events: [ev("e1", "hold", "2026-01-10")], today: "2026-02-05" }),
  } },
  { name: "test-ext-pause-skip", model: pause("skip"), records: {
    "held-and-resumed": rec("scr", S2, { answers: { arm: "A", age: "50", "@res": "2026-01-31" }, events: [ev("e1", "hold", "2026-01-10")], today: "2026-02-05" }),
  } },
  { name: "test-ext-facts", model: facts, records: {
    "screening-window-open": rec("scr", S2, { answers: { arm: "A", age: "50" }, today: "2026-01-07" }),
    "screening-window-closes-today": rec("scr", S2, { answers: { arm: "A", age: "50" }, today: "2026-01-08" }),
    "screening-missed": rec("scr", S2, { answers: { arm: "A", age: "50" }, today: "2026-01-12" }),
    "screening-done": rec("scr", S2, { answers: { arm: "A", age: "50" }, actuals: { "screen#1": S2 }, today: "2026-01-12" }),
    progressed: rec("scr", S2, { answers: { arm: "A", age: "50" }, actuals: { "screen#1": S2 }, events: [ev("e1", "pd", "2026-02-20")], today: "2026-03-01" }),
  } },
  { name: "test-participant-screen-rand", model: screenRand, records: {
    counted: rec("screening", S3),
    "rand-and-surgery-entered": rec("screening", S3, { answers: { "@rand": "2026-11-12", "@surgery": "2026-12-20" }, today: "2026-12-01" }),
    progressed: rec("screening", S3, { answers: { "@rand": "2026-11-12" }, events: [ev("p", "prog", "2027-01-15")], today: "2027-01-20" }),
  } },
];

// ── Records for the sample models (backend/app/kit/samples, saved as the backend saves them) ──────
const S4 = "2026-11-02";
const s = (over: Partial<GoldenRecord> = {}) => rec("screening", S4, over);

export const SAMPLE_RECORDS: Record<string, Record<string, GoldenRecord>> = {
  // Run-in, crossover periods (Period 2 entered within 42-49 days), extension decision, events.
  arc: {
    empty: s(),
    answered: s({ answers: { seq: "AB", wocbp: "yes", runin_ok: "yes", ole_yes: "yes", "@p2": "2027-01-08" }, today: "2026-11-20" }),
    "runin-failed": s({ answers: { seq: "BA", wocbp: "no", runin_ok: "no" }, today: "2026-11-25" }),
    recorded: s({ answers: { seq: "BA", wocbp: "no", runin_ok: "yes", ole_yes: "no" },
      actuals: { "scr#1": "2026-11-02", "run#1": "2026-11-10", "p1d1#1": "2026-11-25", "p1d14#1": "2026-12-08" },
      doseTimes: { "p1d1#1": "09:15" }, today: "2026-12-20" }),
    events: s({ answers: { seq: "AB", wocbp: "yes", runin_ok: "yes", ole_yes: "yes" },
      actuals: { "p1d1#1": "2026-11-23" },
      events: [ev("e1", "exac", "2026-12-01", { ocs: "yes" }), ev("e2", "exac", "2026-12-20", { ocs: "no" }), ev("e3", "wd", "2027-01-05")],
      today: "2027-01-10" }),
  },
  // Day 1 entered within 7-21 days, cycles chained, 8-weekly scans until progression, PK time points.
  helix: {
    empty: s(),
    answered: s({ answers: { part: "A", dl: "2", wocbp: "yes", biopsy: "yes", "@day1": "2026-11-16" }, today: "2026-11-16" }),
    recorded: s({ answers: { part: "B", dl: "1", wocbp: "no" },
      actuals: { "c1d1#1": "2026-11-18", "c1d8#1": "2026-11-25", "cnd1#2": "2026-12-12", "cnd15#2": "2026-12-26", "cnd1#3": "2027-01-12", "ta#1": "2027-01-13" },
      doseTimes: { "c1d1#1": "08:30" }, today: "2027-01-20" }),
    "dlt-and-progression": s({ answers: { part: "A", dl: "3", wocbp: "no", biopsy: "no" }, actuals: { "c1d1#1": "2026-11-16" },
      events: [ev("d1", "dlt", "2026-11-30"), ev("u1", "uns", "2027-01-05"), ev("p1", "pd", "2027-02-10", { pd_conf: "no" })],
      today: "2027-02-15" }),
    "stopped-for-toxicity": s({ answers: { part: "B", dl: "1", wocbp: "no" }, actuals: { "c1d1#1": "2026-11-16" },
      events: [ev("t1", "tox", "2027-01-20")], today: "2027-01-25" }),
  },
  // Two arms, surgery date entered, adjuvant start entered within 28-56 days of surgery.
  orion: {
    empty: s(),
    "arm-a": s({ answers: { arm: "A", wocbp: "yes", pk: "yes" } }),
    "arm-b-surgery": s({ answers: { arm: "B", wocbp: "no", pk: "no", "@surgery": "2026-12-10" }, today: "2026-12-01" }),
    "arm-a-recorded": s({ answers: { arm: "A", wocbp: "no", pk: "yes", "@surgery": "2027-02-01" },
      actuals: { "rand#1": "2026-11-13", "neo#1": "2026-11-13", "neo#2": "2026-11-30", "adja#1": "2027-03-15" }, today: "2027-04-01" }),
    "arm-a-discontinued": s({ answers: { arm: "A", wocbp: "yes", pk: "no" },
      events: [ev("u1", "uns", "2026-12-01"), ev("e1", "et", "2027-01-10", { et_reason: "ae" })], today: "2027-01-15" }),
    "arm-b-consent-withdrawn": s({ answers: { arm: "B", wocbp: "no", pk: "no" },
      events: [ev("e1", "et", "2026-12-15", { et_reason: "consent" })], today: "2026-12-20" }),
  },
};
