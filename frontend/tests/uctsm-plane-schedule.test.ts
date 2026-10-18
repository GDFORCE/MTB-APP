import assert from "node:assert/strict";
import test from "node:test";

import { describeWindow, mergePage, toPlaneRows, type PlaneEntry } from "../src/features/uctsm/planeSchedule.ts";

const entry = (over: Partial<PlaneEntry> = {}): PlaneEntry => ({
  logical_key: "IMAGING_BASELINE:0", event_code: "IMAGING_BASELINE", status: "RESOLVED", completed: false,
  planned_start: "2026-01-31", earliest: "2026-01-28", latest: "2026-02-03", window_known: true,
  original_planned: "2026-01-31", appointment_date: null, appointment_outside_window: null,
  override_target: null, override_window_earliest: null, override_window_latest: null,
  override_outside_window: null, override_authority: null, override_reason: null, cancelled: false,
  actual_visit_date: null, actual_treatment_date: null, track_name: "Imaging", track_code: "IMAGING",
  provisional: false, evaluation_id: "ev-1", ...over,
});

test("protocol target, appointment, exception and actuals stay separate", () => {
  const [row] = toPlaneRows([entry({
    appointment_date: "2026-02-02", appointment_outside_window: false,
    override_target: "2026-02-20", override_authority: "PI Dr A", override_outside_window: true,
    actual_visit_date: "2026-02-21" })]);
  assert.equal(row.protocolTarget, "2026-01-31");
  assert.equal(row.appointment, "2026-02-02");
  assert.match(row.exception, /date 2026-02-20, authorised by PI Dr A/);
  assert.equal(row.actualVisit, "2026-02-21");
  assert.deepEqual(row.flags, ["Exception is outside the protocol window"]);
});

test("an unknown window is never rendered as a zero-width one", () => {
  assert.equal(describeWindow({ window_known: false, earliest: null, latest: null }), "No window stated");
  assert.equal(describeWindow({ window_known: true, earliest: "2026-01-31", latest: "2026-01-31" }),
    "exactly 2026-01-31");
  const [row] = toPlaneRows([entry({ window_known: false, earliest: null, latest: null,
    appointment_date: "2026-02-02" })]);
  assert.deepEqual(row.flags, ["Appointment cannot be checked: the protocol states no window"]);
});

test("a retried page never duplicates a patient", () => {
  const one = [{ external_patient_id: "A", patient_code: "A" }];
  const merged = mergePage(one, { items: [{ external_patient_id: "A", patient_code: "A" },
    { external_patient_id: "B", patient_code: "B" }], next_cursor: null });
  assert.deepEqual(merged.map((item) => item.external_patient_id), ["A", "B"]);
});
