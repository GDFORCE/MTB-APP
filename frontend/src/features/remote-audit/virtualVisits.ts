// Remote Audit — virtual-visit types and pure helpers (Phase 6, spec §12).
//
// Free of react-native/axios imports so it stays unit-testable with
// `node --test`, like the other pure modules here.
//
// Two defaults in `normalizeSession` are safety decisions rather than
// conveniences, and both err the same way: an unrecognized or missing field
// produces the state that BLOCKS, never the one that permits. A malformed
// response must not read as "consent already given" or "not being recorded".

export type PrivacyMode = "E2EE_NO_RECORDING" | "SERVER_RECORDED" | "SERVER_NO_RECORDING";
export type SessionState = "SCHEDULED" | "LIVE" | "ENDED" | "CANCELLED";
export type ConsentStatus = "PENDING" | "GRANTED" | "DECLINED" | "NOT_REQUIRED";

export interface VirtualSession {
  id: string;
  auditId: string;
  state: SessionState;
  privacyMode: PrivacyMode;
  recordingEnabled: boolean;
  consentRequired: boolean;
  consentStatus: ConsentStatus;
  scheduledStartUtc?: string;
  expectedDurationMinutes: number;
  purpose: string;
}

const PRIVACY_MODES: readonly string[] = [
  "E2EE_NO_RECORDING", "SERVER_RECORDED", "SERVER_NO_RECORDING",
];
const CONSENT_STATUSES: readonly string[] = [
  "PENDING", "GRANTED", "DECLINED", "NOT_REQUIRED",
];

export function normalizeSession(raw: any): VirtualSession {
  const mode = String(raw?.privacy_mode ?? raw?.privacyMode ?? "");
  const consent = String(raw?.consent_status ?? raw?.consentStatus ?? "");
  return {
    id: String(raw?.id ?? ""),
    auditId: String(raw?.audit_id ?? raw?.auditId ?? ""),
    state: (raw?.state ?? "SCHEDULED") as SessionState,
    // An unrecognized mode falls back to the one that cannot record. The
    // alternative — defaulting to SERVER_RECORDED — would be harmless-looking
    // and would tell people a call is recorded when nobody knows.
    privacyMode: (PRIVACY_MODES.includes(mode) ? mode : "SERVER_NO_RECORDING") as PrivacyMode,
    recordingEnabled: Boolean(raw?.recording_enabled ?? raw?.recordingEnabled),
    consentRequired: raw?.consent_required ?? raw?.consentRequired ?? true,
    // PENDING blocks joining. Any other default would let a malformed
    // response open a call nobody agreed to.
    consentStatus: (CONSENT_STATUSES.includes(consent) ? consent : "PENDING") as ConsentStatus,
    scheduledStartUtc: raw?.scheduled_start_utc ?? raw?.scheduledStartUtc ?? undefined,
    expectedDurationMinutes: Number(
      raw?.expected_duration_minutes ?? raw?.expectedDurationMinutes ?? 60),
    purpose: String(raw?.purpose ?? ""),
  };
}

/** Mirrors the server's `JOINABLE_CONSENT`: only GRANTED or NOT_REQUIRED. */
export function consentPermitsJoin(session: VirtualSession): boolean {
  return session.consentStatus === "GRANTED" || session.consentStatus === "NOT_REQUIRED";
}

/** Whether a join is worth offering at all. The server enforces every one of
 *  these independently; this only avoids showing a button that would fail. */
export function canJoin(session: VirtualSession): boolean {
  return consentPermitsJoin(session)
    && session.state !== "ENDED"
    && session.state !== "CANCELLED";
}

/** True only for the one mode in which a recording can exist. Mirrors the
 *  server's `RECORDING_MODES`, which is a single-element set on purpose. */
export function modePermitsRecording(mode: PrivacyMode): boolean {
  return mode === "SERVER_RECORDED";
}
