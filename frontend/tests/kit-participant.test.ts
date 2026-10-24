import assert from "node:assert/strict";
import test from "node:test";

import {
  answer, dmyToIso, formatDmyInput, isOverdue, isoToDmy, kitStatus, logEvent, nextVisit, progress, recordVisit, removeEvent, setAnchorDate,
} from "../src/features/kit/participant.ts";

const base = { startAnchor: "screening", startDate: "2026-11-02", answers: { seq: "AB" }, events: [], actuals: {}, doseTimes: {} };

test("recording and clearing a visit date leaves the rest of the record alone", () => {
  const done = recordVisit(base, "scr#1", "2026-11-03");
  assert.deepEqual(done.actuals, { "scr#1": "2026-11-03" });
  assert.deepEqual(base.actuals, {});
  assert.deepEqual(recordVisit(done, "scr#1", null).actuals, {});
});

test("answers, anchor dates and events are added and removed", () => {
  const p = setAnchorDate(answer(base, "runin_ok", "yes"), "surgery", "2027-01-28");
  assert.deepEqual(p.answers, { seq: "AB", runin_ok: "yes", "@surgery": "2027-01-28" });
  assert.deepEqual(answer(p, "runin_ok", null).answers, { seq: "AB", "@surgery": "2027-01-28" });
  const logged = logEvent(p, { type: "pd", date: "2027-02-15", answers: {} }, "k1");
  assert.equal(logged.events.length, 1);
  assert.equal(removeEvent(logged, "k1").events.length, 0);
});

const row = (over: Record<string, unknown>) => ({
  type: "visit", key: "v#1", status: "scheduled", date: "2026-11-10", windowEnd: "2026-11-12", ...over,
}) as any;

test("next visit is the first still-open one; overdue means its window closed", () => {
  const schedule = { rows: [row({ key: "a", status: "done" }), row({ key: "b", date: "2026-11-01", windowEnd: "2026-11-03" }), row({ key: "c" })] } as any;
  assert.equal(nextVisit(schedule, "2026-11-05")?.key, "c");
  assert.equal(isOverdue(schedule.rows[1], "2026-11-05"), true);
  assert.equal(isOverdue(schedule.rows[2], "2026-11-05"), false);
  assert.deepEqual(progress(schedule), { done: 1, total: 3 });
});

test("form dates are DD/MM/YYYY and must exist", () => {
  assert.equal(formatDmyInput("02112026"), "02/11/2026");
  assert.equal(dmyToIso("02/11/2026"), "2026-11-02");
  assert.equal(dmyToIso("30/02/2026"), null);
  assert.equal(isoToDmy("2026-11-02"), "02/11/2026");
});

test("list status: overdue beats active; an ended or fully done schedule is completed", () => {
  const today = "2026-11-05";
  assert.equal(kitStatus({ rows: [row({ date: "2026-11-01", windowEnd: "2026-11-02" })] } as any, today), "overdue");
  assert.equal(kitStatus({ rows: [row({})] } as any, today), "active");
  assert.equal(kitStatus({ rows: [row({ status: "done" })] } as any, today), "completed");
  assert.equal(kitStatus({ rows: [row({})], ended: { event: "wd", date: today, cancelled: 1 } } as any, today), "completed");
  assert.equal(kitStatus({ rows: [] } as any, today), "no_visits");
});
