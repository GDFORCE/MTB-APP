// Remote Audit — capture-side device I/O.
//
// Two things here are security-relevant and deliberately NOT convenience code:
//
// 1. `sha256OfFile` hashes the RAW BYTES of the captured file. The backend
//    worker recomputes SHA-256 over the same bytes and a mismatch is a hard
//    FAIL_TECHNICAL (see verify_pipeline.py's `hash_match`). Hashing a base64
//    string, or the file's URI, or anything else convenient would make every
//    capture fail — so this reads real bytes via expo-file-system's `File`
//    and feeds them to expo-crypto's binary `digest()`.
//
// 2. `acquireFreshLocation` always requests a NEW fix. It never falls back to
//    `getLastKnownPositionAsync`: the server classifies staleness from
//    `location_age_ms`, and quietly handing it a cached fix would launder an
//    old location into a fresh-looking one. If no fix can be obtained, the
//    manifest carries no coordinates at all and the server classifies the
//    evidence LOCATION_MISSING — which is the honest outcome.
//
// The pure manifest construction lives in ./manifest so it stays unit-testable.
import * as Crypto from "expo-crypto";
import { File } from "expo-file-system";
import * as Location from "expo-location";
import { Platform } from "react-native";

import { requestAttestationToken } from "@/modules/mtb-play-integrity";
import type { AttestationOutcome } from "@/modules/mtb-play-integrity";

import { buildCaptureManifest as buildManifest, toHex } from "./manifest";
import type { CaptureLocation } from "./types";

/** SHA-256 over the file's actual bytes, hex-encoded — the exact value the
 *  backend worker independently recomputes from object storage. */
export async function sha256OfFile(uri: string): Promise<string> {
  const bytes = await new File(uri).bytes();
  const digest = await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes);
  return toHex(digest);
}

export async function fileSize(uri: string): Promise<number> {
  const size = new File(uri).size;
  return typeof size === "number" && size > 0 ? size : 0;
}

export class LocationUnavailable extends Error {}

/**
 * Acquire a fresh, high-accuracy position.
 *
 * `mocked` is Android-only — expo-location exposes the OS's own mock-provider
 * flag there. iOS has no equivalent through Expo today, so a simulated iOS
 * location reports `mocked: false` here; that gap closes with the device
 * attestation work in Phase 8, and the backend never treats "not mocked" as
 * positive proof of anything on its own.
 */
export async function acquireFreshLocation(): Promise<CaptureLocation> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  if (status !== "granted") {
    throw new LocationUnavailable("Location permission is required to capture audit evidence.");
  }
  const enabled = await Location.hasServicesEnabledAsync();
  if (!enabled) {
    throw new LocationUnavailable("Turn on location services, then try again.");
  }
  const fix = await Location.getCurrentPositionAsync({
    accuracy: Location.Accuracy.Highest,
    mayShowUserSettingsDialog: true,
  });
  return {
    lat: fix.coords.latitude,
    lon: fix.coords.longitude,
    accuracyM: typeof fix.coords.accuracy === "number" ? fix.coords.accuracy : null,
    locationAgeMs: Math.max(0, Date.now() - fix.timestamp),
    mocked: Boolean(fix.mocked),
  };
}

/** Thin binding of the pure manifest builder to this device's platform. */
export function buildCaptureManifest(input: {
  sessionNonce: string;
  location: CaptureLocation | null;
  capturedAtMs: number;
  playIntegrityToken?: string | null;
  attestationUnavailableReason?: string | null;
}): Record<string, unknown> {
  return buildManifest({ ...input, platformOs: Platform.OS, platformVersion: Platform.Version });
}

/**
 * Request a Play Integrity token bound to this capture session's nonce.
 *
 * Re-exported through this module so the capture screen has one import for
 * everything device-side. Never throws — see the module's own docstring for
 * why an unavailable provider must not abort a capture.
 */
export async function attestCapture(sessionNonce: string): Promise<AttestationOutcome> {
  return requestAttestationToken(sessionNonce);
}

export { isPlayIntegrityAvailable } from "@/modules/mtb-play-integrity";
export type { AttestationOutcome } from "@/modules/mtb-play-integrity";
