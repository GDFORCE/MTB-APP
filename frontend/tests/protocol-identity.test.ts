import assert from "node:assert/strict";
import test from "node:test";

import {
  identityBlockFrom, resolutionBody, savingDisabledReason, successNote, validateResolution,
  blockHeadline,
} from "../src/features/uctsm/protocolIdentity.ts";
import type { ProtocolIdentity } from "../src/features/uctsm/protocolIdentity.ts";

/**
 * P2: a quarantined protocol upload is a structured 409, never an extraction
 * success and never a generic "could not extract" error.
 */

const IDENTITY: ProtocolIdentity = {
  id: "i1", classification: "MISMATCH", original_classification: "MISMATCH",
  review_status: "PENDING_REVIEW", revision: 3, permitted: false, filename: "NCT02088541.pdf",
  stage: "QUARANTINED", created_at: "2026-10-14T00:00:00Z", issue_codes: ["IDENTITY_MISMATCH"],
  allowed_actions: ["CORRECT_EXPECTED_IDENTITY", "REJECT"], binary_sha256: "a",
  text_verified_full_text: true,
};

const quarantine = (status: number, detail: unknown) => ({ response: { status, data: { detail } } });

test("a quarantine response is recognised and carries its identity", () => {
  const block = identityBlockFrom(quarantine(409, {
    code: "PROTOCOL_IDENTITY_BLOCKED", message: "Wrong protocol", identity: IDENTITY, next_action: "Ask a reviewer",
  }));
  assert.equal(block?.code, "PROTOCOL_IDENTITY_BLOCKED");
  assert.equal(block?.identity?.id, "i1");
  assert.match(blockHeadline(block!), /not the protocol it was submitted as/);
});

test("ordinary errors are not mistaken for an identity block", () => {
  assert.equal(identityBlockFrom(quarantine(409, "Trial already exists")), null);
  assert.equal(identityBlockFrom(quarantine(503, { code: "PROTOCOL_IDENTITY_BLOCKED" })), null);
  assert.equal(identityBlockFrom(quarantine(409, { code: "SOMETHING_ELSE" })), null);
  assert.equal(identityBlockFrom(new Error("network")), null);
});

test("saving stays disabled, with a reason, while identity is blocked or pending", () => {
  const block = identityBlockFrom(quarantine(409, { code: "PROTOCOL_IDENTITY_BLOCKED", message: "m", identity: IDENTITY }));
  assert.ok(savingDisabledReason(null, block));
  assert.match(savingDisabledReason(IDENTITY, null) ?? "", /waiting for an identity review/);
  assert.equal(savingDisabledReason({ ...IDENTITY, permitted: true, classification: "MATCH" }, null), null);
  assert.equal(savingDisabledReason(null, null), null);
});

test("repeats and amendments get an explanatory note, a plain match does not", () => {
  assert.match(successNote({ ...IDENTITY, classification: "DUPLICATE", permitted: true, reused_extraction: true }) ?? "", /reused/);
  assert.match(successNote({ ...IDENTITY, classification: "NEW_VERSION", permitted: true }) ?? "", /earlier version/);
  assert.equal(successNote({ ...IDENTITY, classification: "MATCH", permitted: true }), null);
});

test("a reviewer decision needs a reason and, where relevant, an identifier", () => {
  assert.ok(validateResolution("REJECT", "  ", ""));
  assert.equal(validateResolution("REJECT", "wrong file", ""), null);
  assert.ok(validateResolution("CORRECT_EXPECTED_IDENTITY", "typo", ""));
  assert.equal(validateResolution("CORRECT_EXPECTED_IDENTITY", "typo", "NCT02088554"), null);
  const body = resolutionBody(IDENTITY, "CORRECT_EXPECTED_IDENTITY", " typo ", " NCT02088554 ");
  assert.deepEqual(body, {
    action: "CORRECT_EXPECTED_IDENTITY", reason: "typo", expected_revision: 3,
    corrected_identifier: "NCT02088554",
  });
  assert.equal(resolutionBody(IDENTITY, "REJECT", "no", "x").corrected_identifier, null);
});
