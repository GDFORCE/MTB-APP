import assert from "node:assert/strict";
import test from "node:test";
import { approveRuleBody, canHandleRules, declareRuleBody, emptyManualForm, reviewFailureMessage, type RuleView } from "../src/features/uctsm/manualRules.ts";

const view: RuleView = { schedule_version_id: "draft", revision: 7, status: "IN_REVIEW",
  events: [{ code: "SPECIAL", display_name: "Special review", handling: null, evidence_refs: ["source"] }],
  evidence: [{ id: "source", source_text: "Synthetic instruction for a manual assessment." }], issues: [] };
const valid = { kind: "MANUAL_ONLY" as const, reason: " Explicit source limitation ",
  evidence: ["source"], policy: " Reviewed workflow ", responsible: true };

test("manual handling requires an explicit choice, source, reason, policy and responsibility", () => {
  assert.throws(() => declareRuleBody(view, "SPECIAL", emptyManualForm()));
  for (const change of [{ kind: null }, { reason: "" }, { evidence: [] }, { evidence: ["foreign"] },
    { policy: "" }, { responsible: false }]) {
    assert.throws(() => declareRuleBody(view, "SPECIAL", { ...valid, ...change }));
  }
  assert.throws(() => declareRuleBody(view, "foreign-event", valid));
  assert.throws(() => declareRuleBody({ ...view, status: "APPROVED" }, "SPECIAL", valid));
});

test("a handling request binds the current draft revision without client actor, approval or date", () => {
  assert.deepEqual(declareRuleBody(view, "SPECIAL", { ...valid, evidence: ["source", "source"] }), {
    schedule_version_id: "draft", expected_revision: 7, event_code: "SPECIAL", kind: "MANUAL_ONLY",
    reason: "Explicit source limitation", evidence_refs: ["source"], policy_reference: "Reviewed workflow",
    accepts_responsibility: true,
  });
});

test("approval is available only for a pending manual request and still requires a reason", () => {
  assert.throws(() => approveRuleBody(view, "SPECIAL", "independent review"));
  const pending = structuredClone(view);
  pending.events[0].handling = { kind: "MANUAL_ONLY", status: "PENDING", requested_by: "requester",
    approved_by: null, owner_id: "requester", reason: "reason", evidence_refs: ["source"], policy_reference: "workflow" };
  assert.throws(() => approveRuleBody(pending, "SPECIAL", ""));
  assert.equal(approveRuleBody(pending, "SPECIAL", "Independent review").expected_revision, 7);
  pending.events[0].handling.kind = "UNSUPPORTED";
  assert.throws(() => approveRuleBody(pending, "SPECIAL", "Independent review"));
});

test("UI rights fail closed and trial-scoped grants do not leak to other trials", () => {
  const scoped = { capabilities: {}, trial_scoped_grants: [{ capability: "schedule.correct", trial_id: "one" }] };
  assert.equal(canHandleRules(null, "one", "schedule.correct"), false);
  assert.equal(canHandleRules(scoped, "one", "schedule.correct"), true);
  assert.equal(canHandleRules(scoped, "two", "schedule.correct"), false);
  assert.equal(canHandleRules(scoped, "one", "schedule.approve"), false);
});

test("schedule review explains maker-checker without presenting it as an amendment grant problem", () => {
  const message = reviewFailureMessage({ response: { status: 403, headers: { "x-authz-code": "MAKER_CHECKER" } } });
  assert.match(message, /last material change cannot approve/);
  assert.match(message, /another authorised approver/);
  assert.doesNotMatch(message, /amendment/);
  assert.match(reviewFailureMessage({ response: { status: 403 } }), /authorised reviewer/);
});

test("review errors distinguish stale drafts and uncertain server outcomes without reflecting raw diagnostics", () => {
  assert.match(reviewFailureMessage({ response: { status: 409, data: { detail: "PRIVATE_SENTINEL" } } }), /Reload and review/);
  assert.match(reviewFailureMessage({ response: { status: 503, data: { detail: "PRIVATE_SENTINEL" } } }), /check its state/);
  assert.doesNotMatch(reviewFailureMessage(new Error("PRIVATE_SENTINEL")), /PRIVATE_SENTINEL/);
  assert.equal(reviewFailureMessage({ response: { status: 422, data: { detail: "Choose source evidence" } } }), "Choose source evidence");
});
