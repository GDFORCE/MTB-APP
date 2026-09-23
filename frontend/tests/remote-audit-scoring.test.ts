import assert from "node:assert/strict";
import test from "node:test";

import {
  canDecide,
  conditionalNeedsCapa,
  formatPercent,
  hasCriticalBlocker,
  normalizeDecision,
  normalizeScoreRun,
  numberOrNull,
  requiresOverrideReason,
  sortedBlockers,
} from "../src/features/remote-audit/scoring.ts";
import {
  consentPermitsJoin,
  normalizeSession,
} from "../src/features/remote-audit/virtualVisits.ts";

/**
 * Remote Audit — scoring, decision and virtual-visit presentation
 * (spec §12, §13, §16.11).
 *
 * The failures guarded here are all ones that would look fine on screen: a
 * "not scored" axis rendered as a 0% failing grade, a critical blocker sorted
 * below an informational one, an override path that doesn't ask for a reason,
 * or a join button offered before anyone consented.
 */

// ── Absent is not zero ──────────────────────────────────────────────────────

test("an absent score is null, never zero", () => {
  // NaN reaches the client as null or "NaN" depending on the JSON encoder.
  // Both mean "nothing applicable was scored" — 0% would be a catastrophic
  // failing grade for something that was never measured.
  assert.equal(numberOrNull(null), null);
  assert.equal(numberOrNull(undefined), null);
  assert.equal(numberOrNull("NaN"), null);
  assert.equal(numberOrNull(Number.NaN), null);
  assert.equal(numberOrNull(Infinity), null);
  assert.equal(numberOrNull(0), 0);
  assert.equal(numberOrNull(66.666), 66.666);
});

test("formatPercent says 'not scored' rather than printing 0%", () => {
  assert.equal(formatPercent(null), "not scored");
  assert.equal(formatPercent(0), "0%");
  assert.equal(formatPercent(66.666), "66.7%");
});

test("an unscored confidence axis stays null through normalization", () => {
  const run = normalizeScoreRun({
    compliance_percent: 100.0, evidence_confidence_percent: null,
    proposed_decision: "APPROVED",
  });
  assert.ok(run);
  assert.equal(run.compliancePercent, 100);
  // Absent, not zero — the screen must say "not scored", not "0%".
  assert.equal(run.evidenceConfidencePercent, null);
});

// ── Blockers before the score ───────────────────────────────────────────────

test("blockers sort most severe first", () => {
  const sorted = sortedBlockers([
    { code: "C", severity: "info", explanation: "", requirementIds: [] },
    { code: "A", severity: "critical", explanation: "", requirementIds: [] },
    { code: "B", severity: "medium", explanation: "", requirementIds: [] },
  ]);
  // The worst thing must be the first thing read.
  assert.deepEqual(sorted.map((b) => b.code), ["A", "B", "C"]);
});

test("an unknown severity sorts last rather than crashing the sort", () => {
  const sorted = sortedBlockers([
    { code: "WEIRD", severity: "catastrophic", explanation: "", requirementIds: [] },
    { code: "REAL", severity: "high", explanation: "", requirementIds: [] },
  ]);
  assert.deepEqual(sorted.map((b) => b.code), ["REAL", "WEIRD"]);
});

test("a critical blocker is detected", () => {
  assert.equal(hasCriticalBlocker([
    { code: "X", severity: "high", explanation: "", requirementIds: [] },
  ]), false);
  assert.equal(hasCriticalBlocker([
    { code: "X", severity: "critical", explanation: "", requirementIds: [] },
  ]), true);
});

test("blockers and their requirement ids survive normalization", () => {
  const run = normalizeScoreRun({
    proposed_decision: "NOT_APPROVED",
    blockers: [{ code: "CRITICAL_REQUIREMENT_FAILED", severity: "critical",
                 explanation: "A critical requirement was not met.",
                 requirement_ids: ["r1", "r2"] }],
  });
  assert.ok(run);
  assert.equal(run.blockers.length, 1);
  assert.deepEqual(run.blockers[0].requirementIds, ["r1", "r2"]);
});

test("a malformed response proposes REVIEW_REQUIRED, never APPROVED", () => {
  // Defaulting to APPROVED would let a broken response read as a sign-off.
  const run = normalizeScoreRun({});
  assert.ok(run);
  assert.equal(run.proposedDecision, "REVIEW_REQUIRED");
  assert.deepEqual(run.blockers, []);
});

test("an absent score run is null, not an empty pass", () => {
  assert.equal(normalizeScoreRun(null), null);
  assert.equal(normalizeScoreRun(undefined), null);
});

// ── Sections ────────────────────────────────────────────────────────────────

test("an all-N/A section normalizes to null rather than 0%", () => {
  const run = normalizeScoreRun({
    sections: [{ section_id: "equipment", weight_percent: 40, compliance_percent: null,
                 not_applicable_count: 3 }],
  });
  assert.ok(run);
  // "not applicable" is not "failed everything".
  assert.equal(run.sections[0].compliancePercent, null);
  assert.equal(run.sections[0].displayPercent, null);
  assert.equal(run.sections[0].notApplicableCount, 3);
});

test("the displayed section percent is rounded but the raw one is not", () => {
  const run = normalizeScoreRun({
    sections: [{ section_id: "f", weight_percent: 100, compliance_percent: 66.66666 }],
  });
  assert.ok(run);
  assert.equal(run.sections[0].compliancePercent, 66.66666);
  assert.equal(run.sections[0].displayPercent, 66.7);
});

// ── Decision gating ─────────────────────────────────────────────────────────

test("nothing can be decided before the audit is scored", () => {
  assert.equal(canDecide(null), false);
  assert.equal(canDecide(normalizeScoreRun({ proposed_decision: "APPROVED" })), true);
});

test("disagreeing with the engine requires a written reason", () => {
  const run = normalizeScoreRun({ proposed_decision: "NOT_APPROVED" });
  // Agreeing needs no justification...
  assert.equal(requiresOverrideReason(run, "NOT_APPROVED"), false);
  // ...disagreeing does, and the field only appears when it is needed.
  assert.equal(requiresOverrideReason(run, "APPROVED"), true);
  assert.equal(requiresOverrideReason(run, "APPROVED_WITH_CONDITIONS"), true);
});

test("a REVIEW_REQUIRED proposal makes every decision an override", () => {
  // The engine declined to propose an outcome, so any human decision is a
  // departure from it and has to be explained.
  const run = normalizeScoreRun({ proposed_decision: "REVIEW_REQUIRED" });
  for (const decision of ["APPROVED", "APPROVED_WITH_CONDITIONS", "NOT_APPROVED"] as const) {
    assert.equal(requiresOverrideReason(run, decision), true);
  }
});

test("a conditional approval with no conditions is blocked", () => {
  // "Approved with conditions" and no conditions is an unconditional approval
  // wearing a hat. The server refuses it; the button does too.
  assert.equal(conditionalNeedsCapa("APPROVED_WITH_CONDITIONS", []), true);
  assert.equal(conditionalNeedsCapa("APPROVED_WITH_CONDITIONS", ["capa-1"]), false);
  assert.equal(conditionalNeedsCapa("APPROVED", []), false);
});

test("an override keeps the machine's verdict visible", () => {
  const decision = normalizeDecision({
    decision: "APPROVED", machine_proposed: "NOT_APPROVED", overridden: true,
    override_reason: "Regulator exemption on file.", decided_by: "approver-1",
  });
  assert.ok(decision);
  assert.equal(decision.overridden, true);
  // The disagreement is the most audit-relevant fact on the record.
  assert.equal(decision.machineProposed, "NOT_APPROVED");
  assert.equal(decision.overrideReason, "Regulator exemption on file.");
});

test("an absent decision is null, not a default NOT_APPROVED record", () => {
  assert.equal(normalizeDecision(null), null);
  assert.equal(normalizeDecision(undefined), null);
});

// ── Virtual visits ──────────────────────────────────────────────────────────

test("consent defaults to PENDING, which blocks joining", () => {
  // A malformed response must not read as consent already given.
  const session = normalizeSession({ id: "s1" });
  assert.equal(session.consentStatus, "PENDING");
  assert.equal(consentPermitsJoin(session), false);
});

test("the default privacy mode is the one that cannot record", () => {
  const session = normalizeSession({ id: "s1" });
  assert.equal(session.privacyMode, "SERVER_NO_RECORDING");
  assert.equal(session.recordingEnabled, false);
});

test("only GRANTED and NOT_REQUIRED permit a join", () => {
  for (const status of ["GRANTED", "NOT_REQUIRED"]) {
    assert.equal(consentPermitsJoin(normalizeSession({ consent_status: status })), true, status);
  }
  for (const status of ["PENDING", "DECLINED"]) {
    assert.equal(consentPermitsJoin(normalizeSession({ consent_status: status })), false, status);
  }
});

test("a recorded session is reported as recorded", () => {
  const session = normalizeSession({
    id: "s1", privacy_mode: "SERVER_RECORDED", recording_enabled: true,
  });
  assert.equal(session.privacyMode, "SERVER_RECORDED");
  assert.equal(session.recordingEnabled, true);
});

test("an E2EE session is never reported as recording-enabled", () => {
  const session = normalizeSession({
    id: "s1", privacy_mode: "E2EE_NO_RECORDING", recording_enabled: false,
  });
  assert.equal(session.recordingEnabled, false);
});
