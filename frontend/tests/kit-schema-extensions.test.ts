// Protocol fields beyond the kit's original schema, as the engine applies them: date ranges,
// interval stages, time limits, unstated counts, delay limits, richer conditions, every-nth
// assessments, minimum gaps, crossover answers and pauses. Constructed models, expected dates
// worked out by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { anchorRangeWarnings, enrolmentAnchors, rangeText } from "../src/features/kit/participant.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const base = (over: Record<string, unknown> = {}) => ({
  protocol: {}, arms: [], review_questions: [], notes: [], rules: [], events: [],
  questions: [
    { id: "arm", label: "Arm", text: "Arm?", help: "", kind: "choice", scope: "participant", options: [{ value: "A", label: "A" }, { value: "B", label: "B" }] },
    { id: "age", label: "Age", text: "Age?", help: "", kind: "number", scope: "participant", options: [] },
  ],
  anchors: [
    anchor("scr", "start", { label: "Screening" }),
    anchor("d1", "ask", { label: "Day 1", is_day1: true, relative_to: "scr", offset_days: 14,
      estimates: [{ when: [], relative_to: "scr", offset_days: 14 }], range: { relative_to: "scr", min_days: 1, max_days: 28 } }),
  ],
  visits: [visit("screen", "scr", 0), visit("c1", "d1", 0)],
  ...over,
}) as any;
const START = "2026-01-05";
const run = (m: any, answers: Record<string, string> = {}, over: Record<string, unknown> = {}) =>
  computeKitSchedule(m, {}, { startAnchor: "scr", startDate: START, answers, ...over } as any);
const visits = (s: any) => s.rows.filter((r: any) => r.type === "visit");
const dates = (s: any, id: string) => visits(s).filter((r: any) => r.visitId === id).map((r: any) => r.date);

test("Day 1 with a range waits for its entered date and warns when it falls outside the range", () => {
  const m = base();
  assert.deepEqual(dates(run(m), "c1"), [null]);
  assert.deepEqual(dates(run(m, { "@d1": "2026-01-20" }), "c1"), ["2026-01-20"]);
  assert.equal(run(m, { "@d1": "2026-01-20" }).warnings.length, 0);
  assert.match(run(m, { "@d1": "2026-02-10" }).warnings[0], /Day 1 is 36 days after Screening; the protocol allows 1 to 28 days/);
  assert.equal(rangeText(m, m.anchors[1]), "The protocol allows 1 to 28 days after Screening.");
  assert.match(anchorRangeWarnings(m, "scr", START, { "@d1": "2026-02-10" }).d1, /36 days after Screening/);
  assert.deepEqual(enrolmentAnchors(m).map((a) => a.id), ["d1"]);
});

test("an interval that changes over time and a time limit", () => {
  const m = base({ visits: [visit("screen", "scr", 0), visit("scan", "scr", 0, {
    repeat: { every_days: 56, stages: [{ every_days: 56, until_day: 112 }, { every_days: 84, until_day: null }], max_days: 300, count: null },
  })] });
  // 0, 56, 112 (two 8-week gaps), then 12-weekly: 196, 280; 364 is past the 300-day limit.
  assert.deepEqual(dates(run(m), "scan"), ["2026-01-05", "2026-03-02", "2026-04-27", "2026-07-20", "2026-10-12"]);
});

test("a repeat with no stated count shows the next ones, flagged, instead of a single made-up visit", () => {
  const m = base({ visits: [visit("screen", "scr", 0), visit("fu", "scr", 0, { repeat: { every_days: 30, count: null } })] });
  const rows = visits(run(m)).filter((r: any) => r.visitId === "fu");
  assert.equal(rows.length, 6);
  assert.match(rows[5].notes.join(" "), /doesn't say how many/);
  assert.equal(rows[0].notes.length, 0);
});

test("a cycle delayed beyond what the protocol allows is warned about", () => {
  const m = base({ visits: [visit("screen", "scr", 0), visit("cyc", "scr", 0, {
    repeat: { every_days: 21, count: 3, chain: true, max_delay_days: 7, delay_effect: "Discontinue treatment." } })] });
  const ok = run(m, {}, { actuals: { "cyc#1": START, "cyc#2": "2026-01-31" } });
  assert.equal(ok.warnings.length, 0);
  const late = run(m, {}, { actuals: { "cyc#1": START, "cyc#2": "2026-02-06" } });
  assert.deepEqual(late.warnings, ["CYC·2 was 11 days later than planned; the protocol allows up to 7 days. Discontinue treatment."]);
});

test("comparisons, or and not in visit and assessment conditions", () => {
  const m = base({ visits: [visit("screen", "scr", 0, { procedures: [
    { name: "Geriatric scale", if: [{ question: "age", op: ">=", value: 65 }] },
    { name: "PK", if: [{ any: [{ question: "arm", in: ["A"] }, { not: { question: "age", op: "<", value: 18 } }] }] },
  ] }), visit("elder", "scr", 7, { include_if: [{ question: "age", op: ">", value: 64 }] })] });
  const procs = (s: any) => visits(s).find((r: any) => r.visitId === "screen").procedures.map((p: any) => `${p.name}${p.conditional ? "?" : ""}`);
  assert.deepEqual(procs(run(m, { age: "70", arm: "B" })), ["Geriatric scale", "PK"]);
  assert.deepEqual(procs(run(m, { age: "12", arm: "B" })), []);
  assert.deepEqual(procs(run(m, { age: "12", arm: "A" })), ["PK"]);
  assert.deepEqual(procs(run(m, { arm: "B" })), ["Geriatric scale?", "PK?"]);
  assert.deepEqual(dates(run(m, { age: "70" }), "elder"), ["2026-01-12"]);
  assert.deepEqual(dates(run(m, { age: "40" }), "elder"), []);
  assert.equal(run(m, { age: "40" }).skipped[0].onlyIf, "Age > 64");
});

test("an assessment every 3rd cycle from cycle 3, open-ended", () => {
  const m = base({ visits: [visit("screen", "scr", 0), visit("cyc", "scr", 0, {
    repeat: { every_days: 21, count: 9, overrides: [{ n: [], every: 3, from: 3, to: null, add: [{ name: "CT", if: [] }], remove: [], note: null }] } })] });
  const withCt = visits(run(m)).filter((r: any) => r.procedures.some((p: any) => p.name === "CT")).map((r: any) => r.code);
  assert.deepEqual(withCt, ["CYC·3", "CYC·6", "CYC·9"]);
});

test("a dose given too soon after the previous one is warned about", () => {
  const m = base({ visits: [visit("screen", "scr", 0), visit("dose", "scr", 0, { repeat: { every_days: 21, count: 2, chain: true }, min_gap: { days: 14, after: null } })] });
  assert.match(run(m, {}, { actuals: { "dose#1": START, "dose#2": "2026-01-15" } }).warnings[0], /10 days after DOSE·1; the protocol requires at least 14 days/);
});

test("crossover: visits for the new arm start from the crossover date", () => {
  const m = base({
    events: [{ id: "pd", label: "Progression", repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [] }],
    rules: [{ id: "x", label: "Crossover", when: { event: "pd" }, stop_visits: [], stop_all: false, same_day: "keep", starts: null, blocks: [],
      allowed: null, effect: "", source: null, if: [], set_answers: { arm: "B" }, pause: null }],
    visits: [visit("screen", "scr", 0), visit("a", "scr", 0, { include_if: [{ question: "arm", in: ["A"] }], repeat: { every_days: 14, count: 4 } }),
      visit("b", "scr", 0, { include_if: [{ question: "arm", in: ["B"] }], repeat: { every_days: 14, count: 4 } })],
  });
  assert.deepEqual(dates(run(m, { arm: "A" }), "b"), []);
  const crossed = run(m, { arm: "A" }, { events: [{ key: "e1", type: "pd", date: "2026-01-25", answers: {} }] });
  assert.deepEqual(dates(crossed, "a"), ["2026-01-05", "2026-01-19"]);
  assert.deepEqual(dates(crossed, "b"), ["2026-02-02", "2026-02-16"]);
});

test("a pause moves later visits by its length, drops missed ones, or waits for the resume date", () => {
  const pause = (mode: string) => base({
    anchors: [...base().anchors, anchor("res", "ask", { label: "Resumed" })],
    events: [{ id: "hold", label: "Held", repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [] }],
    rules: [{ id: "h", label: "Treatment held", when: { event: "hold" }, stop_visits: [], stop_all: false, same_day: "keep", starts: null,
      blocks: [], allowed: null, effect: "", source: null, if: [], set_answers: null, pause: { visits: ["cyc"], resume: "res", mode } }],
    visits: [visit("screen", "scr", 0), visit("cyc", "scr", 0, { repeat: { every_days: 14, count: 4 } })],
  });
  const held = { events: [{ key: "e1", type: "hold", date: "2026-01-10", answers: {} }] };
  // Planned 5, 19 Jan, 2, 16 Feb; held 10 Jan, resumed 31 Jan (21 days).
  assert.deepEqual(dates(run(pause("shift"), {}, held), "cyc"), ["2026-01-05", null, null, null]);
  assert.match(visits(run(pause("shift"), {}, held))[2].relative, /after Resumed \(Treatment held\)/);
  assert.deepEqual(dates(run(pause("shift"), { "@res": "2026-01-31" }, held), "cyc"), ["2026-01-05", "2026-02-09", "2026-02-23", "2026-03-09"]);
  assert.deepEqual(dates(run(pause("skip"), { "@res": "2026-01-31" }, held), "cyc"), ["2026-01-05", "2026-02-02", "2026-02-16"]);
});

test("conditions on what happened: an event logged by the visit's date, a visit done or missed", () => {
  const m = base({
    events: [{ id: "pd", label: "Progression", repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [] }],
    visits: [
      visit("screen", "scr", 0, { window_after: 3 }),
      // Survival calls only after progression; scans only while it has not happened.
      visit("call", "scr", 0, { repeat: { every_days: 30, count: 4 }, include_if: [{ event: "pd", happened: true }] }),
      visit("scan", "scr", 0, { repeat: { every_days: 30, count: 4 }, include_if: [{ event: "pd", happened: false }] }),
      visit("retest", "scr", 10, { include_if: [{ visit: "screen", n: null, status: "missed" }] }),
      visit("review", "scr", 20, { procedures: [{ name: "Extra ECG", if: [{ visit: "screen", n: null, status: "done" }] }] }),
    ],
  });
  const pd = { events: [{ key: "e1", type: "pd", date: "2026-02-20", answers: {} }] };
  // Planned 5 Jan, 4 Feb, 6 Mar, 5 Apr; progression on 20 Feb.
  assert.deepEqual(dates(run(m, {}, pd), "call"), ["2026-03-06", "2026-04-05"]);
  assert.deepEqual(dates(run(m, {}, pd), "scan"), ["2026-01-05", "2026-02-04"]);
  assert.deepEqual(dates(run(m), "call"), []);
  assert.equal(dates(run(m), "scan").length, 4);
  // Screening window closed on 8 Jan without a date: the retest applies; done, it does not.
  assert.deepEqual(dates(run(m, {}, { today: "2026-01-12" }), "retest"), ["2026-01-15"]);
  assert.deepEqual(dates(run(m, {}, { today: "2026-01-07" }), "retest"), []);
  assert.deepEqual(dates(run(m, {}, { today: "2026-01-12", actuals: { "screen#1": START } }), "retest"), []);
  const ecg = (s: any) => visits(s).find((r: any) => r.visitId === "review").procedures.map((p: any) => p.name);
  assert.deepEqual(ecg(run(m, {}, { actuals: { "screen#1": START } })), ["Extra ECG"]);
  assert.deepEqual(ecg(run(m)), []);
  assert.equal(run(m).skipped.find((v: any) => v.id === "call"), undefined);
});
