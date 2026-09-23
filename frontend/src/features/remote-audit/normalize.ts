// Remote Audit — pure response normalizers.
//
// Split out of api.ts (which pulls in axios + react-native) so these stay
// testable with `node --test`, matching this codebase's frontend test
// convention. They matter more than normalizers usually do: the evidence
// viewer's entire honesty guarantee is that it renders the engine's reasons,
// so a normalizer that silently drops `reasons` would turn a reasoned verdict
// into a bare verdict — exactly what spec §15.1 forbids.
import type {
  AuditSummary,
  CaptureMode,
  CaptureSession,
  EvidenceItem,
  UploadIntent,
  VerificationReason,
  VerificationResult,
} from "./types";

export const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

export function normalizeSession(raw: any): CaptureSession {
  return {
    id: text(raw?.id),
    auditId: text(raw?.audit_id ?? raw?.auditId),
    nonce: text(raw?.nonce),
    expiresAt: text(raw?.expires_at ?? raw?.expiresAt),
    requestedMediaType: text(raw?.requested_media_type ?? raw?.requestedMediaType, "image/jpeg"),
    captureMode: text(raw?.capture_mode ?? raw?.captureMode, "LIVE_IN_APP") as CaptureMode,
  };
}

export function normalizeUploadIntent(raw: any): UploadIntent {
  return {
    evidenceId: text(raw?.evidence_id ?? raw?.evidenceId),
    objectKey: text(raw?.object_key ?? raw?.objectKey),
    serverMediated: Boolean(raw?.server_mediated ?? raw?.serverMediated),
    serverUploadUrl: raw?.server_upload_url ?? raw?.serverUploadUrl ?? undefined,
    upload: raw?.upload ?? null,
  };
}

export function normalizeEvidence(raw: any): EvidenceItem {
  return {
    id: text(raw?.id),
    auditId: text(raw?.audit_id ?? raw?.auditId),
    // QUARANTINED is the safe default for an unrecognized status: it means
    // "isolated, not yet judged". Defaulting to PASS-like anything would let a
    // backend rename quietly present unverified evidence as acceptable.
    status: text(raw?.status, "QUARANTINED") as EvidenceItem["status"],
    captureMode: text(raw?.capture_mode ?? raw?.captureMode, "LIVE_IN_APP") as CaptureMode,
    mediaType: text(raw?.media_type ?? raw?.mediaType, "image/jpeg"),
    displayName: raw?.display_name ?? raw?.displayName ?? undefined,
    createdAt: raw?.created_at ?? raw?.createdAt ?? undefined,
  };
}

export function normalizeReason(raw: any): VerificationReason {
  return {
    code: text(raw?.code, "UNKNOWN"),
    severity: text(raw?.severity, "info"),
    explanation: text(raw?.explanation),
    measuredValue: raw?.measured_value ?? raw?.measuredValue,
    threshold: raw?.threshold,
    source: raw?.source ?? undefined,
  };
}

export function normalizeVerification(raw: any): VerificationResult | null {
  if (!raw) return null;
  return {
    overallState: text(raw?.overall_state ?? raw?.overallState, "PROCESSING") as VerificationResult["overallState"],
    ruleSetVersion: text(raw?.rule_set_version ?? raw?.ruleSetVersion),
    reasons: Array.isArray(raw?.reasons) ? raw.reasons.map(normalizeReason) : [],
    signals: raw?.signals && typeof raw.signals === "object" ? raw.signals : {},
    verificationRunId: Number(raw?.verification_run_id ?? raw?.verificationRunId ?? 1),
    completedAt: raw?.completed_at_utc ?? raw?.completedAt ?? undefined,
  };
}

export function normalizeAudit(raw: any): AuditSummary {
  return {
    id: text(raw?.id),
    state: text(raw?.state, "draft"),
    siteId: text(raw?.site_id ?? raw?.siteId),
  };
}
