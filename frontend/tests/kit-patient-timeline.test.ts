import assert from "node:assert/strict";
import test from "node:test";

import { patientTimeline } from "../src/features/kit/patientSections.ts";

const v = (key: string, code: string, phase: string, date: string | null, over: Record<string, unknown> = {}) => ({
  type: "visit", key, visitId: key.split("#")[0], code, name: code, phase, modality: "site",
  status: date == null ? "waiting" : "scheduled", date, planned: date, actual: null, relative: date == null ? "Surgery + 30 d" : null,
  windowStart: date, windowEnd: date, windowDays: [0, 0], dependsOnDecision: false, fromEvent: null, until: null, ...over,
});
const model = {
  visits: [
    { id: "c1d1", anchor: "start", follows: null }, { id: "c2d1", anchor: "start", follows: null }, { id: "c3d1", anchor: "start", follows: null },
    { id: "postop", anchor: "surgery", follows: null }, { id: "eot", anchor: "start", follows: null },
  ],
  anchors: [{ id: "start", kind: "start", label: "Start" }, { id: "surgery", kind: "ask", label: "Surgery" }],
  events: [{ id: "pd", label: "Progression", ends_schedule: true, visits: [], questions: [] }],
} as any;
const run = (current: any[], over: Record<string, any> = {}) => patientTimeline({
  model, participant: { startAnchor: "start", startDate: "2026-10-23", answers: {}, events: [] } as any,
  current: { rows: current } as any, withoutEvents: { rows: current } as any, baseline: { rows: current } as any,
  today: "2026-12-05", conditionOf: () => null, ...over,
});

test("done, late, moved, next and overdue visits are told apart", () => {
  const current = [
    v("c1d1#1", "C1D1", "Cycle 1", "2026-10-23", { status: "done", actual: "2026-10-23" }),
    v("c2d1#1", "C2D1", "Cycle 2", "2026-11-20", { status: "done", planned: "2026-11-13", actual: "2026-11-20" }),
    v("c3d1#1", "C3D1", "Cycle 3", "2026-12-11", { windowStart: "2026-12-08", windowEnd: "2026-12-14" }),
  ];
  const baseline = [current[0], { ...current[1], date: "2026-11-13" }, { ...current[2], date: "2026-12-04" }];
  const t = run(current, { baseline: { rows: baseline } });
  const rows = t.sections.flatMap((s) => s.rows);
  assert.equal(rows[0].dot, "done");
  assert.deepEqual(rows[1].tags.map((x) => x.text), ["+7 days"]);
  assert.equal(rows[2].dot, "next");
  assert.deepEqual(rows[2].tags.map((x) => x.text), ["Moved · was 4 Dec"]);
  assert.equal(rows[0].year, "2026");
  assert.equal(rows[1].year, null);
  assert.deepEqual([t.done, t.total, t.late, t.moved, t.overdue], [2, 3, 1, 1, 0]);
  assert.equal(run([v("c3d1#1", "C3D1", "Cycle 3", "2026-11-01")]).overdue, 1);
});

test("an event shows on the line and the visits it cancelled are kept, crossed out", () => {
  const before = [v("c3d1#1", "C3D1", "Treatment", "2027-01-01"), v("c3d1#2", "C4D1", "Treatment", "2027-01-22")];
  const after = [before[0], v("eot#1", "EOT", "After treatment", "2027-01-20", { fromEvent: "Progression · 15 Jan" })];
  const t = run(after, {
    participant: { startAnchor: "start", startDate: "2026-10-23", answers: {}, events: [{ key: "e1", type: "pd", date: "2027-01-15", answers: {} }] },
    current: { rows: after, ended: { event: "pd", date: "2027-01-15", cancelled: 1 } }, withoutEvents: { rows: before },
  });
  const rows = t.sections.flatMap((s) => s.rows);
  assert.deepEqual(rows.map((r) => [r.kind, r.date]), [
    ["visit", "2027-01-01"], ["event", "2027-01-15"], ["cancelled", "2027-01-15"], ["visit", "2027-01-20"],
  ]);
  assert.equal(rows[0].year, "2027");
  assert.equal(rows[2].name, "1 planned visit cancelled");
  assert.equal(rows[2].meta, "C4D1");
  assert.equal(rows[2].dot, "struck");
  assert.ok(rows[3].tags.some((x) => x.text === "Added by Progression"));
  assert.deepEqual(t.ended, { label: "Progression", date: "2027-01-15" });
  assert.equal(t.cancelled, 1);
  // The event and the cancelled row sit in the phase before them.
  assert.deepEqual(t.sections.map((s) => s.label), ["Treatment", "After treatment"]);
});

test("visits waiting for a date name the date they wait for; decisions keep their answer", () => {
  const current = [
    v("c1d1#1", "C1", "Before surgery", "2026-10-23", { status: "done", actual: "2026-10-23" }),
    { type: "decision", key: "d1", questionId: "ok", text: "Run-in successful?", at: "Week 4", date: "2026-11-16",
      options: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }], answer: "yes" },
    v("postop#1", "POST", "After surgery", null),
  ];
  const t = run(current);
  const rows = t.sections.flatMap((s) => s.rows);
  assert.equal(rows[1].meta, "Answered: Yes");
  assert.equal(rows[2].dot, "wait");
  assert.equal(rows[2].waitingAnchor, "surgery");
  assert.deepEqual(t.pendingAnchors, [{ id: "surgery", label: "Surgery", waiting: 1 }]);
  assert.equal(t.sections[1].range, "Waits for a date · 1 visit");
  assert.equal(t.sections[0].range, "Oct 2026 – Nov 2026 · all 1 done");
});
