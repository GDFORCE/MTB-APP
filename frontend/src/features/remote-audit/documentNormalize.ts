// Remote Audit — pure document normalizers (Phase 4).
//
// Split from the API layer so they stay testable with `node --test`. The
// distinction these normalizers must never blur is `extracted` vs `confirmed`:
// one is a machine's guess and the other is a person's word, and the review UI
// renders them differently precisely so nobody confirms a value by looking at
// it twice.
import type {
  DocumentCheckRun,
  DocumentExtraction,
  DocumentFinding,
  DocumentItem,
  DocumentStatus,
  DocumentUploadTarget,
} from "./types";

const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

const VALID_CONFIDENCE = ["high", "medium", "low"] as const;

export function normalizeExtraction(raw: any): DocumentExtraction | null {
  if (!raw || typeof raw !== "object") return null;
  const confidence = text(raw?.confidence);
  return {
    source: text(raw?.source, "unknown"),
    model: raw?.model ?? undefined,
    fields: raw?.fields && typeof raw.fields === "object" ? raw.fields : {},
    // An unrecognized or missing confidence is treated as null, never as
    // "high". The backend applies the same rule; agreeing with it here keeps
    // a malformed response from looking more trustworthy than it is.
    confidence: (VALID_CONFIDENCE as readonly string[]).includes(confidence)
      ? (confidence as DocumentExtraction["confidence"])
      : null,
    confidenceNotes: raw?.confidence_notes ?? raw?.confidenceNotes ?? null,
    extractedAt: raw?.extracted_at ?? raw?.extractedAt ?? undefined,
  };
}

export function normalizeDocument(raw: any): DocumentItem {
  return {
    id: text(raw?.id),
    auditId: text(raw?.audit_id ?? raw?.auditId),
    documentType: text(raw?.document_type ?? raw?.documentType, "document"),
    displayName: text(raw?.display_name ?? raw?.displayName, "Document"),
    mediaType: text(raw?.media_type ?? raw?.mediaType, "application/pdf"),
    // QUARANTINED is the safe default for an unrecognized status: isolated
    // and not yet judged. Any post-check default would show an unscanned
    // file as if it had passed something.
    status: text(raw?.status, "QUARANTINED") as DocumentStatus,
    uploadedBy: text(raw?.uploaded_by ?? raw?.uploadedBy),
    uploadedAt: raw?.uploaded_at ?? raw?.uploadedAt ?? undefined,
    contentBlocked: Boolean(raw?.content_blocked ?? raw?.contentBlocked),
    extracted: normalizeExtraction(raw?.extracted),
    confirmed:
      raw?.confirmed && typeof raw.confirmed === "object" ? raw.confirmed : null,
    confirmedBy: raw?.confirmed_by ?? raw?.confirmedBy ?? null,
    expiresOn: raw?.expires_on ?? raw?.expiresOn ?? null,
    decisionReasonCode: raw?.decision_reason_code ?? raw?.decisionReasonCode ?? null,
    decisionNote: raw?.decision_note ?? raw?.decisionNote ?? null,
    supersedesDocumentId: raw?.supersedes_document_id ?? raw?.supersedesDocumentId ?? null,
    supersededByDocumentId: raw?.superseded_by_document_id ?? raw?.supersededByDocumentId ?? null,
  };
}

export function normalizeFinding(raw: any): DocumentFinding {
  return {
    code: text(raw?.code, "UNKNOWN"),
    severity: text(raw?.severity, "info"),
    explanation: text(raw?.explanation),
    field: raw?.field ?? undefined,
    measuredValue: raw?.measured_value ?? raw?.measuredValue,
    threshold: raw?.threshold,
    source: raw?.source ?? undefined,
  };
}

export function normalizeCheckRun(raw: any): DocumentCheckRun | null {
  if (!raw) return null;
  return {
    checkRunId: Number(raw?.check_run_id ?? raw?.checkRunId ?? 1),
    outcome: text(raw?.outcome, "PROCESSING"),
    ruleSetVersion: text(raw?.rule_set_version ?? raw?.ruleSetVersion),
    findings: Array.isArray(raw?.findings) ? raw.findings.map(normalizeFinding) : [],
    checks: raw?.checks && typeof raw.checks === "object" ? raw.checks : {},
    completedAt: raw?.completed_at_utc ?? raw?.completedAt ?? undefined,
    supersedesCheckId: raw?.supersedes_check_id ?? raw?.supersedesCheckId ?? null,
  };
}

export function normalizeUploadTarget(raw: any): DocumentUploadTarget {
  return {
    document: normalizeDocument(raw),
    serverMediated: Boolean(raw?.server_mediated ?? raw?.serverMediated),
    serverUploadUrl: raw?.server_upload_url ?? raw?.serverUploadUrl ?? null,
    upload: raw?.upload ?? null,
  };
}

/** Statuses a reviewer may act on. Mirrors documents.py's
 *  `REVIEWER_VISIBLE_STATUSES` — the server enforces it; this only keeps the
 *  UI from offering buttons that would be refused. */
export const REVIEWER_VISIBLE: ReadonlySet<string> = new Set([
  "TECHNICALLY_VALID", "REVIEW_REQUIRED", "ACCEPTED", "REJECTED", "SUPERSEDED", "EXPIRED",
]);

export function isDecidable(status: string): boolean {
  return status === "TECHNICALLY_VALID" || status === "REVIEW_REQUIRED";
}

/** True when a value came from a machine and no human has confirmed it.
 *  Drives the "suggested" styling in the review UI. */
export function isUnconfirmed(doc: DocumentItem, field: string): boolean {
  const confirmed = doc.confirmed ?? {};
  return (
    doc.extracted != null &&
    doc.extracted.fields[field] != null &&
    confirmed[field] == null
  );
}
