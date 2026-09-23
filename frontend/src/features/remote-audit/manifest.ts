// Remote Audit — pure capture-manifest construction.
//
// Deliberately free of expo/react-native imports so it can be unit-tested with
// `node --test` like this codebase's other pure frontend logic. The platform is
// passed in rather than read from `Platform`, which is also what lets the tests
// prove the Android-only mock-location handling without an emulator.
import type { CaptureLocation } from "./types";

export function toHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

export interface ManifestInput {
  sessionNonce: string;
  location: CaptureLocation | null;
  capturedAtMs: number;
  platformOs: string;
  platformVersion?: string | number;
  playIntegrityToken?: string | null;
  /** Why no token was produced, when there isn't one. Recorded so a reviewer
   *  can tell "this build cannot attest" from "attestation was tried and
   *  failed" — the first is expected in a dev build, the second is not. */
  attestationUnavailableReason?: string | null;
}

/**
 * The client half of the capture manifest (spec §8.2). Everything the server
 * can determine for itself — receipt time, object key, session binding, policy
 * version — is added server-side at finalize; this carries only what the device
 * alone knows.
 *
 * These are CLAIMS. The worker cross-checks them against EXIF, the site's
 * geofence, and the attestation verdict before any of it counts for anything,
 * so nothing here is trusted on its own.
 */
export function buildCaptureManifest(input: ManifestInput): Record<string, unknown> {
  const {
    sessionNonce, location, capturedAtMs, platformOs, platformVersion,
    playIntegrityToken, attestationUnavailableReason,
  } = input;
  return {
    nonce: sessionNonce,
    captured_at_client_utc: new Date(capturedAtMs).toISOString(),
    platform: platformOs,
    platform_version: String(platformVersion ?? ""),
    // An absent fix is represented as ABSENT, never as 0/0 — the backend
    // distinguishes LOCATION_MISSING from a point in the Gulf of Guinea, and
    // collapsing the two would turn "we don't know" into a confident lie.
    ...(location
      ? {
          lat: location.lat,
          lon: location.lon,
          accuracy_m: location.accuracyM,
          location_age_ms: location.locationAgeMs,
          // expo-location surfaces the OS mock-provider flag on Android only.
          android_is_mock: platformOs === "android" ? location.mocked : false,
          // No Expo-level iOS simulated-location signal exists today (it
          // arrives with Phase 8's native work). Reporting `false` for a
          // signal we cannot read is the honest encoding of "no iOS signal",
          // and the engine never treats a false here as evidence of anything.
          ios_is_simulated: false,
        }
      : {}),
    // The raw token, for the server to decode through Google. Never parsed
    // client-side — a client that read its own verdict and reported "I'm fine"
    // would be exactly the self-attestation this replaces.
    play_integrity_token: playIntegrityToken ?? null,
    // Present only when there is no token. Diagnostics, never a verdict: the
    // server decides what an absent attestation means, not this field.
    attestation_unavailable_reason: playIntegrityToken
      ? null
      : (attestationUnavailableReason ?? null),
  };
}
