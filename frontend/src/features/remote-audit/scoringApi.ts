// Remote Audit — scoring, decision and virtual-visit API clients (Phases 6-7).
import { api, API_BASE } from "@/src/api/client";

import { normalizeCapa } from "./queries";
import type { Capa } from "./queries";
import { normalizeDecision, normalizeScoreRun } from "./scoring";
import { normalizeSession } from "./virtualVisits";
import type { AuditDecision, FinalDecision, ScoreRun } from "./scoring";
import type { PrivacyMode, VirtualSession } from "./virtualVisits";

// Re-exported so screens import their session types and helpers from one
// place, while the pure logic itself stays testable without react-native.
export { canJoin, consentPermitsJoin, modePermitsRecording, normalizeSession } from "./virtualVisits";
export type { ConsentStatus, PrivacyMode, SessionState, VirtualSession } from "./virtualVisits";

const uuid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const idempotent = (key?: string) => ({ headers: { "Idempotency-Key": key ?? uuid() } });

// ── Scoring ─────────────────────────────────────────────────────────────────

export interface RequirementResultInput {
  requirementId: string;
  sectionId: string;
  outcome: "MET" | "PARTIAL" | "NOT_MET" | "NOT_APPLICABLE" | "MISSING" | "WAIVED";
  isCritical?: boolean;
  weight?: number;
  evidenceConfidence?: "HIGH" | "LIMITED" | "UNVERIFIED" | "FAILED" | null;
  waiverReason?: string;
  note?: string;
}

export async function scoreAudit(auditId: string, input: {
  templateId: string;
  results: RequirementResultInput[];
}): Promise<ScoreRun> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/score`,
    {
      template_id: input.templateId,
      results: input.results.map((r) => ({
        requirement_id: r.requirementId,
        section_id: r.sectionId,
        outcome: r.outcome,
        is_critical: r.isCritical ?? false,
        weight: r.weight ?? 1.0,
        evidence_confidence: r.evidenceConfidence ?? null,
        waiver_reason: r.waiverReason ?? null,
        note: r.note ?? null,
      })),
    },
    idempotent(),
  );
  return normalizeScoreRun(data)!;
}

export async function fetchScore(auditId: string): Promise<{
  latest: ScoreRun | null;
  history: ScoreRun[];
}> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/score`);
  return {
    latest: normalizeScoreRun(data?.latest),
    history: Array.isArray(data?.history)
      ? (data.history.map(normalizeScoreRun).filter(Boolean) as ScoreRun[])
      : [],
  };
}

// ── Decision ────────────────────────────────────────────────────────────────

/** Mint a short-lived, action-scoped step-up token. The final decision
 *  endpoint consumes it once and refuses without it — in production it can
 *  only be issued from an MFA-authenticated session. */
export async function requestStepUpToken(actionScope: string, password: string): Promise<string> {
  const { data } = await api.post(`/remote-audit/step-up`, {
    action_scope: actionScope,
    password,
  });
  return String(data?.token ?? "");
}

export async function recordDecision(auditId: string, input: {
  decision: FinalDecision;
  stepUpToken: string;
  overrideReason?: string;
  linkedCapaIds?: string[];
  signatureStatement?: string;
}): Promise<AuditDecision> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/decision`,
    {
      decision: input.decision,
      step_up_token: input.stepUpToken,
      override_reason: input.overrideReason ?? null,
      linked_capa_ids: input.linkedCapaIds ?? [],
      signature_statement: input.signatureStatement ?? "",
    },
    idempotent(),
  );
  return normalizeDecision(data)!;
}

export async function fetchDecision(auditId: string): Promise<{
  latest: AuditDecision | null;
  history: AuditDecision[];
}> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/decision`);
  return {
    latest: normalizeDecision(data?.latest),
    history: Array.isArray(data?.history)
      ? (data.history.map(normalizeDecision).filter(Boolean) as AuditDecision[])
      : [],
  };
}

export async function fetchAuditCapas(auditId: string): Promise<Capa[]> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/capas`);
  return Array.isArray(data) ? data.map(normalizeCapa) : [];
}

/** The report URL. Returned rather than fetched because the PDF is served as
 *  an authenticated attachment — the caller hands it to a viewer or a share
 *  sheet rather than rendering it inline. */
export function auditReportUrl(auditId: string): string {
  return `${API_BASE}/remote-audit/audits/${auditId}/report.pdf`;
}

// ── Virtual visits ──────────────────────────────────────────────────────────

export async function scheduleSession(auditId: string, input: {
  scheduledStartUtc: string;
  privacyMode: PrivacyMode;
  consentRequired?: boolean;
  expectedDurationMinutes?: number;
  purpose?: string;
}): Promise<VirtualSession> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/virtual-sessions`,
    {
      scheduled_start_utc: input.scheduledStartUtc,
      privacy_mode: input.privacyMode,
      consent_required: input.consentRequired ?? true,
      expected_duration_minutes: input.expectedDurationMinutes ?? 60,
      purpose: input.purpose ?? "",
    },
    idempotent(),
  );
  return normalizeSession(data);
}

export async function fetchSessions(auditId: string): Promise<{
  sessions: VirtualSession[];
  livekitConfigured: boolean;
}> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/virtual-sessions`);
  return {
    sessions: Array.isArray(data?.sessions) ? data.sessions.map(normalizeSession) : [],
    livekitConfigured: Boolean(data?.livekit_configured),
  };
}

/** `privacyModeShown` is what the UI actually told this person. The server
 *  checks it against the session's real mode, so consent given for a
 *  no-recording call can never be applied to a recorded one. */
export async function recordConsent(sessionId: string, input: {
  granted: boolean;
  privacyModeShown: PrivacyMode;
}): Promise<VirtualSession> {
  const { data } = await api.post(`/remote-audit/virtual-sessions/${sessionId}/consent`, {
    granted: input.granted,
    privacy_mode_shown: input.privacyModeShown,
  });
  return normalizeSession(data);
}

export async function joinSession(sessionId: string, role: "host" | "presenter" | "observer") {
  const { data } = await api.post(`/remote-audit/virtual-sessions/${sessionId}/join`, { role });
  return {
    token: String(data?.token ?? ""),
    url: String(data?.url ?? ""),
    room: String(data?.room ?? ""),
    role: String(data?.role ?? role),
    canPublish: Boolean(data?.can_publish),
    privacyMode: (data?.privacy_mode ?? "SERVER_NO_RECORDING") as PrivacyMode,
    recordingEnabled: Boolean(data?.recording_enabled),
    expiresAt: String(data?.expires_at ?? ""),
  };
}
