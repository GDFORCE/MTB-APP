import assert from "node:assert/strict";
import test from "node:test";
import { conflictSummary, emptyConflictForm, evidenceLocation, resolveConflictBody, type Conflict } from "../src/features/uctsm/conflicts.ts";

const evidence = { evidence_id: "e1", type: "TABLE", page: 4, section: "Schedule of assessments", table: "Table 2",
  row: "C1D1", column: "Day", quote: "Treatment starts on Day 1 of each cycle." };
const conflict: Conflict = { event_id: "ev", event_code: "C1D1", display_name: "Cycle 1 Day 1", status: "CONFLICTING",
  evidence: [evidence, { ...evidence, evidence_id: "e2", quote: "  " }], missing_evidence_ids: [], resolvable: true };
const form = { chosen: ["e1"], reading: " Day 1 of cycle ", reason: " Table row and text agree " };

test("a resolution needs supporting evidence belonging to the visit, a reading and a reason", () => {
  assert.throws(() => resolveConflictBody(conflict, emptyConflictForm()));
  for (const change of [{ chosen: [] }, { chosen: ["foreign"] }, { chosen: ["e2"] }, { reading: "x" }, { reason: " " }]) {
    assert.throws(() => resolveConflictBody(conflict, { ...form, ...change }));
  }
  assert.throws(() => resolveConflictBody({ ...conflict, resolvable: false }, form), /no quoted protocol text/);
});

test("the request body carries only evidence ids, the reading and the reason", () => {
  assert.deepEqual(resolveConflictBody(conflict, { ...form, chosen: ["e1", "e1"] }), {
    chosen_evidence_ids: ["e1"], confirmed_reading: "Day 1 of cycle", reason: "Table row and text agree" });
});

test("evidence locations never invent a missing page or section", () => {
  assert.equal(evidenceLocation(evidence), "Page 4 · Schedule of assessments · Table Table 2 · Row C1D1 · Column Day");
  assert.equal(evidenceLocation({ ...evidence, page: null, section: null, table: null, row: null, column: null }),
    "Page not stated · Section not stated");
});

test("the summary says approval stays blocked and counts visits that cannot be resolved", () => {
  assert.equal(conflictSummary([]), "No unresolved interpretations.");
  assert.match(conflictSummary([conflict]), /1 unresolved interpretation\. Approval stays blocked/);
  assert.match(conflictSummary([conflict, { ...conflict, event_id: "x", resolvable: false }]),
    /2 unresolved interpretations, 1 without quoted protocol text/);
});
