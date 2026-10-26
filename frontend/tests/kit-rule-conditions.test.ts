// Branches of one event: rules with conditions ("if"). Before this, two rules for the same event
// both acted, so a patient who stopped treatment early lost both the safety visit and the
// long-term follow-up. Each test is a protocol branch on when, what or who.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule, ruleIfText } from "../src/features/kit/index.ts";
import { eventSummaries } from "../src/features/kit/sections.ts";
import { patientTimeline } from "../src/features/kit/patientSections.ts";

const visit = (id: string, phase: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase, modality: "site", anchor, offset_days: offset, window_before: 0, window_after: 0,
  follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], ...over,
});
const rule = (id: string, label: string, stop: string[], cond: unknown[], over: Record<string, unknown> = {}) => ({
  id, label, when: { event: "stop" }, stop_visits: stop, stop_all: false, same_day: "keep", starts: null, blocks: [],
  allowed: null, effect: label, source: null, if: cond, ...over,
});
const model = (rules: unknown[], over: Record<string, unknown> = {}) => ({
  protocol: {}, arms: [], review_questions: [], notes: [],
  questions: [{ id: "arm", label: "Arm", text: "Arm?", scope: "participant", kind: "choice",
    options: [{ value: "A", label: "A" }, { value: "B", label: "B" }] },
  { id: "responder", label: "Responder", text: "Responded at C2?", scope: "decision", kind: "boolean", options: [], ask_at: { visit: "c2" } }],
  anchors: [anchor("day1", "start", { label: "Day 1", is_day1: true }), anchor("stopped", "ask", { label: "Treatment stopped" })],
  visits: [
    visit("c1", "Treatment", "day1", 0), visit("c2", "Treatment", "day1", 28), visit("c3", "Treatment", "day1", 56),
    visit("c4", "Treatment", "day1", 84), visit("safe", "Follow-up", "stopped", 30),
    visit("ltfu", "Follow-up", "stopped", 90, { repeat: { every_days: 90, count: 2, start_n: 1, chain: false, open_ended: false, overrides: [] } }),
  ],
  events: [{ id: "stop", label: "Treatment stopped", repeatable: true, ends_schedule: false, sets_anchor: "stopped", note: null,
    questions: [{ id: "grade", label: "Grade", text: "Grade?", kind: "choice", options: ["1", "2", "3"].map((v) => ({ value: v, label: v })) }],
    visits: [] }],
  rules, ...over,
}) as any;
const START = "2026-01-05";
const day = (n: number) => new Date(Date.parse(START + "T00:00:00Z") + n * 864e5).toISOString().slice(0, 10);
const stopOn = (n: number, answers: Record<string, string> = {}) => ({ key: `e${n}`, type: "stop", date: day(n), answers });
const run = (m: any, events: unknown[] = [], answers: Record<string, string> = { arm: "A" }) =>
  computeKitSchedule(m, {}, { startAnchor: "day1", startDate: START, answers, events } as any);
const codes = (s: any) => s.rows.filter((r: any) => r.type === "visit").map((r: any) => r.code);
const stoppedBy = (s: any) => Object.fromEntries(s.stoppedBy.map((b: any) => [b.label, b.visits.map((v: any) => v.code)]));

const byTiming = model([
  rule("early", "Stopped before Cycle 3", ["c3", "c4", "ltfu"], [{ timing: { visit: "c3", min_days: null, max_days: -1 } }]),
  rule("late", "Stopped after Cycle 3", ["c4", "safe"], [{ timing: { visit: "c3", min_days: 0, max_days: null } }]),
]);

test("WHEN: stopping before Cycle 3 keeps the safety visit and drops long-term follow-up", () => {
  const s = run(byTiming, [stopOn(41)]);
  assert.deepEqual(codes(s), ["C1", "C2", "SAFE"]);
  assert.deepEqual(stoppedBy(s), { "Stopped before Cycle 3": ["C3", "C4", "LTFU·1", "LTFU·2"] });
  assert.deepEqual(s.warnings, []);
});

test("WHEN: stopping after Cycle 3 gives long-term follow-up and no safety visit", () => {
  const s = run(byTiming, [stopOn(74)]);
  assert.deepEqual(codes(s), ["C1", "C2", "C3", "LTFU·1", "LTFU·2"]);
  assert.deepEqual(stoppedBy(s), { "Stopped after Cycle 3": ["C4", "SAFE"] });
  assert.deepEqual(s.warnings, []);
});

test("WHEN: a recorded Cycle 3 date decides the branch, not the planned one", () => {
  const late = computeKitSchedule(byTiming, {}, { startAnchor: "day1", startDate: START, answers: { arm: "A" },
    actuals: { "c3#1": day(70) }, events: [stopOn(65)] } as any);   // C3 done late: day 65 is before it
  assert.deepEqual(Object.keys(stoppedBy(late)), ["Stopped before Cycle 3"]);
});

test("WHAT: a grade 3 event stops treatment, a grade 1 event changes nothing", () => {
  const m = model([rule("g3", "Grade 3: stop treatment", ["c3", "c4"], [{ event_question: "grade", in: ["3"] }])]);
  assert.deepEqual(stoppedBy(run(m, [stopOn(41, { grade: "1" })])), {});
  assert.deepEqual(stoppedBy(run(m, [stopOn(41, { grade: "3" })])), { "Grade 3: stop treatment": ["C3", "C4"] });
});

test("WHAT: a later event that meets the condition acts even if the first one did not", () => {
  const m = model([rule("g3", "Grade 3: stop treatment", ["c3", "c4"], [{ event_question: "grade", in: ["3"] }])]);
  const s = run(m, [stopOn(20, { grade: "1" }), stopOn(60, { grade: "3" })]);
  assert.deepEqual(stoppedBy(s), { "Grade 3: stop treatment": ["C4"] });  // C3 (day 56) was before day 60
});

test("WHO: arm A and arm B take different branches of the same event", () => {
  const m = model([
    rule("a", "Arm A: safety visit only", ["ltfu"], [{ question: "arm", in: ["A"] }]),
    rule("b", "Arm B: long-term follow-up only", ["safe"], [{ question: "arm", in: ["B"] }]),
  ]);
  assert.deepEqual(stoppedBy(run(m, [stopOn(41)], { arm: "A" })), { "Arm A: safety visit only": ["LTFU·1", "LTFU·2"] });
  assert.deepEqual(stoppedBy(run(m, [stopOn(41)], { arm: "B" })), { "Arm B: long-term follow-up only": ["SAFE"] });
});

test("HOW MANY TIMES: a rule for the second time an event is logged waits for the second one", () => {
  const m = model([rule("second", "Second stop: end treatment", ["c4"], [{ occurrence: 2 }])]);
  assert.deepEqual(stoppedBy(run(m, [stopOn(20)])), {});
  assert.deepEqual(stoppedBy(run(m, [stopOn(20), stopOn(60)])), { "Second stop: end treatment": ["C4"] });
});

test("NOT KNOWN YET: a branch on an unanswered decision waits, says so, and acts once answered", () => {
  const m = model([rule("nr", "Non-responder stops", ["c3", "c4"], [{ question: "responder", in: ["no"] }])]);
  const waiting = run(m, [stopOn(41)]);
  assert.deepEqual(stoppedBy(waiting), {});
  assert.equal(waiting.issues.filter((i: any) => i.kind === "info").length, 1);
  assert.match(waiting.warnings[0], /Non-responder stops: whether this applies .* depends on something not recorded yet/);
  assert.deepEqual(stoppedBy(run(m, [stopOn(41)], { arm: "A", responder: "no" })), { "Non-responder stops": ["C3", "C4"] });
  assert.deepEqual(stoppedBy(run(m, [stopOn(41)], { arm: "A", responder: "yes" })), {});
});

test("rules without conditions behave exactly as before", () => {
  const m = model([rule("any", "Any stop", ["c4"], [])]);
  assert.deepEqual(stoppedBy(run(m, [stopOn(41)])), { "Any stop": ["C4"] });
});

test("the conditions are shown in words wherever the rule is", () => {
  assert.equal(ruleIfText(byTiming, byTiming.rules[0]), "If before C3");
  assert.equal(ruleIfText(byTiming, byTiming.rules[1]), "If on or after C3");
  const m = model([rule("x", "x", [], [{ question: "arm", in: ["A"] }, { event_question: "grade", in: ["3"] }, { occurrence: 2 }])]);
  assert.equal(ruleIfText(m, m.rules[0]), "If Arm = A and Grade = 3 and the second time");
  const summary = eventSummaries(byTiming).find((e) => e.id === "early")!;
  assert.match(summary.detail || "", /^If before C3: Stopped before Cycle 3/);
});

test("the patient timeline names the branch that acted, not the first rule for the event", () => {
  const participant = { startAnchor: "day1", startDate: START, answers: { arm: "A" }, events: [stopOn(74)] } as any;
  const current = computeKitSchedule(byTiming, {}, participant);
  const rows = patientTimeline({ model: byTiming, participant, current, withoutEvents: computeKitSchedule(byTiming, {}, { ...participant, events: [] }),
    baseline: computeKitSchedule(byTiming, {}, { ...participant, events: [] }), today: day(80), conditionOf: () => null }) as any;
  const all = JSON.stringify(rows);
  assert.match(all, /Stops 2 visits/);  // the "after Cycle 3" branch stops C4 + SAFE, not the 3 of the other branch
});
