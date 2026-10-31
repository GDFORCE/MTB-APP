// STEP 0 — frozen results. The schedules today's engine computes for the sample models and the
// constructed test models, across several patient records (no answers, answers, recorded dates,
// logged events), saved as JSON snapshots in tests/golden/schedules/. Every later step must keep
// this test passing: a model that doesn't use a new feature gives exactly the same schedule.
//
// - A missing snapshot is written from the current engine and the test passes (refused when CI is set).
// - UPDATE_GOLDEN=1 rewrites every snapshot on purpose (check the diff before committing).
// - Otherwise each record's schedule must equal its snapshot exactly.
//
// The sample models are read from tests/golden/models/, written by the backend golden test
// (backend/tests/test_kit_golden.py) from backend/app/kit/samples as the backend saves them.
// Run that one first.
import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";

import { computeKitSchedule } from "../src/features/kit/index.ts";
import { participantSummary } from "../src/features/kit/describe.ts";
import { anchorPositions, enrolmentAnchors } from "../src/features/kit/participant.ts";
import { parseD } from "../src/features/kit/utils.ts";
import { GOLDEN_CASES, SAMPLE_RECORDS } from "./golden/kit-golden-models.ts";
import type { GoldenCase, GoldenRecord } from "./golden/kit-golden-models.ts";

const GOLDEN = new URL("./golden/", import.meta.url);
const MODELS = new URL("models/", GOLDEN);
const SCHEDULES = new URL("schedules/", GOLDEN);
const UPDATE = process.env.UPDATE_GOLDEN === "1";

// Output fields a later step adds to the serialized schedule. List each one here, with its step,
// instead of rewriting the snapshots: everything that existed at step 0 stays compared exactly.
// A later step that changes an existing field's value for an old model is a regression.
const ADDED_SCHEDULE_KEYS: string[] = [];
// "check": a visit's yes/no check per occurrence (null on every old model), added 29 Oct 2026.
const ADDED_ROW_KEYS: string[] = ["check", "doses", "time", "timeKind", "kind"];

/** JSON copy of a computed schedule without the fields added after step 0. */
function goldenView(schedule: unknown) {
  const s = JSON.parse(JSON.stringify(schedule));
  for (const k of ADDED_SCHEDULE_KEYS) delete s[k];
  for (const row of s.rows) for (const k of ADDED_ROW_KEYS) delete row[k];
  return s;
}

/** Everything the snapshot holds for one model: its enrolment dates and, per record, the schedule
 *  and the patient-list summary (next visit, what is needed). */
function snapshotOf(c: GoldenCase) {
  const site = c.site || {};
  const records: Record<string, unknown> = {};
  for (const [name, record] of Object.entries(c.records)) {
    const part = { events: [], actuals: {}, doseTimes: {}, ...record };
    records[name] = {
      schedule: goldenView(computeKitSchedule(c.model, site, part as any)),
      summary: participantSummary(c.model, site, part, parseD(record.today)),
    };
  }
  return JSON.parse(JSON.stringify({
    case: c.name,
    enrolmentAnchors: enrolmentAnchors(c.model).map((a: { id: string }) => a.id),
    anchorPositions: anchorPositions(c.model),
    records,
  }));
}

const emptyRecord = (model: any): GoldenRecord => ({
  startAnchor: model.anchors.find((a: { kind: string }) => a.kind === "start").id, startDate: "2026-11-02", answers: {}, today: "2026-11-02",
});

/** The sample models as the backend saved them, with their records. */
function sampleCases(): GoldenCase[] {
  if (!existsSync(MODELS)) return [];
  return readdirSync(MODELS).filter((f) => f.endsWith(".json")).sort().map((f) => {
    const saved = JSON.parse(readFileSync(new URL(f, MODELS), "utf8"));
    return { name: `sample-${saved.name}`, model: saved.model, site: saved.siteAnswers || {},
      records: SAMPLE_RECORDS[saved.name] || { empty: emptyRecord(saved.model) } };
  });
}
const SAMPLES = sampleCases();
const CASES = [...SAMPLES, ...GOLDEN_CASES];
const sample = (name: string) => SAMPLES.find((c) => c.name === `sample-${name}`);

test("the backend has written the saved sample models", () => {
  const names = SAMPLES.map((c) => c.name);
  assert.ok(["sample-arc", "sample-helix", "sample-orion"].every((n) => names.includes(n)),
    "frontend/tests/golden/models/{arc,helix,orion}.json are missing: run the backend golden test first "
    + "(in backend/: python -m pytest -q tests/test_kit_golden.py).");
});

for (const c of CASES) {
  test(`golden schedules: ${c.name}`, (t) => {
    const file = new URL(`${c.name}.json`, SCHEDULES);
    const current = snapshotOf(c);
    if (UPDATE || !existsSync(file)) {
      assert.ok(UPDATE || !process.env.CI, `${c.name}.json is missing; create it locally (run the test once) and commit it.`);
      mkdirSync(SCHEDULES, { recursive: true });
      writeFileSync(file, JSON.stringify(current, null, 2) + "\n");
      t.diagnostic(`wrote tests/golden/schedules/${c.name}.json`);
      return;
    }
    const stored = JSON.parse(readFileSync(file, "utf8"));
    const again = "If this change is intended, rerun with UPDATE_GOLDEN=1 and review the snapshot diff.";
    assert.deepStrictEqual(Object.keys(current.records), Object.keys(stored.records), `${c.name}: the list of records changed. ${again}`);
    assert.deepStrictEqual(current.enrolmentAnchors, stored.enrolmentAnchors, `${c.name}: the dates asked at Add Patient changed. ${again}`);
    assert.deepStrictEqual(current.anchorPositions, stored.anchorPositions, `${c.name}: the planned anchor positions changed. ${again}`);
    for (const name of Object.keys(stored.records)) {
      assert.deepStrictEqual(current.records[name], stored.records[name], `${c.name} / ${name}: the schedule changed. ${again}`);
    }
  });
}

// ── Checks on the harness itself ──────────────────────────────────────────────────────────────────

test("every snapshot is the same when computed twice (nothing depends on the clock or on run order)", () => {
  for (const c of CASES) assert.deepStrictEqual(snapshotOf(c), snapshotOf(c), c.name);
});

test("every record has a fixed today and a known start anchor", () => {
  for (const c of CASES) {
    const start = c.model.anchors.find((a: { kind: string }) => a.kind === "start")?.id;
    for (const [name, r] of Object.entries(c.records)) {
      assert.match(r.today, /^\d{4}-\d{2}-\d{2}$/, `${c.name} / ${name}`);
      assert.equal(r.startAnchor, start, `${c.name} / ${name}`);
    }
  }
});

test("a changed date is caught by the comparison", () => {
  const c = GOLDEN_CASES.find((x) => x.name === "test-rules-kcp")!;
  const snap = snapshotOf(c);
  const changed = JSON.parse(JSON.stringify(snap));
  changed.records["progression-day70"].schedule.rows.find((r: any) => r.code === "SAFETY").date = "2026-04-12";
  assert.throws(() => assert.deepStrictEqual(snapshotOf(c), changed));
});

// ── Dates worked out by hand, so the snapshots start from a correct engine ───────────────────────
const rowsOf = (c: GoldenCase | undefined, record: string) => {
  assert.ok(c, "sample model missing: run the backend golden test first");
  const s = computeKitSchedule(c!.model, c!.site || {}, { events: [], actuals: {}, doseTimes: {}, ...c!.records[record] } as any);
  return s.rows.filter((r: any) => r.type === "visit") as any[];
};
const byCode = (rows: any[], code: string) => rows.find((r) => r.code === code);

test("by hand: ARC with no answers yet (screening 2 Nov 2026, a Monday)", () => {
  const rows = rowsOf(sample("arc"), "empty");
  // Run-in = screening + 7 (lasts 14 days, +3 window); Period 1 = run-in + 14.
  assert.deepEqual([byCode(rows, "RUN").date, byCode(rows, "RUN").windowEnd, byCode(rows, "RUN").endDay], ["2026-11-09", "2026-11-12", "2026-11-22"]);
  assert.equal(byCode(rows, "P1D1").date, "2026-11-23");
  // P1D14 = P1D1 + 13 = Sun 6 Dec: nearest weekday in its ±2 window is Mon 7 Dec; it depends on the run-in decision.
  const d14 = byCode(rows, "P1D14");
  assert.deepEqual([d14.date, d14.weekend, d14.nearestWeekday, d14.dependsOnDecision], ["2026-12-06", true, "2026-12-07", true]);
  // P1D28 = P1D1 + 27 = Sun 20 Dec -> Mon 21 Dec. Period 2 is entered per patient (42-49 days), so it waits.
  assert.deepEqual([byCode(rows, "P1D28").date, byCode(rows, "P1D28").nearestWeekday], ["2026-12-20", "2026-12-21"]);
  assert.deepEqual([byCode(rows, "P2D1").status, byCode(rows, "P2D1").date], ["waiting", null]);
});

test("by hand: HELIX part A with Day 1 entered on 16 Nov 2026", () => {
  const rows = rowsOf(sample("helix"), "answered");
  const dates = ["C1D1", "C1D8", "C1D15", "C2D1", "C3D1", "C2D15", "C3D15", "TA1"].map((code) => byCode(rows, code)?.date);
  // C1D8/C1D15 = C1D1 + 7/14; C2D1 = C1D1 + 21, then every 28; C{n}D15 = C{n}D1 + 14; scans Day 1 + 56.
  assert.deepEqual(dates, ["2026-11-16", "2026-11-23", "2026-11-30", "2026-12-07", "2027-01-04", "2026-12-21", "2027-01-18", "2027-01-11"]);
});

test("by hand: ORION arm B with surgery entered on 10 Dec 2026", () => {
  const rows = rowsOf(sample("orion"), "arm-b-surgery");
  // Day 1 = screening + 14; pre-op = surgery − 7 (window −7); POD30 = surgery + 30; LTFU1 = surgery + 168.
  assert.equal(byCode(rows, "RAND").date, "2026-11-16");
  assert.deepEqual([byCode(rows, "PREOP").date, byCode(rows, "PREOP").windowStart], ["2026-12-03", "2026-11-26"]);
  assert.deepEqual([byCode(rows, "SURG").date, byCode(rows, "POD30").date, byCode(rows, "LTFU1").date], ["2026-12-10", "2027-01-09", "2027-05-27"]);
  // Adjuvant start is entered 28-56 days after surgery: until then arm B's cycles wait. Arm A's neoadjuvant cycles don't apply.
  assert.deepEqual([byCode(rows, "AC1D1").status, byCode(rows, "AC1D1").date], ["waiting", null]);
  assert.equal(rows.some((r) => r.visitId === "neo"), false);
});
