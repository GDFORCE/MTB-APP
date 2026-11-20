// Pattern rules (18 Nov 2026): rules the kit fields cannot hold, named by their shape and
// calculated by a handler (src/features/kit/patterns.ts). A rule the backend marked `blocked`
// is never applied. Dates below are worked out by hand.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { openPointViews, withCheckedPoints } from "../src/features/kit/openPoints.ts";
import { PATTERN_HANDLERS, addBusinessDays, addMonths, blockedPatternRules } from "../src/features/kit/patterns.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const repeat = (every: number, count: number, over: Record<string, unknown> = {}) => ({
  every_days: every, count, count_question: null, start_n: 1, label_pattern: null, code_pattern: null, chain: false,
  open_ended: false, until_anchor: null, overrides: [], ...over,
});
const base = (over: Record<string, unknown>) => ({
  protocol: {}, arms: [], questions: [], review_questions: [], notes: [], events: [], rules: [],
  anchors: [{ id: "d1", label: "Day 1", kind: "start", relative_to: null, offset_days: 0, is_day1: true, day_zero: false,
    set_by_visit: null, estimates: [], range: null }], ...over,
}) as any;
const pr = (id: string, pattern: string, visits: string[], params: Record<string, unknown>, over: Record<string, unknown> = {}) =>
  ({ id, pattern, label: id, visits, params, quote: "", page: null, ...over });
const run = (model: any, start: string, over: Record<string, unknown> = {}) =>
  computeKitSchedule(model, {}, { startAnchor: "d1", startDate: start, answers: {}, today: start, ...over } as any) as any;
const dates = (s: any, id: string) => s.rows.filter((r: any) => r.type === "visit" && r.visitId === id).map((r: any) => r.date);
const row = (s: any, key: string) => s.rows.find((r: any) => r.key === key);

test("the frontend handlers are exactly the patterns the backend accepts", () => {
  const py = readFileSync(new URL("../../backend/app/kit/patterns.py", import.meta.url), "utf8");
  const backend = [...py.matchAll(/^ {4}"([A-Z][A-Z0-9_]*)": Pattern\(/gm)].map((m) => m[1]).sort();
  assert.ok(backend.length >= 3);
  assert.deepEqual(Object.keys(PATTERN_HANDLERS).sort(), backend);
});

test("calendar months keep the day of the month, or the protocol's rule when the month is short", () => {
  const jan31 = Date.UTC(2026, 0, 31);
  const iso = (t: number | null) => (t == null ? null : new Date(t).toISOString().slice(0, 10));
  assert.equal(iso(addMonths(jan31, 1, "last_day_of_month")), "2026-02-28");
  assert.equal(iso(addMonths(jan31, 1, "first_day_of_next_month")), "2026-03-01");
  assert.equal(addMonths(jan31, 1, "not_stated"), null);
  assert.equal(iso(addMonths(jan31, 2, "not_stated")), "2026-03-31");
  assert.equal(iso(addBusinessDays(Date.UTC(2026, 0, 30), 1)), "2026-02-02"); // Friday + 1 business day = Monday
});

test("monthly visits on a fixed grid count every month from the anchor, not from a shortened month", () => {
  const model = base({
    visits: [visit("m", "d1", 30, { repeat: repeat(30, 3), window_before: 5, window_after: 5 }), visit("home", "d1", 3, { follows: "m", repeat: repeat(30, 3) })],
    pattern_rules: [pr("cm", "CALENDAR_MONTHS", ["m"], { months: 1, every_months: 1, missing_day: "last_day_of_month" })],
  });
  const s = run(model, "2026-01-31");
  assert.deepEqual(dates(s, "m"), ["2026-02-28", "2026-03-31", "2026-04-30"]);
  assert.deepEqual([row(s, "m#1").windowStart, row(s, "m#1").windowEnd], ["2026-02-23", "2026-03-05"]);
  // The visit that follows moves with it.
  assert.deepEqual(dates(s, "home"), ["2026-03-03", "2026-04-03", "2026-05-03"]);
  assert.match(row(s, "m#1").notes.join(" "), /Dated by the protocol rule/);
  assert.equal(row(s, "m#1").rule, "Day 1 + 1 calendar month");
  assert.equal(row(s, "m#3").rule, "Day 1 + 3 calendar months");
});

test("a month without that day and no rule in the protocol gives no date, with a reason", () => {
  const model = base({
    visits: [visit("m", "d1", 30, { repeat: repeat(30, 2) })],
    pattern_rules: [pr("cm", "CALENDAR_MONTHS", ["m"], { months: 1, every_months: 1, missing_day: "not_stated" })],
  });
  const s = run(model, "2026-01-31");
  assert.deepEqual(dates(s, "m"), [null, "2026-03-31"]);
  assert.equal(row(s, "m#1").status, "waiting");
  assert.ok(s.issues.some((w: any) => /no day 31/.test(w.text)));
});

test("business days skip the weekend and give a window up to the last allowed day", () => {
  const model = base({
    visits: [visit("inf", "d1", 0), visit("call", "d1", 3, { follows: "inf", modality: "phone" })],
    pattern_rules: [pr("bd", "BUSINESS_DAYS", ["call"], { business_days: 3, within: true, excludes_holidays: true })],
  });
  const s = run(model, "2026-01-31"); // a Saturday
  const call = row(s, "call#1");
  assert.equal(call.date, "2026-02-04");
  assert.deepEqual([call.windowStart, call.windowEnd], ["2026-02-02", "2026-02-04"]);
  assert.deepEqual(call.windowDays, [2, 0]);
  assert.ok(s.issues.some((w: any) => /holiday/.test(w.text)), "holidays the app cannot know are said once");
});

test("a weekday rule moves the visit, and a weekly grid it moves does not drift", () => {
  const model = base({
    visits: [visit("fin", "d1", 209), visit("w", "d1", 5, { repeat: repeat(7, 3) })],
    pattern_rules: [
      pr("mon", "WEEKDAY", ["fin"], { weekday: "monday", direction: "on_or_after" }),
      pr("fri", "WEEKDAY", ["w"], { weekday: "friday", direction: "on_or_before" }),
    ],
  });
  const s = run(model, "2026-01-31");
  assert.equal(row(s, "fin#1").date, "2026-08-31"); // Day 210 = Fri 28 Aug; the first Monday on or after
  // Grid: 5 Feb (Thu), 12 Feb, 19 Feb -> the Friday on or before each: 30 Jan, 6 Feb, 13 Feb.
  assert.deepEqual(dates(s, "w"), ["2026-01-30", "2026-02-06", "2026-02-13"]);
});

test("a blocked rule is never applied, even when its name has a handler", () => {
  const model = base({
    visits: [visit("m", "d1", 30)],
    pattern_rules: [
      pr("cm", "CALENDAR_MONTHS", ["m"], { months: 1 }, { blocked: "“missing_day” must be ..." }),
      pr("x", "CONSECUTIVE_MISSED_VISITS_WITHDRAW", ["m"], { consecutive: 2 }, { blocked: "no_handler" }),
    ],
  });
  const s = run(model, "2026-01-31");
  assert.equal(row(s, "m#1").date, "2026-03-02"); // the plain 30 days
  assert.deepEqual(blockedPatternRules(model).map((r) => r.id), ["cm", "x"]);
});

test("the beyond-vocabulary benchmark model: months, business days and the Monday final visit", () => {
  const model = JSON.parse(readFileSync(new URL("../../docs/protocol-benchmarks/synthetic/syn06-beyond-vocabulary/hand-model.json", import.meta.url), "utf8"));
  const s = computeKitSchedule(model, {}, { startAnchor: "day1", startDate: "2026-01-31", answers: { far: "yes" }, today: "2026-01-31" } as any) as any;
  assert.deepEqual(dates(s, "infm"), ["2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31"]);
  assert.equal(row(s, "final#1").date, "2026-08-31");
  assert.equal(row(s, "call1#1").date, "2026-02-04");
  assert.equal(row(s, "homem#1").date, "2026-03-03");
  // The missed-visit rules have no handler: they stay blockers.
  assert.deepEqual(blockedPatternRules(model).map((r) => r.pattern).sort(),
    ["CONSECUTIVE_MISSED_VISITS_WITHDRAW", "MISSED_VISIT_TESTS_CARRIED_OVER"]);
});

test("reviewers see blocked rules first (they cannot tick them off) and calculated ones to check", () => {
  const model = base({
    visits: [visit("m", "d1", 30), visit("fin", "d1", 209)],
    open_points: [{ id: "op1", kind: "ambiguous", text: "unclear", why: "", used: "", quote: "", page: null, affects: [], stage: "model" }],
    pattern_rules: [
      pr("mon", "WEEKDAY", ["fin"], { weekday: "monday", direction: "on_or_after" }, { label: "Monday final visit", quote: "first Monday", page: 3 }),
      pr("x", "CONSECUTIVE_MISSED_VISITS_WITHDRAW", ["m"], { consecutive: 2 }, { blocked: "no_handler", label: "Two missed: withdrawn" }),
    ],
  });
  const views = openPointViews(model);
  assert.deepEqual(views.map((p) => [p.id, p.kind, p.tickable]), [
    ["pattern_x", "blocked", false], ["op1", "ambiguous", true], ["pattern_mon", "pattern", true]]);
  assert.match(views[0].why || "", /no calculation for CONSECUTIVE_MISSED_VISITS_WITHDRAW/);
  assert.deepEqual(views[0].affects, ["M"]);
  assert.equal(views[2].page, "p. 3");
  // A tick on a blocked rule is ignored; a tick on a calculated one is saved with the model.
  const ticked = withCheckedPoints(model, { pattern_mon: true, pattern_x: true });
  assert.deepEqual(ticked.pattern_rules.map((r: any) => r.checked ?? false), [true, false]);
  assert.equal(openPointViews(ticked).find((p) => p.id === "pattern_x")!.checked, false);
});
