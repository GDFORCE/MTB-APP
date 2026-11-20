// How a confirmed study-wide event changes one participant's calendar, on the synthetic P16-style
// fixture (backend/tests/fixtures/kit_synthetic/p16_study_closeout.raw.json). The server decides who
// is affected and stores the effect; these check the calendar the staff and patient screens compute
// from it. Expected dates are worked out by hand: randomization 5 Jan 2026, Visit 8 = Day 259.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { kitVisitRows } from "../src/features/kit/patientRows.ts";

const model = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/kit_synthetic/p16_study_closeout.model.json", import.meta.url), "utf8"));
const inExtension = { "v3#1": "2026-01-19", "v4#1": "2026-02-02", "v5#1": "2026-03-02", "v6#1": "2026-03-30", "v7#1": "2026-04-06" };
const effect = (over: Record<string, unknown> = {}) => ({
  id: "close-2026:p1", event: "final_v6", action: "early_close", label: "Final participant in the study completed Visit 6",
  effective: "2026-06-01", state: "applied", booked: null, ...over,
});
const part = (effects: unknown[], actuals: Record<string, string> = inExtension) =>
  ({ startAnchor: "rand", startDate: "2026-01-05", answers: {}, events: [], actuals, today: "2026-06-03", studyEffects: effects });
const run = (p: any) => computeKitSchedule(model, {}, p);
const row = (s: any, key: string) => s.rows.find((r: any) => r.key === key);
const patientRows = (p: any) => kitVisitRows({ model, site_answers: {}, participant: p, workflow_version: 2,
  patient: { id: "p1", trial_id: "t" }, trial: {} } as any);
const TASK = "se.final_v6.early_eos#1";

test("without an effect, routine Visit 8 stays on Day 259", () => {
  const s = run(part([]));
  assert.equal(row(s, "v8#1").planned, "2026-09-21");
  assert.equal(row(s, TASK), undefined);
});

test("an applied closeout supersedes Visit 8 and asks the site for the next available date", () => {
  const s = run(part([effect()]));
  assert.equal(row(s, "v8#1"), undefined);
  const [group] = s.stoppedBy;
  assert.equal(group.label, "Final participant in the study completed Visit 6");
  assert.deepEqual(group.visits.map((v: any) => v.key), ["v8#1"]);
  const task = row(s, TASK);
  assert.equal(task.status, "waiting");
  assert.equal(task.planned, null);
  assert.deepEqual(task.needs.map((n: any) => [n.input, n.roles]), [["booking", ["pi", "crc"]]]);
  assert.deepEqual(task.procedures.map((p: any) => p.name), ["Recurrence assessment", "Partial Mayo score"]);
  assert.equal(task.studyEffect.state, "applied");
  assert.ok(!patientRows(part([effect()])).some((r: any) => r.id === TASK), "the patient sees no undated task");
});

test("a booking dates the closeout for staff and patient alike", () => {
  const p = part([effect({ booked: "2026-06-12" })]);
  const task = row(run(p), TASK);
  assert.deepEqual([task.status, task.planned, task.booked, task.needs], ["scheduled", "2026-06-12", "2026-06-12", undefined]);
  const seen = patientRows(p).find((r: any) => r.id === TASK);
  assert.equal(seen.scheduled_date, "2026-06-12");
  assert.ok(!patientRows(p).some((r: any) => r.id === "v8#1"));
});

test("the final participant keeps Visit 7 and loses only the extension end", () => {
  const actuals = { "v3#1": "2026-03-16", "v4#1": "2026-03-30", "v5#1": "2026-04-27", "v6#1": "2026-05-26" };
  const s = computeKitSchedule(model, {}, { startAnchor: "rand", startDate: "2026-03-02", answers: {}, events: [], actuals, today: "2026-06-01",
    studyEffects: [effect({ id: "close-2026:final", action: "final_participant" })] } as any);
  assert.equal(row(s, "v7#1").planned, "2026-06-01");
  assert.equal(row(s, "v8#1"), undefined);
  assert.equal(row(s, TASK), undefined);
});

test("a recorded Visit 8 is never superseded", () => {
  const s = run(part([effect()], { ...inExtension, "v8#1": "2026-05-20" }));
  assert.equal(row(s, "v8#1").actual, "2026-05-20");
});

test("after a correction removes the participant, a booked closeout stays visible for review and Visit 8 returns", () => {
  const s = run(part([effect({ state: "review", booked: "2026-06-12" })]));
  assert.equal(row(s, "v8#1").planned, "2026-09-21");
  const task = row(s, TASK);
  assert.equal(task.booked, "2026-06-12");
  assert.ok(task.notes.some((n: string) => n.includes("Review this booking")));
  const withdrawn = run(part([effect({ state: "withdrawn", booked: null })]));
  assert.equal(row(withdrawn, TASK), undefined);
  assert.equal(row(withdrawn, "v8#1").planned, "2026-09-21");
});

test("an effect from a version without the action changes nothing and says so", () => {
  const s = run(part([effect({ action: "not_in_this_version" })]));
  assert.equal(row(s, "v8#1").planned, "2026-09-21");
  assert.ok(s.warnings.some((w: string) => w.includes("no matching action")));
});
