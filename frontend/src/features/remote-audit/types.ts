// Remote Audit — types mirroring the backend's API shapes
// (backend/remote_audit_routes.py). Kept deliberately narrow: only what the
// screens actually render, normalized from loose backend JSON in api.ts.

export type CaptureMode = "LIVE_IN_APP" | "GALLERY_HISTORICAL";

export type EvidenceState =
  | "QUARANTINED"
  | "PROCESSING"
  | "PROCESSING_BLOCKED"
  | "PROCESSING_FAILED"
  | "PASS"
  | "PASS_WITH_LIMITED_CONFIDENCE"
  | "REVIEW"
  | "SUSPICIOUS_REVIEW"
  | "FAIL_TECHNICAL"
  | "HOLD";

export interface CaptureSession {
  id: string;
  auditId: string;
  nonce: string;
  expiresAt: string;
  requestedMediaType: string;
  captureMode: CaptureMode;
}

export interface UploadIntent {
  evidenceId: string;
  objectKey: string;
  serverMediated: boolean;
  serverUploadUrl?: string;
  // Present only when the storage backend issued a real presigned POST.
  upload?: { url: string; fields: Record<string, string> } | null;
}

export interface EvidenceItem {
  id: string;
  auditId: string;
  status: EvidenceState;
  captureMode: CaptureMode;
  mediaType: string;
  displayName?: string;
  createdAt?: string;
}

/** One human-readable reason behind a verification verdict. The spec is
 *  explicit that a reviewer must never see only a score — these are what the
 *  evidence viewer actually renders. */
export interface VerificationReason {
  code: string;
  severity: string;
  explanation: string;
  measuredValue?: unknown;
  threshold?: unknown;
  source?: string;
}

export interface VerificationResult {
  overallState: EvidenceState;
  ruleSetVersion: string;
  reasons: VerificationReason[];
  signals: Record<string, unknown>;
  verificationRunId: number;
  completedAt?: string;
}

export interface CaptureLocation {
  lat: number;
  lon: number;
  accuracyM: number | null;
  locationAgeMs: number | null;
  mocked: boolean;
}

export interface AuditSummary {
  id: string;
  state: string;
  siteId: string;
}

// ── Phase 4: Documents (spec §10) ───────────────────────────────────────────

export type DocumentStatus =
  | "QUARANTINED"
  | "PROCESSING"
  | "PROCESSING_BLOCKED"
  | "PROCESSING_FAILED"
  | "TECHNICALLY_VALID"
  | "REVIEW_REQUIRED"
  | "ACCEPTED"
  | "REJECTED"
  | "SUPERSEDED"
  | "EXPIRED";

/** Machine-extracted field values. Deliberately a separate type from the
 *  confirmed values so the UI cannot render one as the other by accident —
 *  the whole extract → review → commit rule depends on that distinction
 *  staying visible to the person doing the confirming. */
export interface DocumentExtraction {
  source: string;
  model?: string;
  fields: Record<string, unknown>;
  confidence: "high" | "medium" | "low" | null;
  confidenceNotes?: string | null;
  extractedAt?: string;
}

export interface DocumentItem {
  id: string;
  auditId: string;
  documentType: string;
  displayName: string;
  mediaType: string;
  status: DocumentStatus;
  uploadedBy: string;
  uploadedAt?: string;
  contentBlocked: boolean;
  extracted: DocumentExtraction | null;
  confirmed: Record<string, unknown> | null;
  confirmedBy?: string | null;
  expiresOn?: string | null;
  decisionReasonCode?: string | null;
  decisionNote?: string | null;
  supersedesDocumentId?: string | null;
  supersededByDocumentId?: string | null;
}

export interface DocumentFinding {
  code: string;
  severity: string;
  explanation: string;
  field?: string;
  measuredValue?: unknown;
  threshold?: unknown;
  source?: string;
}

export interface DocumentCheckRun {
  checkRunId: number;
  outcome: string;
  ruleSetVersion: string;
  findings: DocumentFinding[];
  checks: Record<string, unknown>;
  completedAt?: string;
  supersedesCheckId?: string | null;
}

export interface DocumentUploadTarget {
  document: DocumentItem;
  serverMediated: boolean;
  serverUploadUrl?: string | null;
  upload?: { url: string; fields: Record<string, string> } | null;
}
