// "Followed telephonically 30 days after the last study drug administration" (CRD/09 crossover):
// the call counts from the last dose still on the schedule, and withdrawal keeps it, so a patient who
// stops after Period I gets the call 30 days after the Period I dose. Constructed model, dates by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { enterableAnchors } from "../src/features/kit/participant.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Period", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const ending = (id: string, keeps: string[] = []) => ({
  id, label: id, repeatable: false, ends_schedule: true, note: null, questions: [], visits: [], keeps_visits: keeps,
});
const MODEL = {
  protocol: {}, arms: [], review_questions: [], notes: [], questions: [], rules: [],
  anchors: [
    anchor("scr", "start"),
    anchor("p1", "relative", { relative_to: "scr", offset_days: 7, is_day1: true }),
    anchor("p2", "ask", { estimates: [{ when: [], relative_to: "p1", offset_days: 21 }], range: { relative_to: "p1", min_days: 21, max_days: null } }),
    anchor("last_dose", "last_visit", { label: "Last dose", last_of: ["p1_dose", "p2_dose"] }),
  ],
  visits: [
    visit("p1_dose", "p1", 0), visit("p1_out", "p1", 2),
    visit("p2_dose", "p2", 0), visit("p2_eos", "p2", 2),
    visit("call", "last_dose", 30, { modality: "phone" }),
  ],
  events: [ending("withdrawal", ["call"]), ending("death")],
} as any;
// Screening 5 Jan 2026, Period I dose 12 Jan.
const run = (rec: Record<string, unknown> = {}) =>
  computeKitSchedule(MODEL, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: {}, ...rec } as any);
const row = (s: any, id: string) => s.rows.find((r: any) => r.type === "visit" && r.visitId === id) || null;
const P2 = { "@p2": "2026-02-02" };

test("the call is 30 days after the Period II dose when both periods happen", () => {
  const s = run({ answers: P2 });
  assert.equal(row(s, "call").date, "2026-03-04");
  assert.equal(row(s, "call").relative, null);
  assert.match(row(s, "call").rule, /^Last dose/);
  // Done late, the real dose date counts.
  assert.equal(row(run({ answers: P2, actuals: { "p2_dose#1": "2026-02-05" } }), "call").date, "2026-03-07");
});

test("before the Period II date is known the call waits for it", () => {
  const s = run();
  assert.equal(row(s, "call").date, null);
  assert.equal(row(s, "call").status, "waiting");
});

test("withdrawn after Period I: the call is 30 days after the Period I dose actually given", () => {
  const events = [{ key: "w", type: "withdrawal", date: "2026-01-20", answers: {} }];
  const s = run({ answers: P2, events, actuals: { "p1_dose#1": "2026-01-13" } });
  assert.equal(row(s, "p2_dose"), null, "Period II is cancelled");
  assert.equal(row(s, "call").date, "2026-02-12");
  // Without the protocol's date entered for Period II it is the same.
  assert.equal(row(run({ events, actuals: { "p1_dose#1": "2026-01-13" } }), "call").date, "2026-02-12");
});

test("no call after death, and none when no dose was given", () => {
  assert.equal(row(run({ answers: P2, events: [{ key: "d", type: "death", date: "2026-01-20", answers: {} }] }), "call"), null);
  assert.equal(row(run({ answers: P2, events: [{ key: "w", type: "withdrawal", date: "2026-01-08", answers: {} }] }), "call"), null);
});

test("the last-dose date is worked out, never entered by the site", () => {
  assert.deepEqual(enterableAnchors(MODEL).map((a: any) => a.id), ["p1", "p2"]);
});

// Daily oral dosing between visits (APD334-202): follow-up is 2 and 4 weeks after the last dose. A drug
// stop logged between visits is that last dose; the last visit before it is not.
test("with daily dosing an early stop logged between visits is the last dose", () => {
  const daily = {
    protocol: {}, arms: [], review_questions: [], notes: [], questions: [],
    anchors: [anchor("d1", "start"), anchor("last_dose", "last_visit", { last_of: ["w2", "w6", "w10"], stop_is_last_dose: true })],
    visits: [visit("w2", "d1", 14), visit("w6", "d1", 42), visit("w10", "d1", 70), visit("fu1", "last_dose", 14), visit("fu2", "last_dose", 28)],
    events: [ending("stop", ["fu1", "fu2"])],
    rules: [{ id: "stop_r", label: "Drug stopped", when: { event: "stop" }, if: [], stop_visits: [], stop_all: true, same_day: "keep",
      starts: null, blocks: [], allowed: null, effect: null, source: null, keeps_visits: ["fu1", "fu2"] }],
  } as any;
  const at = (events: any[] = []) => computeKitSchedule(daily, {}, { startAnchor: "d1", startDate: "2026-01-01", answers: {}, events } as any) as any;
  const date = (s: any, id: string) => s.rows.find((r: any) => r.type === "visit" && r.visitId === id)?.date ?? null;
  assert.equal(date(at(), "fu1"), "2026-03-26", "completed: 14 days after the last visit");
  const s = at([{ key: "s", type: "stop", date: "2026-02-20", answers: {} }]);
  assert.equal(date(s, "w10"), null, "visits after the stop are cancelled");
  assert.equal(date(s, "fu1"), "2026-03-06");
  assert.equal(date(s, "fu2"), "2026-03-20");
  // Without the flag the follow-up still counts from the last visit given (dose-at-visit protocols).
  const plain = { ...daily, anchors: [daily.anchors[0], { ...daily.anchors[1], stop_is_last_dose: false }] };
  const p = computeKitSchedule(plain, {}, { startAnchor: "d1", startDate: "2026-01-01", answers: {}, events: [{ key: "s", type: "stop", date: "2026-02-20", answers: {} }] } as any) as any;
  assert.equal(date(p, "fu1"), "2026-02-26");
});
