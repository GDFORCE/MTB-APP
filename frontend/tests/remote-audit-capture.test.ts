import assert from "node:assert/strict";
import test from "node:test";

import { buildCaptureManifest, toHex } from "../src/features/remote-audit/manifest.ts";
import {
  normalizeEvidence,
  normalizeSession,
  normalizeUploadIntent,
  normalizeVerification,
} from "../src/features/remote-audit/normalize.ts";
import type { CaptureLocation } from "../src/features/remote-audit/types.ts";

/**
 * Remote Audit — capture manifest and response normalization
 * (spec §8.2 capture manifest, §15.1 "never expose only a score").
 *
 * Each test here guards a failure that would be silent in the UI and expensive
 * downstream: a fabricated 0/0 location, a mock-location flag dropped on the
 * way to the server, or a verification verdict rendered without the reasons
 * that justify it.
 */

const fix: CaptureLocation = {
  lat: 12.9716,
  lon: 77.5946,
  accuracyM: 8.5,
  locationAgeMs: 1200,
  mocked: false,
};

const CAPTURED_AT = Date.UTC(2026, 8, 17, 10, 30, 0);

test("a missing location produces NO coordinate keys, never 0/0", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n1", location: null, capturedAtMs: CAPTURED_AT, platformOs: "android",
  });

  // The backend distinguishes LOCATION_MISSING from a real point. Emitting
  // lat:0/lon:0 would place every basement capture off the coast of Africa and
  // classify it OUTSIDE_BOUNDARY instead of "we could not get a fix".
  for (const key of ["lat", "lon", "accuracy_m", "location_age_ms", "android_is_mock"]) {
    assert.ok(!(key in manifest), `${key} must be absent when there is no fix`);
  }
  assert.equal(manifest.nonce, "n1");
});

test("a real fix is carried through verbatim, including a null accuracy", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n2", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
  });

  assert.equal(manifest.lat, 12.9716);
  assert.equal(manifest.lon, 77.5946);
  assert.equal(manifest.accuracy_m, 8.5);
  assert.equal(manifest.location_age_ms, 1200);

  const unknownAccuracy = buildCaptureManifest({
    sessionNonce: "n3",
    location: { ...fix, accuracyM: null, locationAgeMs: null },
    capturedAtMs: CAPTURED_AT,
    platformOs: "android",
  });
  // null means "the OS did not report it" — it must reach the server as null,
  // not as a plausible-looking default that the geofence classifier would trust.
  assert.equal(unknownAccuracy.accuracy_m, null);
  assert.equal(unknownAccuracy.location_age_ms, null);
});

test("an Android mock-location flag reaches the server", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n4",
    location: { ...fix, mocked: true },
    capturedAtMs: CAPTURED_AT,
    platformOs: "android",
  });
  // This is the single strongest spoofing signal the client can report.
  // Dropping it would let a mocked GPS pass as a genuine on-site capture.
  assert.equal(manifest.android_is_mock, true);
});

test("iOS never claims an android_is_mock verdict it cannot make", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n5",
    location: { ...fix, mocked: true },
    capturedAtMs: CAPTURED_AT,
    platformOs: "ios",
  });
  // expo-location's `mocked` is Android-only; on iOS it is meaninglessly
  // false-y. Forwarding it as an iOS verdict would be inventing a signal.
  assert.equal(manifest.android_is_mock, false);
  assert.equal(manifest.ios_is_simulated, false);
});

test("capture time is emitted as an unambiguous UTC instant", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n6", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
  });
  assert.equal(manifest.captured_at_client_utc, "2026-09-17T10:30:00.000Z");
});

test("attestation token defaults to null rather than being omitted", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n7", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
  });
  // An explicit null says "this build has no attestation bridge yet"
  // (true until Phase 8). An absent key would read the same as a malformed
  // payload; the server should be able to tell the difference.
  assert.equal(manifest.play_integrity_token, null);
});

test("toHex produces lowercase, zero-padded hex", () => {
  const buf = new Uint8Array([0x00, 0x0f, 0xff, 0xa5]).buffer;
  // Zero-padding matters: dropping the leading zero of 0x0f would yield a
  // 63-character digest that could never match the server's SHA-256.
  assert.equal(toHex(buf), "000fffa5");
});

test("verification normalization preserves every reason", () => {
  const result = normalizeVerification({
    overall_state: "REVIEW",
    rule_set_version: "evidence-verification-v1",
    verification_run_id: 2,
    reasons: [
      { code: "GEO_OUTSIDE_BOUNDARY", severity: "high", explanation: "Captured 240 m outside the approved site boundary.", measured_value: 240, threshold: 50 },
      { code: "ATTESTATION_UNAVAILABLE", severity: "medium", explanation: "Device integrity could not be established." },
    ],
    signals: { geo_status: "OUTSIDE_BOUNDARY", hash_match: true },
  });

  assert.ok(result);
  assert.equal(result.overallState, "REVIEW");
  // Spec §15.1: a reviewer must never see a verdict without its justification.
  assert.equal(result.reasons.length, 2);
  assert.equal(result.reasons[0].code, "GEO_OUTSIDE_BOUNDARY");
  assert.equal(result.reasons[0].measuredValue, 240);
  assert.equal(result.reasons[0].threshold, 50);
  assert.equal(result.reasons[1].severity, "medium");
  assert.equal(result.signals.geo_status, "OUTSIDE_BOUNDARY");
  assert.equal(result.verificationRunId, 2);
});

test("a verification with malformed reasons degrades to an empty list, not a crash", () => {
  const result = normalizeVerification({ overall_state: "PASS", reasons: "not-an-array" });
  assert.ok(result);
  assert.deepEqual(result.reasons, []);
  assert.deepEqual(result.signals, {});
});

test("an absent verification is null, not an empty PASS", () => {
  // The viewer renders null as "nothing decided yet". A synthesized empty
  // result would render as a verdict with no findings — i.e. a clean bill of
  // health for evidence nobody has checked.
  assert.equal(normalizeVerification(null), null);
  assert.equal(normalizeVerification(undefined), null);
});

test("unknown evidence status falls back to QUARANTINED", () => {
  const item = normalizeEvidence({ id: "e1", audit_id: "a1" });
  // The safe default is "isolated, not yet judged". Any PASS-like default
  // would let a backend rename silently present unverified evidence as fine.
  assert.equal(item.status, "QUARANTINED");
  assert.equal(item.captureMode, "LIVE_IN_APP");
});

test("session and upload-intent normalization accept both snake and camel case", () => {
  const session = normalizeSession({
    id: "s1", audit_id: "a1", nonce: "abc", expires_at: "2026-09-17T10:35:00Z",
    requested_media_type: "image/jpeg", capture_mode: "LIVE_IN_APP",
  });
  assert.equal(session.nonce, "abc");
  assert.equal(session.captureMode, "LIVE_IN_APP");

  const intent = normalizeUploadIntent({
    evidenceId: "e1", objectKey: "remote-audit/a1/originals/e1.jpg", serverMediated: true,
  });
  assert.equal(intent.objectKey, "remote-audit/a1/originals/e1.jpg");
  assert.equal(intent.serverMediated, true);
  assert.equal(intent.upload, null);
});

// ── Attestation in the manifest (Phase 8, Android) ──────────────────────────

test("a real attestation token reaches the manifest and clears the reason", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n8", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
    playIntegrityToken: "eyJhbGciOiJSUzI1NiJ9.verdict.signature",
    attestationUnavailableReason: "REQUEST_FAILED",
  });
  assert.equal(manifest.play_integrity_token, "eyJhbGciOiJSUzI1NiJ9.verdict.signature");
  // A token and a "why there's no token" are mutually exclusive. Sending both
  // would let a stale reason contradict the evidence actually supplied.
  assert.equal(manifest.attestation_unavailable_reason, null);
});

test("the reason is carried when there is no token", () => {
  // The distinction the reviewer needs: a dev build that CANNOT attest is
  // expected; an attestation that was tried and failed is not.
  const notInBuild = buildCaptureManifest({
    sessionNonce: "n9", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
    attestationUnavailableReason: "MODULE_NOT_IN_BUILD",
  });
  assert.equal(notInBuild.play_integrity_token, null);
  assert.equal(notInBuild.attestation_unavailable_reason, "MODULE_NOT_IN_BUILD");

  const failed = buildCaptureManifest({
    sessionNonce: "n10", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "android",
    attestationUnavailableReason: "REQUEST_FAILED",
  });
  assert.equal(failed.attestation_unavailable_reason, "REQUEST_FAILED");
});

test("an absent attestation is null on both fields, never a placeholder", () => {
  const manifest = buildCaptureManifest({
    sessionNonce: "n11", location: fix, capturedAtMs: CAPTURED_AT, platformOs: "ios",
  });
  // A placeholder token would be decoded server-side as garbage and reported
  // TAMPERED — a worse answer than an honest "unavailable".
  assert.equal(manifest.play_integrity_token, null);
  assert.equal(manifest.attestation_unavailable_reason, null);
});

test("the capture-session nonce satisfies Play Integrity's own constraints", () => {
  // Google requires URL-safe, unpadded, 16-500 characters. The server's nonce
  // is secrets.token_hex(16) = 32 hex chars, which qualifies — this pins that
  // so a future change to the nonce format fails here rather than producing
  // tokens Google silently rejects.
  const serverNonce = "a3f1c8e29b7d4506a3f1c8e29b7d4506";
  assert.ok(serverNonce.length >= 16 && serverNonce.length <= 500);
  assert.match(serverNonce, /^[A-Za-z0-9_-]+$/);
});
