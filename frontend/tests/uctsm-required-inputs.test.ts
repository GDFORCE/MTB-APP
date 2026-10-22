import assert from "node:assert/strict";
import test from "node:test";
import {
  acknowledgeBody, buildEnrolmentInputs, customBody, emptyCustomForm, emptyEntry, enrolmentReady, entryText,
  reviewNotice, type RequiredInput, type RequiredInputsView,
} from "../src/features/uctsm/requiredInputs.ts";

const surgery: RequiredInput = { code: "ANCHOR:SURGERY", label: "Surgery date", kind: "DATE", source: "DERIVED", reason: "", anchor_code: "SURGERY" };
const dose: RequiredInput = { code: "ANCHOR:DOSE_TIME", label: "Dose time", kind: "DATETIME", source: "DERIVED", reason: "", anchor_code: "DOSE_TIME" };
const weight: RequiredInput = { code: "CUSTOM:WEIGHT", label: "Weight (kg)", kind: "NUMBER", source: "CUSTOM", reason: "" };
const view = (over: Partial<RequiredInputsView> = {}): RequiredInputsView => ({
  schedule_version_id: "v", status: "IN_REVIEW", list_hash: "h", derived: [surgery, dose], custom: [weight],
  effective: [surgery, dose, weight], acknowledged: false, needs_acknowledgement: true, acknowledgement: null, ...over });

test("a custom entry is normalised and validated before it is sent", () => {
  assert.deepEqual(customBody({ code: " weight kg ", label: " Weight (kg) ", kind: "NUMBER", reason: " dosing " }),
    { code: "WEIGHT_KG", label: "Weight (kg)", kind: "NUMBER", reason: "dosing" });
  for (const bad of [{ code: "x" }, { code: "1ABC" }, { label: "x" }, { reason: " " }])
    assert.throws(() => customBody({ ...emptyCustomForm(), code: "WEIGHT", label: "Weight", reason: "dosing", ...bad }));
});

test("acknowledgement needs a reason and an editable version", () => {
  assert.throws(() => acknowledgeBody(view(), " "));
  assert.throws(() => acknowledgeBody(view({ status: "APPROVED" }), "reviewed"));
  assert.deepEqual(acknowledgeBody(view(), " reviewed the list "), { reason: "reviewed the list" });
});

test("the review notice names every required detail and says approval waits for acknowledgement", () => {
  assert.match(reviewNotice(view()), /Surgery date, Dose time, Weight \(kg\)\. Review and acknowledge/);
  assert.match(reviewNotice(view({ acknowledged: true })), /^Reviewed\./);
  assert.match(reviewNotice(view({ effective: [], needs_acknowledgement: false })), /needs no extra patient details/);
});

test("a date and time are combined with the IST offset and must both be valid", () => {
  assert.equal(entryText(dose, { value: "2026-03-10", time: "23:30", later: false }), "2026-03-10T23:30:00+05:30");
  assert.equal(entryText(dose, { value: "2026-03-10", time: "23:30", later: false }, "+00:00"), "2026-03-10T23:30:00+00:00");
  for (const bad of [{ value: "10/03/2026", time: "23:30" }, { value: "2026-03-10", time: "25:00" }, { value: "2026-03-10", time: "" }])
    assert.throws(() => entryText(dose, { ...bad, later: false }));
  assert.throws(() => entryText(surgery, { value: "2026-02-31x", time: "", later: false }));
  assert.throws(() => entryText(weight, { value: "heavy", time: "", later: false }));
  assert.equal(entryText(weight, { value: "62.5", time: "", later: false }), "62.5");
});

test("every entry is entered or explicitly deferred; nothing is skipped silently", () => {
  const entries = [surgery, dose, weight];
  assert.throws(() => buildEnrolmentInputs(entries, {}), /Surgery date.*Dose time.*Weight/s);
  assert.equal(enrolmentReady(entries, {}), false);
  const values = { [surgery.code]: { value: "2026-03-20", time: "", later: false },
    [dose.code]: { ...emptyEntry(), later: true }, [weight.code]: { value: "70", time: "", later: false } };
  assert.deepEqual(buildEnrolmentInputs(entries, values), {
    inputs: { "ANCHOR:SURGERY": "2026-03-20", "CUSTOM:WEIGHT": "70" }, deferred_inputs: ["ANCHOR:DOSE_TIME"] });
  assert.equal(enrolmentReady(entries, values), true);
});
