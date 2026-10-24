// Rules read from the protocol: what happens to the schedule if something happens. Constructed
// models shaped like SP-102-05 (a second injection replaces the rest of Period 1) and KCP-330-008
// (progression ends treatment, follow-up continues).
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { eventSummaries } from "../src/features/kit/sections.ts";
import { patientTimeline } from "../src/features/kit/patientSections.ts";

const visit = (id: string, phase: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase, modality: "site", anchor, offset_days: offset, window_before: 3, window_after: 3,
  follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], ...over,
});
const inj2Rule = {
  id: "inj2", label: "Injection 2 given", when: { anchor: "p2d1" },
  stop_visits: ["p1w8", "p1w12", "p1w16", "p1w20", "p1w24"], stop_all: false, same_day: "keep", starts: "p2d1", blocks: [],
  allowed: { relative_to: "p1d1", min_days: 29, max_days: 140, not_on_visit: "p1w4" },
  effect: "Later Period 1 visits stop; Period 2 follows Injection 2.", source: "p. 23, footnote 13",
};
const aesiRule = {
  id: "aesi", label: "AESI", when: { event: "aesi" }, stop_visits: [], stop_all: false, same_day: "keep", starts: null,
  blocks: ["inj2"], allowed: null, effect: "Injection 2 is no longer allowed.", source: null,
};
const sp102 = {
  protocol: {}, arms: [], questions: [], review_questions: [], notes: [],
  anchors: [
    anchor("p1d1", "start", { label: "Period 1 Day 1", is_day1: true }),
    anchor("p2d1", "ask", { label: "Injection 2", set_by_visit: "p2d1v" }),
  ],
  visits: [
    visit("p1d1v", "Period 1", "p1d1", 0), visit("p1w4", "Period 1", "p1d1", 28), visit("p1w8", "Period 1", "p1d1", 56),
    visit("p1w12", "Period 1", "p1d1", 84), visit("p1w16", "Period 1", "p1d1", 112), visit("p1w20", "Period 1", "p1d1", 140),
    visit("p1w24", "Period 1", "p1d1", 168), visit("p2d1v", "Period 2", "p2d1", 0), visit("p2w2", "Period 2", "p2d1", 14),
    visit("p2w4", "Period 2", "p2d1", 28),
  ],
  events: [{ id: "aesi", label: "AESI", repeatable: true, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [] }],
  rules: [inj2Rule, aesiRule],
} as any;
const START = "2026-01-01";
const day = (n: number) => new Date(Date.parse(START + "T00:00:00Z") + n * 864e5).toISOString().slice(0, 10);
const run = (answers: Record<string, string> = {}, over: Record<string, unknown> = {}) =>
  computeKitSchedule(sp102, {}, { startAnchor: "p1d1", startDate: START, answers, ...over } as any);
const codes = (s: any) => s.rows.filter((r: any) => r.type === "visit").map((r: any) => r.code);

test("before Injection 2 every Period 1 visit stays and says it stops if Injection 2 is given", () => {
  const s = run();
  assert.deepEqual(codes(s), ["P1D1V", "P1W4", "P1W8", "P1W12", "P1W16", "P1W20", "P1W24", "P2D1V", "P2W2", "P2W4"]);
  const w12 = s.rows.find((r: any) => r.code === "P1W12") as any;
  assert.deepEqual(w12.stopsIf, ["Injection 2 given"]);
  assert.deepEqual((s.rows.find((r: any) => r.code === "P1W4") as any).stopsIf, []);
  assert.deepEqual(s.stoppedBy, []);
  assert.deepEqual(s.warnings, []);
});

test("entering the Injection 2 date stops the later Period 1 visits and dates Period 2 from it", () => {
  const s = run({ "@p2d1": day(60) });
  assert.deepEqual(codes(s), ["P1D1V", "P1W4", "P1W8", "P2D1V", "P2W2", "P2W4"]);
  assert.equal((s.rows.find((r: any) => r.code === "P2W4") as any).date, day(88));
  assert.equal(s.stoppedBy.length, 1);
  assert.equal(s.stoppedBy[0].label, "Injection 2 given");
  assert.equal(s.stoppedBy[0].date, day(60));
  assert.deepEqual(s.stoppedBy[0].visits.map((v) => v.code), ["P1W12", "P1W16", "P1W20", "P1W24"]);
  assert.deepEqual(s.warnings, []);
});

test("a Period 1 visit on the Injection 2 day is kept when the protocol says so, and a done visit always stays", () => {
  assert.ok(codes(run({ "@p2d1": day(56) })).includes("P1W8"));
  const cancelSameDay = { ...sp102, rules: [{ ...inj2Rule, same_day: "cancel" }] };
  const s = computeKitSchedule(cancelSameDay, {}, { startAnchor: "p1d1", startDate: START, answers: { "@p2d1": day(56) } } as any);
  assert.ok(!codes(s).includes("P1W8"));
  const done = run({ "@p2d1": day(60) }, { actuals: { "p1w12#1": day(85) } });
  assert.ok(codes(done).includes("P1W12"));
});

test("the actual date of the Period 2 visit triggers the rule too", () => {
  const s = run({}, { actuals: { "p2d1v#1": day(100) } });
  assert.deepEqual(s.stoppedBy[0].visits.map((v) => v.code), ["P1W16", "P1W20", "P1W24"]);
});

test("a trigger outside its allowed window, or on a forbidden visit day, is flagged", () => {
  assert.deepEqual(run({ "@p2d1": day(20) }).warnings,
    ["Injection 2 given is 20 days after Period 1 Day 1; the protocol allows 29 to 140 days."]);
  const onW4 = run({ "@p2d1": day(28) }).warnings;
  assert.equal(onW4.length, 2);
  assert.match(onW4[1], /falls on the P1W4 visit day/);
});

test("an event that rules out a later one is flagged, before and after that one is entered", () => {
  const events = [{ key: "e1", type: "aesi", date: day(40), answers: {} }];
  assert.match(run({}, { events }).warnings[0], /Injection 2 given is no longer allowed after AESI/);
  assert.match(run({ "@p2d1": day(60) }, { events }).warnings[0], /comes after AESI .* which rules it out/);
});

test("an ending event keeps the follow-up counted from the date it sets", () => {
  const kcp = {
    protocol: {}, arms: [], questions: [], review_questions: [], notes: [],
    anchors: [anchor("c1d1", "start", { is_day1: true }), anchor("eot", "ask"), anchor("fu", "relative", { relative_to: "eot", offset_days: 30 })],
    visits: [
      visit("c1", "Treatment", "c1d1", 0, { repeat: { every_days: 28, count: 6, start_n: 1, chain: false, open_ended: false, until_anchor: null, overrides: [] } }),
      visit("final", "Follow-up", "eot", 0), visit("safety", "Follow-up", "fu", 0),
    ],
    events: [{ id: "pd", label: "Progression", repeatable: false, ends_schedule: true, sets_anchor: "eot", note: null, questions: [], visits: [] }],
    rules: [],
  } as any;
  const s = computeKitSchedule(kcp, {}, { startAnchor: "c1d1", startDate: START, answers: {}, events: [{ key: "e", type: "pd", date: day(70), answers: {} }] } as any);
  assert.deepEqual(codes(s), ["C1·1", "C1·2", "C1·3", "FINAL", "SAFETY"]);
  assert.equal((s.rows.find((r: any) => r.code === "SAFETY") as any).date, day(100));
  assert.equal(s.ended?.cancelled, 3);
});

test("a stop-all rule on an event stops visits other events added, but not the event's own", () => {
  const model = {
    ...sp102,
    events: [
      { id: "uns", label: "Unscheduled", repeatable: true, ends_schedule: false, sets_anchor: null, note: null, questions: [],
        visits: [visit("uns_fu", "Unscheduled", "p1d1", 30)] },
      { id: "wd", label: "Withdrawal", repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [],
        visits: [visit("et", "End", "p1d1", 0)] },
    ],
    rules: [{ ...inj2Rule, id: "wd", label: "Withdrawal", when: { event: "wd" }, stop_visits: [], stop_all: true, starts: null, allowed: null }],
  } as any;
  const s = computeKitSchedule(model, {}, {
    startAnchor: "p1d1", startDate: START, answers: {},
    events: [{ key: "u", type: "uns", date: day(10), answers: {} }, { key: "w", type: "wd", date: day(30), answers: {} }],
  } as any);
  assert.deepEqual(codes(s), ["P1D1V", "P1W4", "ET"]);
  // Period 2 was still waiting for its date; once participation ends it will not happen either.
  assert.deepEqual(s.stoppedBy[0].visits.map((v) => v.code),
    ["P1W8", "P1W12", "P1W16", "P1W20", "P1W24", "P2D1V", "P2W2", "P2W4", "UNS_FU"]);
});

test("the protocol view lists every rule with its effect and source", () => {
  const list = eventSummaries(sp102);
  assert.deepEqual(list.map((e) => [e.label, e.effect]), [["Injection 2 given", "Stops 5 visits"], ["AESI", "Rules something out"]]);
  assert.equal(list[0].detail, "Later Period 1 visits stop; Period 2 follows Injection 2. (p. 23, footnote 13)");
  assert.match(list[1].detail!, /Rules out: Injection 2 given/);
});

test("the patient timeline shows the stopped visits as one row with the rule that stopped them", () => {
  const current = run({ "@p2d1": day(60) });
  const t = patientTimeline({
    model: sp102, participant: { startAnchor: "p1d1", startDate: START, answers: { "@p2d1": day(60) }, events: [] } as any,
    current, withoutEvents: current, baseline: current, today: day(1), conditionOf: () => null,
  });
  const struck = t.sections.flatMap((s) => s.rows).find((r) => r.kind === "cancelled")!;
  assert.equal(struck.name, "4 planned visits stopped");
  assert.equal(struck.date, day(60));
  assert.deepEqual(struck.tags.map((x) => x.text), ["By Injection 2 given"]);
  assert.equal(t.cancelled, 4);
  assert.deepEqual(t.stopped, [{ label: "Injection 2 given", count: 4, date: day(60) }]);
});

test("a trigger dated before the date it is measured from is a mistake: the rule is not applied", () => {
  const s = run({ "@p2d1": day(-10) });
  assert.deepEqual(s.stoppedBy, []);
  assert.ok(codes(s).includes("P1W12"));
  assert.deepEqual((s.rows.find((r: any) => r.code === "P1W12") as any).stopsIf, ["Injection 2 given"]);
  assert.deepEqual(s.issues.map((i) => i.kind), ["order"]);
  assert.match(s.warnings[0], /is before Period 1 Day 1 .* so it was not applied/);
});

test("each rule's allowed window is given as dates for the date sheet", () => {
  assert.deepEqual(run().ruleWindows, [
    { rule: "inj2", label: "Injection 2 given", anchor: "p2d1", event: null, from: day(29), to: day(140), notOn: { code: "P1W4", date: day(28) } },
  ]);
  assert.deepEqual(run({ "@p2d1": day(20) }).issues.map((i) => i.kind), ["window"]);
  assert.deepEqual(run({}, { events: [{ key: "a", type: "aesi", date: day(40), answers: {} }] }).issues.map((i) => i.kind), ["info"]);
});
