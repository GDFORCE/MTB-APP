import assert from "node:assert/strict";
import test from "node:test";

import {
  isDecidable,
  isUnconfirmed,
  normalizeCheckRun,
  normalizeDocument,
  normalizeExtraction,
  normalizeUploadTarget,
  REVIEWER_VISIBLE,
} from "../src/features/remote-audit/documentNormalize.ts";

/**
 * Remote Audit — document normalization (spec §10, extract → review → commit).
 *
 * The failures guarded here are the ones that would be invisible in the UI:
 * an unscanned document rendered as if it had passed, a model's guess shown
 * where a reviewer's confirmation should be, or findings silently dropped from
 * a verdict a reviewer is about to act on.
 */

test("an unrecognized status falls back to QUARANTINED, never something permissive", () => {
  const doc = normalizeDocument({ id: "d1", audit_id: "a1" });
  assert.equal(doc.status, "QUARANTINED");
  // And QUARANTINED is not a status a reviewer may see or act on.
  assert.equal(REVIEWER_VISIBLE.has(doc.status), false);
  assert.equal(isDecidable(doc.status), false);
});

test("PROCESSING_BLOCKED is neither visible nor decidable", () => {
  // The scanner being unreachable must never surface the file with a caveat —
  // it stays hidden until it has actually been scanned.
  assert.equal(REVIEWER_VISIBLE.has("PROCESSING_BLOCKED"), false);
  assert.equal(isDecidable("PROCESSING_BLOCKED"), false);
});

test("only post-check states are decidable", () => {
  assert.equal(isDecidable("TECHNICALLY_VALID"), true);
  assert.equal(isDecidable("REVIEW_REQUIRED"), true);
  for (const terminal of ["ACCEPTED", "REJECTED", "SUPERSEDED", "EXPIRED"]) {
    assert.equal(isDecidable(terminal), false, `${terminal} must not be decidable again`);
  }
});

test("extraction lands in `extracted` and never in `confirmed`", () => {
  const doc = normalizeDocument({
    id: "d1",
    status: "REVIEW_REQUIRED",
    extracted: { source: "vision:anthropic", fields: { due_date: "2027-01-01" }, confidence: "low" },
  });
  assert.equal(doc.extracted?.fields.due_date, "2027-01-01");
  // A model's guess is not a fact and must not arrive as one.
  assert.equal(doc.confirmed, null);
  assert.equal(doc.expiresOn, null);
});

test("an unrecognized confidence becomes null, not high", () => {
  assert.equal(normalizeExtraction({ source: "x", fields: {}, confidence: "very sure" })?.confidence, null);
  assert.equal(normalizeExtraction({ source: "x", fields: {} })?.confidence, null);
  assert.equal(normalizeExtraction({ source: "x", fields: {}, confidence: "high" })?.confidence, "high");
});

test("absent extraction is null rather than an empty suggestion block", () => {
  // An empty suggestions panel would read as "the model found nothing",
  // which is a different claim from "no extraction was attempted".
  assert.equal(normalizeExtraction(null), null);
  assert.equal(normalizeExtraction(undefined), null);
  assert.equal(normalizeDocument({ id: "d" }).extracted, null);
});

test("isUnconfirmed distinguishes a suggestion from a confirmed value", () => {
  const suggestedOnly = normalizeDocument({
    id: "d1",
    extracted: { source: "pdf_text_layer", fields: { due_date: "2027-01-01" }, confidence: "medium" },
  });
  assert.equal(isUnconfirmed(suggestedOnly, "due_date"), true);

  const confirmed = normalizeDocument({
    id: "d2",
    extracted: { source: "pdf_text_layer", fields: { due_date: "2027-01-01" }, confidence: "medium" },
    confirmed: { due_date: "2027-01-02" },
  });
  // Once a human has ruled, the suggestion is history — including when they
  // corrected it.
  assert.equal(isUnconfirmed(confirmed, "due_date"), false);
});

test("check runs preserve every finding with its severity and measurements", () => {
  const run = normalizeCheckRun({
    check_run_id: 3,
    outcome: "REVIEW_REQUIRED",
    rule_set_version: "document-checks-v1",
    findings: [
      { code: "AMBIGUOUS_DATE_FORMAT", severity: "medium", explanation: "due date could not be read unambiguously from '03/04/2026'", field: "due_date", measured_value: "03/04/2026" },
      { code: "SIGNATURE_PRESENT_UNVERIFIED", severity: "medium", explanation: "no trust anchors configured" },
    ],
    checks: { malware_status: "CLEAN", mime_match: true },
  });

  assert.ok(run);
  assert.equal(run.findings.length, 2);
  assert.equal(run.findings[0].field, "due_date");
  assert.equal(run.findings[0].measuredValue, "03/04/2026");
  assert.equal(run.checks.malware_status, "CLEAN");
  assert.equal(run.checkRunId, 3);
});

test("malformed findings degrade to an empty list rather than crashing the screen", () => {
  const run = normalizeCheckRun({ outcome: "TECHNICALLY_VALID", findings: "nope" });
  assert.ok(run);
  assert.deepEqual(run.findings, []);
  assert.deepEqual(run.checks, {});
});

test("an absent check run is null, not an empty pass", () => {
  // The screen renders null as "nothing checked yet". A synthesized empty run
  // would render as a completed verdict with no findings.
  assert.equal(normalizeCheckRun(null), null);
  assert.equal(normalizeCheckRun(undefined), null);
});

test("content_blocked survives normalization", () => {
  // This flag is what keeps a malware-rejected file's bytes unreachable. If
  // it were dropped, the UI would offer a download the server then refuses.
  const doc = normalizeDocument({ id: "d1", status: "REJECTED", content_blocked: true });
  assert.equal(doc.contentBlocked, true);
  assert.equal(normalizeDocument({ id: "d2" }).contentBlocked, false);
});

test("upload target carries the presigned POST when storage issued one", () => {
  const direct = normalizeUploadTarget({
    id: "d1", status: "QUARANTINED", server_mediated: false,
    upload: { url: "https://bucket.example/", fields: { key: "k", policy: "p" } },
  });
  assert.equal(direct.serverMediated, false);
  assert.equal(direct.upload?.url, "https://bucket.example/");

  const mediated = normalizeUploadTarget({
    id: "d2", status: "QUARANTINED", server_mediated: true,
    server_upload_url: "/api/remote-audit/documents/d2/upload-bytes",
  });
  assert.equal(mediated.serverMediated, true);
  assert.equal(mediated.upload, null);
  assert.equal(mediated.serverUploadUrl, "/api/remote-audit/documents/d2/upload-bytes");
});

test("supersession links are preserved in both directions", () => {
  const doc = normalizeDocument({
    id: "new", supersedes_document_id: "old", superseded_by_document_id: null,
  });
  assert.equal(doc.supersedesDocumentId, "old");
  assert.equal(doc.supersededByDocumentId, null);
});
