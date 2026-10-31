// "Stool ova and parasite examination for patients with an eosinophil count >2 × ULN AND risk
// factors" (BP11-301): questions asked at Screening decide whether the test is done there.
import assert from "node:assert/strict";
import test from "node:test";

import { computeKitSchedule } from "../src/features/kit/index.ts";

const MODEL = {
  protocol: {}, arms: [], review_questions: [], notes: [], rules: [], events: [],
  anchors: [{ id: "scr", label: "Screening", kind: "start", relative_to: null, offset_days: 0, is_day1: false, day_zero: false, set_by_visit: null, estimates: [], range: null }],
  questions: [
    { id: "eos_uln", label: "Eosinophils", text: "Eosinophil count (× ULN)?", help: "From the screening labs", kind: "number", options: [], scope: "decision", ask_at: { visit: "screen", n: null } },
    { id: "parasite_risk", label: "Parasite risk", text: "Any parasite risk factor?", help: "Endemic area in the last 6 months, chronic GI symptoms, chronic immunosuppression", kind: "boolean", options: [], scope: "decision", ask_at: { visit: "screen", n: null } },
  ],
  visits: [{
    id: "screen", code: "SCR", name: "Screening", phase: "Screening", modality: "site", anchor: "scr", offset_days: 0, window_before: 0, window_after: 0,
    follows: null, include_if: [], repeat: null, timepoints: [], duration_days: null, condition: null,
    procedures: [{ name: "Vital signs", if: [] }, { name: "Stool ova and parasites", if: [{ question: "eos_uln", op: ">", value: 2 }, { question: "parasite_risk", in: ["yes"] }] }],
  }],
} as any;
const run = (answers: Record<string, string> = {}) =>
  computeKitSchedule(MODEL, {}, { startAnchor: "scr", startDate: "2026-01-05", answers } as any);
const stool = (s: any) => s.rows.find((r: any) => r.type === "visit").procedures.find((p: any) => p.name === "Stool ova and parasites") || null;

test("both questions are asked at Screening; until answered the test shows as depending on them", () => {
  const s = run();
  const asked = s.rows.filter((r: any) => r.type === "decision");
  assert.deepEqual(asked.map((r: any) => [r.questionId, r.kind, r.date]), [["eos_uln", "number", "2026-01-05"], ["parasite_risk", "boolean", "2026-01-05"]]);
  assert.equal(stool(s).conditional, true);
});

test("the answers decide: done only above 2 × ULN with a risk factor", () => {
  assert.equal(stool(run({ eos_uln: "2.5", parasite_risk: "yes" })).conditional, false);
  assert.equal(stool(run({ eos_uln: "1.5", parasite_risk: "yes" })), null);
  assert.equal(stool(run({ eos_uln: "2.5", parasite_risk: "no" })), null);
  assert.equal(stool(run({ eos_uln: "2", parasite_risk: "yes" })), null, "exactly 2 × ULN is not above it");
});
