import assert from "node:assert/strict";
import test from "node:test";
import {
  cohortBody, cutoverMessage, emptyPrefs, parsePatientIds, prefsBody, readinessLines, reasonBody, reportLine,
  reportTotals, rollbackBody, snapshotBody, type ScheduleReport,
} from "../src/features/uctsm/operations.ts";

test("a reason is always required and trimmed", () => {
  assert.throws(() => reasonBody("  "));
  assert.deepEqual(reasonBody(" provider fixed "), { reason: "provider fixed" });
  assert.deepEqual(rollbackBody("rehearsal", true), { reason: "rehearsal", accept_plane_changes_loss: true });
  assert.throws(() => rollbackBody("", false));
});

test("patient ids are split on commas, spaces and lines, de-duplicated and capped", () => {
  assert.deepEqual(parsePatientIds("A1, A2\nA1;A3  "), ["A1", "A2", "A3"]);
  assert.throws(() => parsePatientIds(" , "));
  assert.throws(() => parsePatientIds(Array.from({ length: 201 }, (_, i) => `P${i}`).join(",")));
  assert.deepEqual(cohortBody(" pilot ", "A1"), { name: "pilot", external_patient_ids: ["A1"] });
  assert.throws(() => cohortBody(" ", "A1"));
});

test("readiness is explained per patient in plain words", () => {
  assert.deepEqual(readinessLines({ ready: true, state: "SHADOW", patients: [{ external_patient_id: "A", ready: true, reasons: [] }] }), ["Every patient is ready."]);
  assert.deepEqual(readinessLines({ ready: false, state: "SHADOW", patients: [
    { external_patient_id: "B", ready: false, reasons: ["MAPPING_NOT_RECONCILED", "PARITY_NOT_MATCH"] }] }),
    ["B is not reconciled with the old schedule; the two systems do not agree on its dates."]);
  assert.deepEqual(readinessLines({ ready: false, state: "SHADOW", patients: [] }), ["This cohort has no patients."]);
});

test("cutover refusals become actionable sentences without leaking details", () => {
  const refuse = (status: number, detail?: unknown) => cutoverMessage({ response: { status, data: { detail } } });
  assert.match(refuse(403), /cutover grant/);
  assert.match(refuse(409, { code: "MAKER_CHECKER_SAME_PERSON" }), /different person/);
  assert.match(refuse(409, { code: "PLANE_CHANGES_WOULD_BE_LOST", details: { commands: 2, occurrences: 1 } }), /2 commands, 1 visits/);
  assert.match(refuse(409, { code: "SOMETHING_NEW" }), /could not be completed/);
});

test("preferences are validated exactly as the server will, with the Indian zone pre-filled", () => {
  const form = { ...emptyPrefs(), consent: "GRANTED" as const };
  assert.equal(emptyPrefs().timezone, "Asia/Kolkata");
  assert.deepEqual(prefsBody(form), { consent: "GRANTED", timezone: "Asia/Kolkata", delivery_time: "09:00", lead_days: 1,
    quiet_start: null, quiet_end: null });
  for (const bad of [{ consent: "" }, { delivery_time: "9am" }, { lead_days: "31" }, { lead_days: "1.5" },
    { quiet_start: "22:00" }, { quiet_start: "22:00", quiet_end: "22:00" }, { timezone: " " }])
    assert.throws(() => prefsBody({ ...form, ...bad } as never));
  assert.equal(prefsBody({ ...form, quiet_start: "22:00", quiet_end: "07:00" }).quiet_end, "07:00");
});

test("report lines state target, window, actual and the window verdict only when known", () => {
  const row = { patient_code: "P1", visit: "Cycle 1 Day 1", status: "RESOLVED", target_date: "2026-03-20", window_earliest: "2026-03-18",
    window_latest: "2026-03-22", actual_visit_date: "2026-03-23", deviation_days: 3, outside_window: true };
  assert.equal(reportLine(row), "P1 · Cycle 1 Day 1 · target 2026-03-20 (2026-03-18 to 2026-03-22) · done 2026-03-23, +3 d, OUTSIDE window");
  assert.equal(reportLine({ ...row, target_date: null, window_earliest: null, window_latest: null, actual_visit_date: null,
    deviation_days: null, outside_window: null }), "P1 · Cycle 1 Day 1 · target not scheduled · not done");
  const report: ScheduleReport = { profile: "STAFF", next_cursor: null, patients: [{ patient_code: "P1", rows: [row, { ...row, actual_visit_date: null, outside_window: null }] }] };
  assert.deepEqual(reportTotals(report), { patients: 1, visits: 2, outside: 1, done: 1 });
});

test("a promotion needs the pasted snapshot report of a completed encrypted snapshot", () => {
  const report = { status: "ENCRYPTED_LOGICAL_SNAPSHOT", manifest_hash: "a".repeat(64), encrypted_file_hash: "b".repeat(64),
    created_at: "2026-10-16T11:00:00+00:00", collections: 40, documents: 12 };
  assert.deepEqual(snapshotBody(JSON.stringify(report)), { status: report.status, manifest_hash: report.manifest_hash,
    encrypted_file_hash: report.encrypted_file_hash, created_at: report.created_at });
  assert.throws(() => snapshotBody("not json"), /exactly as the snapshot command printed/);
  assert.throws(() => snapshotBody(JSON.stringify({ ...report, status: "FAILED" })), /not a completed/);
  assert.throws(() => snapshotBody(JSON.stringify({ ...report, created_at: "" })), /no created_at/);
  assert.match(cutoverMessage({ response: { status: 409, data: { detail: { code: "SNAPSHOT_STALE" } } } }), /too old/);
});
