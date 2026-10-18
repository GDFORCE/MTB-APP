/**
 * Pure logic for the protocol identity gate (P2) - no React, no network - so the
 * upload screens and the review screen share one interpretation of the API and
 * it can be tested with `node --test`.
 *
 * The API answers a quarantined upload with HTTP 409 and a structured `detail`
 * (never a plain string), so a blocked upload can never be mistaken for either an
 * extraction success or a generic "could not extract" failure.
 */

export type IdentityClassification = "MATCH" | "MISMATCH" | "UNCERTAIN" | "DUPLICATE" | "NEW_VERSION";
export type ReviewStatus = "NOT_REQUIRED" | "PENDING_REVIEW" | "APPROVED" | "REJECTED";
export type ReviewAction =
  | "CORRECT_EXPECTED_IDENTITY" | "CONFIRM_DOCUMENT_IDENTITY" | "REJECT" | "KEEP_QUARANTINED";

export type IdentityIssue = {
  code: string;
  severity: "BLOCKING" | "INFO";
  message: string;
  page?: number | null;
  snippet?: string | null;
};

export type IdentityEvidence = {
  kind: string;
  value: string;
  normalized: string;
  page: number;
  snippet: string;
  authoritative: boolean;
  context: string;
};

export type IdentityReview = {
  id: string;
  action: ReviewAction;
  actor_id: string;
  reason: string;
  expected_revision: number;
  resulting_revision: number;
  classification_before: IdentityClassification;
  classification_after: IdentityClassification | null;
  review_status_after: ReviewStatus;
  corrected_identifier: string | null;
  created_at: string;
};

export type ProtocolIdentity = {
  id: string;
  classification: IdentityClassification;
  original_classification: IdentityClassification;
  review_status: ReviewStatus;
  revision: number;
  permitted: boolean;
  filename: string;
  trial_id?: string | null;
  stage: string;
  created_at: string;
  issue_codes: string[];
  allowed_actions: ReviewAction[];
  duplicate_of_id?: string | null;
  predecessor_id?: string | null;
  binary_sha256: string;
  text_sha256?: string | null;
  text_verified_full_text: boolean;
  owner_user_id?: string;
  // present on detail views
  issues?: IdentityIssue[];
  expected?: { kind: string; value: string; normalized: string; source: string }[];
  document_identifiers?: IdentityEvidence[];
  title?: { value: string; page: number; snippet: string } | null;
  sponsor?: { value: string; page: number; snippet: string } | null;
  version_label?: { value: string; page: number; snippet: string } | null;
  version_date?: string | null;
  registry?: { status: string; provider?: string | null; detail?: string };
  text_coverage?: { pages_total: number; pages_with_text: number; pages_without_text: number[] };
  suggested_correction?: { expected_identifier: string; kind: string } | null;
  reused_extraction?: boolean;
  reviews?: IdentityReview[];
  can_review?: boolean;
};

export type IdentityBlockCode =
  | "PROTOCOL_IDENTITY_BLOCKED"
  | "PROTOCOL_IDENTITY_UNVERIFIED"
  | "PROTOCOL_IDENTITY_TRIAL_MISMATCH"
  | "EXPECTED_IDENTITY_CONFLICT"
  | "EXTRACTION_IN_PROGRESS"
  | "EXTRACTION_NOT_READY";

export type IdentityBlock = {
  code: IdentityBlockCode;
  workflow_state?: string;
  message: string;
  identity?: ProtocolIdentity;
  next_action?: string | null;
};

const BLOCK_CODES = new Set<string>([
  "PROTOCOL_IDENTITY_BLOCKED", "PROTOCOL_IDENTITY_UNVERIFIED", "PROTOCOL_IDENTITY_TRIAL_MISMATCH",
  "EXPECTED_IDENTITY_CONFLICT", "EXTRACTION_IN_PROGRESS", "EXTRACTION_NOT_READY",
]);

/** The structured identity outcome carried by an API error, or null for any other error. */
export function identityBlockFrom(error: unknown): IdentityBlock | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  const detail = response?.data?.detail as Partial<IdentityBlock> | string | undefined;
  if (!detail || typeof detail === "string") return null;
  if (response?.status !== 409 && response?.status !== 422) return null;
  if (!detail.code || !BLOCK_CODES.has(detail.code)) return null;
  return {
    code: detail.code as IdentityBlockCode,
    workflow_state: detail.workflow_state,
    message: detail.message || "This protocol document could not be verified.",
    identity: detail.identity,
    next_action: detail.next_action ?? null,
  };
}

const CLASSIFICATION_LABEL: Record<IdentityClassification, string> = {
  MATCH: "Identity verified",
  MISMATCH: "Wrong protocol",
  UNCERTAIN: "Identity not verified",
  DUPLICATE: "Already uploaded",
  NEW_VERSION: "New version",
};

export function classificationLabel(value: IdentityClassification): string {
  return CLASSIFICATION_LABEL[value] ?? value;
}

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  NOT_REQUIRED: "No review needed",
  PENDING_REVIEW: "Awaiting review",
  APPROVED: "Approved by reviewer",
  REJECTED: "Rejected by reviewer",
};

export function reviewStatusLabel(value: ReviewStatus): string {
  return REVIEW_LABEL[value] ?? value;
}

const ACTION_LABEL: Record<ReviewAction, string> = {
  CORRECT_EXPECTED_IDENTITY: "Correct the expected identity",
  CONFIRM_DOCUMENT_IDENTITY: "Confirm this document's identity",
  REJECT: "Reject this document",
  KEEP_QUARANTINED: "Keep in quarantine",
};

export function actionLabel(value: ReviewAction): string {
  return ACTION_LABEL[value] ?? value;
}

/** The sentence a person reads first. */
export function blockHeadline(block: IdentityBlock): string {
  switch (block.code) {
    case "PROTOCOL_IDENTITY_UNVERIFIED":
      return "This prepared schedule was not identity-verified";
    case "EXTRACTION_IN_PROGRESS":
      return "This protocol is already being extracted";
    case "EXTRACTION_NOT_READY":
      return "The reviewed protocol has not been extracted yet";
    case "PROTOCOL_IDENTITY_TRIAL_MISMATCH":
      return "This document was verified for a different trial";
    case "EXPECTED_IDENTITY_CONFLICT":
      return "The protocol ID does not match this trial";
    default: {
      if (block.identity?.review_status === "REJECTED") return "This document was rejected";
      const classification = block.identity?.classification;
      if (classification === "MISMATCH") return "This file is not the protocol it was submitted as";
      return "This document's identity could not be verified";
    }
  }
}

/** Blocking issue texts first, each with the page it was found on. */
export function issueLines(identity?: ProtocolIdentity): string[] {
  const issues = identity?.issues ?? [];
  return issues
    .filter((item) => item.severity === "BLOCKING")
    .map((item) => (item.page ? `${item.message} (page ${item.page})` : item.message));
}

/**
 * Why a save / consume action must stay disabled, or null when it may proceed.
 * `identity` is the identity block an upload response carried (when it has one).
 */
export function savingDisabledReason(
  identity: ProtocolIdentity | null | undefined, block: IdentityBlock | null,
): string | null {
  if (block) return `${blockHeadline(block)}. ${block.next_action || block.message}`;
  if (identity && !identity.permitted) {
    return identity.review_status === "PENDING_REVIEW"
      ? "This protocol is waiting for an identity review before it can be saved."
      : "This protocol did not pass identity verification.";
  }
  return null;
}

/** A short status line for a successful upload that was a repeat or an amendment. */
export function successNote(identity?: ProtocolIdentity | null): string | null {
  if (!identity) return null;
  if (identity.classification === "DUPLICATE") {
    return identity.reused_extraction
      ? "This protocol was already uploaded; its stored schedule was reused."
      : "This protocol was already uploaded.";
  }
  if (identity.classification === "NEW_VERSION") {
    return "This is a new version of a protocol you already uploaded. It will be saved as a "
      + "new draft; the earlier version and any patient assignments are unchanged.";
  }
  return null;
}

/** Client-side check of a reviewer decision; the server remains authoritative. */
export function validateResolution(
  action: ReviewAction, reason: string, identifier: string,
): string | null {
  if (!reason.trim()) return "Give a reason for this decision.";
  if ((action === "CORRECT_EXPECTED_IDENTITY" || action === "CONFIRM_DOCUMENT_IDENTITY")
      && !identifier.trim()) {
    return "Enter the protocol identifier you are confirming.";
  }
  return null;
}

export function resolutionBody(
  identity: ProtocolIdentity, action: ReviewAction, reason: string, identifier: string,
) {
  const needsIdentifier = action === "CORRECT_EXPECTED_IDENTITY" || action === "CONFIRM_DOCUMENT_IDENTITY";
  return {
    action,
    reason: reason.trim(),
    expected_revision: identity.revision,
    corrected_identifier: needsIdentifier ? identifier.trim() : null,
  };
}

/** What a resume outcome means to the reviewer. */
export function resumeNote(resume: { status: string; retryable?: boolean; message?: string } | null | undefined): string {
  if (!resume) return "No extraction was started.";
  switch (resume.status) {
    case "COMPLETED": return "Approved. The protocol was extracted; the uploader can now collect the schedule.";
    case "ALREADY_HANDLED": return "Approved. Extraction was already started or finished.";
    case "PROVIDER_UNAVAILABLE":
      return "Approved. Extraction is not available right now; retry from this screen when it is.";
    default: return `Approved, but extraction did not finish${resume.message ? `: ${resume.message}` : "."}`
      + (resume.retryable ? " You can retry." : "");
  }
}
