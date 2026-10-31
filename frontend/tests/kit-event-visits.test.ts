// Visits a logged event adds: repeats counted from the previous visit move when one is late (only
// when the protocol counts that way), and rules can stop them. Constructed models, dates by hand.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";

const visit = (id: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Follow-up", modality: "site", anchor: "scr", offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const event = (id: string, label: string, visits: unknown[] = []) => ({ id, label, repeatable: false, ends_schedule: false, note: null, questions: [], visits });
const model = (events: unknown[], rules: unknown[] = []) => ({
  protocol: {}, arms: [], review_questions: [], notes: [], questions: [], rules, events,
  anchors: [{ id: "scr", label: "Screening", kind: "start", relative_to: null, offset_days: 0, is_day1: true, day_zero: false, set_by_visit: null, estimates: [], range: null }],
  visits: [visit("screen", 0)],
}) as any;
const run = (m: any, rec: Record<string, unknown> = {}) =>
  computeKitSchedule(m, {}, { startAnchor: "scr", startDate: "2026-01-05", answers: {}, today: "2026-03-01", ...rec } as any);
const rows = (s: any, id: string) => s.rows.filter((r: any) => r.type === "visit" && r.visitId === id);
const dates = (s: any, id: string) => rows(s, id).map((r: any) => r.date);
const PD = [{ key: "e1", type: "pd", date: "2026-03-01", answers: {} }];
// Progression 1 Mar 2026; follow-up every 12 weeks: 24 May, 16 Aug, 8 Nov.
const fu = (chain: boolean) => model([event("pd", "Progression", [visit("fu", 84, { repeat: { every_days: 84, chain, count: 3 } })])]);

test("counted from the event date, a late follow-up leaves the later ones where they were", () => {
  assert.deepEqual(dates(run(fu(false), { events: PD }), "fu"), ["2026-05-24", "2026-08-16", "2026-11-08"]);
  const late = run(fu(false), { events: PD, actuals: { "ev.e1.fu#1": "2026-06-14" } });
  assert.deepEqual(dates(late, "fu"), ["2026-06-14", "2026-08-16", "2026-11-08"]);
});

test("counted from the previous visit, a late follow-up moves the later ones", () => {
  assert.deepEqual(dates(run(fu(true), { events: PD }), "fu"), ["2026-05-24", "2026-08-16", "2026-11-08"]);
  const late = run(fu(true), { events: PD, actuals: { "ev.e1.fu#1": "2026-06-14" } });
  assert.deepEqual(dates(late, "fu"), ["2026-06-14", "2026-09-06", "2026-11-29"]);
  // Only recorded dates move them: the second one done late moves the third.
  const both = run(fu(true), { events: PD, actuals: { "ev.e1.fu#1": "2026-06-14", "ev.e1.fu#2": "2026-09-10" } });
  assert.deepEqual(dates(both, "fu"), ["2026-06-14", "2026-09-10", "2026-12-03"]);
});

// After progression: safety visits every 4 weeks and survival calls every 12 weeks. Starting a new
// therapy stops the safety visits only.
const post = model([
  event("pd", "Progression", [
    visit("saf", 28, { repeat: { every_days: 28, chain: false, count: 4 } }),
    visit("surv", 84, { repeat: { every_days: 84, chain: false, count: 2 } }),
  ]),
  event("nt", "New therapy"),
], [{ id: "nt_stop", label: "New therapy started", when: { event: "nt" }, if: [], stop_visits: ["saf"], stop_all: false,
  same_day: "keep", starts: null, blocks: [], allowed: null, effect: "Safety visits stop; survival follow-up continues.", source: null }]);

test("a rule can stop visits an event added, and only those it names", () => {
  const before = run(post, { events: PD });
  assert.deepEqual(dates(before, "saf"), ["2026-03-29", "2026-04-26", "2026-05-24", "2026-06-21"]);
  assert.deepEqual(rows(before, "saf")[0].stopsIf, ["New therapy started"]);
  assert.deepEqual(rows(before, "surv")[0].stopsIf, []);
  const after = run(post, { events: [...PD, { key: "e2", type: "nt", date: "2026-05-10", answers: {} }] });
  assert.deepEqual(dates(after, "saf"), ["2026-03-29", "2026-04-26"]);
  assert.deepEqual(dates(after, "surv"), ["2026-05-24", "2026-08-16"]);
  assert.equal(after.stoppedBy[0].rule, "nt_stop");
  assert.deepEqual(after.stoppedBy[0].visits.map((v: any) => v.key), ["ev.e1.saf#3", "ev.e1.saf#4"]);
});

test("a stopped event visit that was already done stays", () => {
  const s = run(post, { events: [...PD, { key: "e2", type: "nt", date: "2026-05-10", answers: {} }], actuals: { "ev.e1.saf#3": "2026-05-24" } });
  assert.deepEqual(dates(s, "saf"), ["2026-03-29", "2026-04-26", "2026-05-24"]);
});

// A withdrawal visit added only when the answer given with the event says so (CR150-16: a serious
// adverse event withdraws only when the reaction threatens the patient's well-being).
test("an event visit gated on an answer given with the event follows that answer", () => {
  const sae = { ...event("sae", "Serious adverse event", [visit("wd", 0, { include_if: [{ event_question: "threat", in: ["yes"] }] })]),
    questions: [{ id: "threat", label: "Threatening", kind: "boolean", scope: "event" }] };
  const m = model([sae]);
  const log = (threat: string) => run(m, { events: [{ key: "e1", type: "sae", date: "2026-02-01", answers: { threat } }] });
  assert.deepEqual(dates(log("yes"), "wd"), ["2026-02-01"]);
  assert.deepEqual(dates(log("no"), "wd"), []);
  const nested = model([{ ...sae, visits: [visit("wd", 0, { include_if: [{ not: { event_question: "threat", in: ["no"] } }] })] }]);
  assert.deepEqual(dates(run(nested, { events: [{ key: "e1", type: "sae", date: "2026-02-01", answers: { threat: "no" } }] }), "wd"), []);
});
