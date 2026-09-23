import { requireOptionalNativeModule } from "expo-modules-core";
import { Platform } from "react-native";

type MtbPlayIntegrityNativeModule = {
  isAvailable(): boolean;
  requestToken(nonce: string): Promise<string>;
};

// `requireOptionalNativeModule` returns null rather than throwing when the
// native module isn't in the running build — which is the normal case in Expo
// Go, and on every iOS build (this module is Android-only; iOS attestation is
// App Attest and is not implemented).
const nativeModule = Platform.OS === "android"
  ? requireOptionalNativeModule<MtbPlayIntegrityNativeModule>("MtbPlayIntegrity")
  : null;

/** Why no attestation token was produced. Recorded on the evidence so a
 *  reviewer can tell "this build cannot attest" from "attestation was tried
 *  and failed" — the first is expected in a dev build, the second is not. */
export type AttestationUnavailableReason =
  | "NOT_ANDROID"
  | "MODULE_NOT_IN_BUILD"
  | "REQUEST_FAILED";

export interface AttestationOutcome {
  /** The raw Play Integrity token, for the server to decode. Never parsed here. */
  token: string | null;
  reason: AttestationUnavailableReason | null;
  /** Provider-side detail, for diagnostics only. Never a verdict. */
  detail?: string;
}

export function isPlayIntegrityAvailable(): boolean {
  return nativeModule?.isAvailable() ?? false;
}

/**
 * Request a Play Integrity token bound to the server's capture-session nonce.
 *
 * NEVER THROWS. A missing module, an outage, a device without Play Services —
 * all return `token: null` with a reason. The capture still goes ahead and the
 * server routes it to human review, which is the honest outcome: the evidence
 * is real, we just can't prove the app that produced it was genuine.
 *
 * Aborting the capture instead would lose real evidence over a Google outage,
 * and returning a placeholder token would quietly defeat the whole control.
 */
export async function requestAttestationToken(nonce: string): Promise<AttestationOutcome> {
  if (Platform.OS !== "android") {
    return { token: null, reason: "NOT_ANDROID" };
  }
  if (!nativeModule) {
    return { token: null, reason: "MODULE_NOT_IN_BUILD" };
  }
  try {
    const token = await nativeModule.requestToken(nonce);
    // An empty string is not a token. Treating it as one would send the
    // server something it would decode as garbage and report as TAMPERED,
    // which is a worse answer than "unavailable".
    if (!token) {
      return { token: null, reason: "REQUEST_FAILED", detail: "empty token" };
    }
    return { token, reason: null };
  } catch (error) {
    return {
      token: null,
      reason: "REQUEST_FAILED",
      detail: error instanceof Error ? error.message : String(error),
    };
  }
}
