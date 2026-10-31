// What the backend's events check (backend/app/kit/events.py) writes into a model, as the engine
// applies it once the event is logged: a rule that stops later visits, visits counted from the
// event date, a test added at later visits ({event, happened}) and a test dropped from them
// ({not: {event, happened}}). Constructed model in the exact shapes that check produces.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";

const visit = (id: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor: "d1", offset_days: offset, window_before: 0,
  window_after: 0, follows: null, include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null,
  procedures: [
    { name: "Spirometry", if: [{ not: { event: "preg", happened: true } }] },
    { name: "Urine pregnancy test", if: [{ event: "preg", happened: true }] },
  ],
  ...over,
});
const model = {
  protocol: {}, arms: [], review_questions: [], notes: [], questions: [],
  anchors: [{ id: "d1", label: "Day 1", kind: "start", relative_to: null, offset_days: 0, is_day1: true, day_zero: false,
    set_by_visit: null, estimates: [], range: null }],
  visits: [visit("dose1", 0), visit("dose2", 28), visit("dose3", 56), visit("fu", 84)],
  events: [{
    id: "preg", label: "Pregnancy", repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [],
    site_actions: ["Report to the sponsor within 24 hours"],
    check: { status: "changes", summary: "Dosing stops; outcome call.", evidence: [], checklist: "pregnancy" },
    visits: [{ ...visit("preg_out", 30), code: "PREG-OUT", name: "Pregnancy outcome call", modality: "phone", procedures: ["Pregnancy outcome"] }],
  }],
  rules: [{ id: "preg_stop", label: "Pregnancy stops dosing", when: { event: "preg" }, stop_visits: ["dose3"], stop_all: false,
    same_day: "keep", starts: null, blocks: [], allowed: null, effect: "Dosing visits stop.", source: "p. 42", if: [],
    set_answers: null, pause: null }],
} as any;
const START = "2026-01-05"; // dose1 5 Jan, dose2 2 Feb, dose3 2 Mar, fu 30 Mar
const run = (over: Record<string, unknown> = {}) =>
  computeKitSchedule(model, {}, { startAnchor: "d1", startDate: START, answers: {}, ...over } as any);
const rows = (s: any) => s.rows.filter((r: any) => r.type === "visit");
const tests = (s: any, id: string) =>
  rows(s).find((r: any) => r.visitId === id)?.procedures.map((p: any) => p.name);
const logged = { events: [{ key: "e1", type: "preg", date: "2026-02-10", answers: {} }] };

test("nothing changes until the event is logged", () => {
  const s = run();
  assert.deepEqual(rows(s).map((r: any) => r.visitId), ["dose1", "dose2", "dose3", "fu"]);
  assert.deepEqual(tests(s, "fu"), ["Spirometry"]);
  assert.deepEqual(s.stoppedBy, []);
});

test("logging pregnancy cancels the later dosing visit, adds the outcome call and swaps the tests from that date", () => {
  const s = run(logged);
  assert.deepEqual(s.stoppedBy.map((x: any) => x.label), ["Pregnancy stops dosing"]);
  assert.deepEqual(s.stoppedBy[0].visits.map((v: any) => v.code), ["DOSE3"]);
  const out = rows(s).find((r: any) => r.code === "PREG-OUT");
  assert.equal(out?.date, "2026-03-12"); // 30 days after 10 Feb
  // Visits before the event keep their tests; later ones get the pregnancy test instead of spirometry.
  assert.deepEqual(tests(s, "dose1"), ["Spirometry"]);
  assert.deepEqual(tests(s, "dose2"), ["Spirometry"]);
  assert.deepEqual(tests(s, "fu"), ["Urine pregnancy test"]);
});
