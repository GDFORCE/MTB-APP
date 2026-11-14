// Schedule fidelity (10 Nov 2026): the cases the protocol bench exposed, as engine tests on
// constructed models with dates worked out by hand.
//  - time points and instructions that apply only to some participants;
//  - rules that stop or add assessments at visits that still happen, and "the last dose" counted
//    only where the dose is still given;
//  - a visit conditional on an event NOT having happened; a repeat that follows a repeat when
//    the occurrence it follows is stopped; a last-dose date set by an event;
//  - "no window stated" kept apart from "exact day";
//  - open points: order, names, the "to check" tag on the protocol timeline.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { openPointViews, openPointsByVisit, withCheckedPoints } from "../src/features/kit/openPoints.ts";
import { protocolTimeline } from "../src/features/kit/sections.ts";

const visit = (id: string, anchor: string, offset: number, over: Record<string, unknown> = {}) => ({
  id, code: id.toUpperCase(), name: id, phase: "Treatment", modality: "site", anchor, offset_days: offset, window_before: 0,
  window_after: 0, follows: null, procedures: [], include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null, ...over,
});
const anchor = (id: string, kind: string, over: Record<string, unknown> = {}) => ({
  id, label: id, kind, relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null, ...over,
});
const event = (id: string, over: Record<string, unknown> = {}) => ({
  id, label: id, repeatable: false, ends_schedule: false, sets_anchor: null, note: null, questions: [], visits: [], ...over,
});
const rule = (id: string, when: Record<string, string>, over: Record<string, unknown> = {}) => ({
  id, label: id, when, stop_visits: [], stop_all: false, same_day: "keep", starts: null, blocks: [], allowed: null, effect: id,
  source: null, if: [], set_answers: null, pause: null, ...over,
});
const yesNo = (id: string) => ({ id, label: id, text: `${id}?`, help: "", kind: "boolean", scope: "participant",
  options: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] });
const base = (over: Record<string, unknown>) => ({
  protocol: {}, arms: [], questions: [], review_questions: [], notes: [], events: [], rules: [],
  anchors: [anchor("d1", "start", { is_day1: true })], ...over,
}) as any;
const START = "2026-01-05"; // a Monday
const run = (model: any, over: Record<string, unknown> = {}) =>
  computeKitSchedule(model, {}, { startAnchor: "d1", startDate: START, answers: {}, today: START, ...over } as any) as any;
const row = (s: any, id: string, n = 1) => s.rows.find((r: any) => r.type === "visit" && r.key === `${id}#${n}`) || null;
const names = (r: any) => r.procedures.map((p: any) => p.name);
const plus = (n: number) => new Date(Date.parse(START + "T00:00:00Z") + n * 864e5).toISOString().slice(0, 10);

test("a time point, and a sample within one, apply only where their conditions hold", () => {
  const pk = [{ question: "pk", in: ["yes"] }];
  const m = base({
    questions: [yesNo("pk")],
    visits: [visit("dose", "d1", 0, { timepoints: [
      { label: "Pre-dose", offset_min: 0, window_before_min: 30, window_after_min: 0, procedures: [{ name: "ECG", if: [] }, { name: "PK sample", if: pk }] },
      { label: "2 h", offset_min: 120, window_before_min: 10, window_after_min: 10, procedures: [{ name: "PK sample", if: pk }] },
      { label: "4 h", offset_min: 240, window_before_min: 10, window_after_min: 10, procedures: [{ name: "PK sample", if: [] }], if: pk },
    ] })],
  });
  const tps = (answers: Record<string, string>) => row(run(m, { answers }), "dose").timepoints.map((t: any) => `${t.label}: ${t.procedures.join(", ")}`);
  assert.deepEqual(tps({ pk: "yes" }), ["Pre-dose: ECG, PK sample", "2 h: PK sample", "4 h: PK sample"]);
  // Not in the sub-study: the ECG time point stays, the PK-only ones go.
  assert.deepEqual(tps({ pk: "no" }), ["Pre-dose: ECG"]);
  // Not answered yet: nothing is hidden.
  assert.equal(tps({}).length, 3);
});

test("instructions are shown only to the participants they apply to", () => {
  const m = base({
    questions: [{ ...yesNo("seq"), kind: "choice", options: [{ value: "AB", label: "AB" }, { value: "BA", label: "BA" }] }],
    visits: [visit("p1", "d1", 0, { instructions: [
      { text: "Dose after a 10-hour fast", if: [{ question: "seq", in: ["AB"] }], kind: "prep" },
      { text: "Dose 30 minutes after a high-fat breakfast", if: [{ question: "seq", in: ["BA"] }], kind: "prep" },
      { text: "Questionnaires before any other assessment", if: [], kind: "order" },
    ] })],
  });
  const texts = (answers: Record<string, string>) => row(run(m, { answers }), "p1").instructions.map((x: any) => x.text);
  assert.deepEqual(texts({ seq: "AB" }), ["Dose after a 10-hour fast", "Questionnaires before any other assessment"]);
  assert.deepEqual(texts({ seq: "BA" }), ["Dose 30 minutes after a high-fat breakfast", "Questionnaires before any other assessment"]);
  const open = row(run(m), "p1").instructions;
  assert.equal(open.length, 3);
  assert.equal(open[0].conditional, true);
  assert.equal(open[2].conditional, false);
});

// Injections at Weeks 0, 4, 8, 12; study drug stopped on Day 40: the visits go on without the
// injection, and the follow-up 56 days after the last dose counts from Week 4 (Day 29), not Week 12.
const stopModel = (withNames: boolean) => base({
  anchors: [anchor("d1", "start", { is_day1: true }),
    anchor("last_dose", "last_visit", { last_of: ["w0", "w4", "w8", "w12"], ...(withNames ? { last_with: ["Injection"] } : {}) })],
  visits: [...[0, 4, 8, 12].map((w) => visit(`w${w}`, "d1", w * 7, { procedures: [{ name: "Vital signs", if: [] }, { name: "Injection", if: [] }] })),
    visit("fu", "last_dose", 56)],
  events: [event("stop")],
  rules: [rule("stop_rule", { event: "stop" }, {
    stop_procedures: [{ visits: "all", names: ["injection"] }],
    add_procedures: [{ visits: ["w12"], procedures: [{ name: "Off-treatment questionnaire", if: [] }] }] })],
});
const stopped = { events: [{ key: "s", type: "stop", date: plus(39), answers: {} }], actuals: { "w0#1": START, "w4#1": plus(28) } };

test("a rule stops and adds assessments at visits that still happen, from its date on", () => {
  const s = run(stopModel(true), stopped);
  assert.deepEqual(names(row(s, "w4")), ["Vital signs", "Injection"], "before the stop nothing changes");
  assert.deepEqual(names(row(s, "w8")), ["Vital signs"]);
  assert.deepEqual(names(row(s, "w12")), ["Vital signs", "Off-treatment questionnaire"]);
  assert.equal(row(s, "w8").date, plus(56), "the visit itself stays on its date");
  // Without the event nothing is stopped or added.
  assert.deepEqual(names(row(run(stopModel(true)), "w12")), ["Vital signs", "Injection"]);
});

test("the last dose counts only the visits at which the dose is still given", () => {
  assert.equal(row(run(stopModel(true), stopped), "fu").date, plus(28 + 56));
  // Planned, nobody stopped: 56 days after Week 12.
  assert.equal(row(run(stopModel(true)), "fu").date, plus(84 + 56));
  // Without last_with the anchor cannot tell that dosing stopped (how models saved before behave).
  assert.equal(row(run(stopModel(false), stopped), "fu").date, plus(84 + 56));
});

test("a date that rests on an unrecorded dose after an early stop says so", () => {
  const s = run(stopModel(true), { events: stopped.events });
  assert.equal(row(s, "fu").date, plus(28 + 56));
  assert.ok(row(s, "fu").notes.some((n: string) => /Counted from W4 on its planned date .* not recorded as done/.test(n)));
  // Recorded: no such note.
  assert.ok(!row(run(stopModel(true), stopped), "fu").notes.some((n: string) => /not recorded as done/.test(n)));
});

test("a visit conditional on an event not having happened is scheduled until the event", () => {
  const m = base({
    events: [event("stop")],
    visits: [visit("ext", "d1", 0, { repeat: { every_days: 28, count: 3 }, include_if: [{ not: { event: "stop", happened: true } }] })],
  });
  const dates = (s: any) => s.rows.filter((r: any) => r.visitId === "ext").map((r: any) => r.date);
  assert.deepEqual(dates(run(m)), [START, plus(28), plus(56)]);
  assert.deepEqual(dates(run(m, { events: [{ key: "s", type: "stop", date: plus(30), answers: {} }] })), [START, plus(28)]);
});

test("Day 8 of a cycle that is stopped is not given either", () => {
  const rep = (over: Record<string, unknown> = {}) => ({ every_days: 21, count: 4, start_n: 1, chain: true, code_pattern: "C{n}D1", ...over });
  const m = base({
    events: [event("pd")],
    visits: [visit("c1", "d1", 0, { repeat: rep() }),
      visit("c8", "d1", 7, { follows: "c1", repeat: rep({ chain: false, code_pattern: "C{n}D8" }) })],
    rules: [rule("pd_rule", { event: "pd" }, { stop_visits: ["c1"] })],
  });
  const codes = (s: any) => s.rows.filter((r: any) => r.type === "visit").map((r: any) => `${r.code} ${r.date}`);
  assert.equal(codes(run(m)).length, 8);
  // Progression on Day 30: Cycle 2 (Day 22) and its Day 8 (Day 29) were given; Cycle 3 and 4 and their Day 8s go.
  assert.deepEqual(codes(run(m, { events: [{ key: "p", type: "pd", date: plus(29), answers: {} }] })),
    [`C1D1 ${START}`, `C1D8 ${plus(7)}`, `C2D1 ${plus(21)}`, `C2D8 ${plus(28)}`]);
});

test("a last-dose date set by an event stands", () => {
  const m = base({
    anchors: [anchor("d1", "start", { is_day1: true }), anchor("last_dose", "last_visit", { last_of: ["w0", "w4", "w8"] })],
    visits: [visit("w0", "d1", 0), visit("w4", "d1", 28), visit("w8", "d1", 56), visit("fu", "last_dose", 30)],
    events: [event("stop", { sets_anchor: "last_dose" })],
    rules: [rule("stop_rule", { event: "stop" }, { stop_visits: ["w4", "w8"] })],
  });
  // Daily dosing at home: the discontinuation is logged on the day of the last dose, Day 41.
  assert.equal(row(run(m, { events: [{ key: "s", type: "stop", date: plus(40), answers: {} }] }), "fu").date, plus(70));
  assert.equal(row(run(m), "fu").date, plus(86));
});

test("a window the protocol does not state is kept apart from an exact day", () => {
  const m = base({ visits: [visit("day1", "d1", 0, { window_stated: false }), visit("w2", "d1", 14)] });
  const s = run(m);
  assert.equal(row(s, "day1").windowStated, false);
  assert.equal(row(s, "w2").windowStated, true);
  const meta = protocolTimeline(m, s, START, () => null).sections.flatMap((x) => x.rows).map((r) => r.meta);
  assert.match(meta[0], /window not stated/);
  assert.match(meta[1], /exact day/);
});

test("open points are listed worst first, with names for what they concern and a tag on the timeline", () => {
  const m = base({
    visits: [visit("w2", "d1", 14), visit("w4", "d1", 28)],
    rules: [rule("stop_rule", { anchor: "d1" })],
    open_points: [
      { id: "a", kind: "not_stated", text: "No window for Week 2", why: "", used: "", quote: "", page: null, affects: ["w2"], stage: "model" },
      { id: "b", kind: "conflict", text: "ECG: table and text differ", why: "", used: "The table is followed.", quote: "ECGs are recorded at Week 24",
        page: 7, affects: ["w4", "w2", "nope"], stage: "tables", found: true, found_on: 8 },
      { id: "c", kind: "cannot_represent", text: "Calendar months", why: "", used: "30 days", quote: "once every calendar month", page: 2,
        affects: ["stop_rule"], stage: "model", found: false },
    ],
  });
  const views = openPointViews(m);
  assert.deepEqual(views.map((p) => p.id), ["b", "c", "a"]);
  assert.deepEqual(views[0].affects, ["W4", "W2"]);
  assert.equal(views[0].page, "p. 8");
  assert.match(views[1].page!, /could not find this passage/);
  assert.deepEqual(views[1].affects, ["stop_rule"]);
  assert.deepEqual(views[1].visitIds, []);
  assert.deepEqual(openPointsByVisit(m), { w4: 1, w2: 2 });
  const tags = protocolTimeline(m, run(m), START, () => null).sections.flatMap((x) => x.rows).map((r) => r.tags.map((t) => t.text));
  assert.deepEqual(tags, [["2 to check"], ["1 to check"]]);
  // The reviewer's ticks are stored on the model; a model without open points is returned as it is.
  assert.deepEqual(withCheckedPoints(m, { b: true }).open_points.map((p: any) => !!p.checked), [false, true, false]);
  assert.equal(openPointViews(withCheckedPoints(m, { b: true }))[0].checked, true);
  const plain = base({ visits: [visit("w2", "d1", 14)] });
  assert.equal(withCheckedPoints(plain, { b: true }), plain);
  assert.deepEqual(openPointViews(plain), []);
});
