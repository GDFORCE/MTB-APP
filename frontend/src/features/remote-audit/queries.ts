// Remote Audit — queries and CAPAs (Phase 5, spec §11).
//
// Types, pure normalizers, and the state predicates the screens use to decide
// which actions to offer. These mirror app/services/remote_audit/queries.py:
// the server is the authority, and agreeing with it here only keeps the UI
// from offering buttons that would be refused.
//
// One rule the UI must not soften: `is_overdue` is computed by the server from
// the due date, never stored. This module recomputes it locally the same way
// for optimistic rendering, and both answers come from the same arithmetic —
// so a cached list cannot show a CAPA as on-time after its date has passed.

export type QueryStatus = "OPEN" | "RESPONDED" | "CLOSED" | "REOPENED" | "CANCELLED";
export type CapaStatus =
  | "OPEN" | "IN_PROGRESS" | "COMPLETED" | "CLOSED" | "REOPENED" | "CANCELLED";
export type Criticality = "critical" | "major" | "minor";

export interface QueryResponse {
  id: string;
  sequence: number;
  authorId: string;
  body: string;
  attachmentIds: string[];
  createdAt?: string;
}

export interface AuditQuery {
  id: string;
  auditId: string;
  title: string;
  detail: string;
  criticality: Criticality;
  status: QueryStatus;
  linkedEntityType?: string | null;
  linkedEntityId?: string | null;
  dueDate?: string | null;
  raisedBy: string;
  closedBy?: string | null;
  closeReasonCode?: string | null;
  lastRejectionReasonCode?: string | null;
  reopenCount: number;
  reopenReason?: string | null;
  version: number;
  createdAt?: string;
  responses: QueryResponse[];
}

export interface EffectivenessReview {
  outcome: "EFFECTIVE" | "INEFFECTIVE";
  note: string;
  reviewedBy: string;
  reviewedAt?: string;
}

export interface Capa {
  id: string;
  auditId: string;
  queryId?: string | null;
  title: string;
  description: string;
  criticality: Criticality;
  status: CapaStatus;
  ownerUserId: string;
  dueDate: string;
  completedBy?: string | null;
  completionNote?: string | null;
  effectivenessReview: EffectivenessReview | null;
  reopenCount: number;
  isOverdue: boolean;
  version: number;
}

const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

const CRITICALITIES = ["critical", "major", "minor"] as const;

function criticality(raw: unknown): Criticality {
  const value = text(raw);
  // An unrecognized criticality reads as "minor" rather than "critical": the
  // UI must not manufacture a blocking finding out of a malformed field. The
  // server is what actually blocks decisions, on its own stored value.
  return (CRITICALITIES as readonly string[]).includes(value) ? (value as Criticality) : "minor";
}

export function normalizeResponse(raw: any): QueryResponse {
  return {
    id: text(raw?.id),
    sequence: Number(raw?.sequence ?? 0),
    authorId: text(raw?.author_id ?? raw?.authorId),
    body: text(raw?.body),
    attachmentIds: Array.isArray(raw?.attachment_ids ?? raw?.attachmentIds)
      ? (raw.attachment_ids ?? raw.attachmentIds)
      : [],
    createdAt: raw?.created_at ?? raw?.createdAt ?? undefined,
  };
}

export function normalizeQuery(raw: any): AuditQuery {
  return {
    id: text(raw?.id),
    auditId: text(raw?.audit_id ?? raw?.auditId),
    title: text(raw?.title, "Untitled query"),
    detail: text(raw?.detail),
    criticality: criticality(raw?.criticality),
    // OPEN is the safe default: it keeps an unrecognized status visible and
    // actionable rather than quietly reading as resolved.
    status: text(raw?.status, "OPEN") as QueryStatus,
    linkedEntityType: raw?.linked_entity_type ?? raw?.linkedEntityType ?? null,
    linkedEntityId: raw?.linked_entity_id ?? raw?.linkedEntityId ?? null,
    dueDate: raw?.due_date ?? raw?.dueDate ?? null,
    raisedBy: text(raw?.raised_by ?? raw?.raisedBy),
    closedBy: raw?.closed_by ?? raw?.closedBy ?? null,
    closeReasonCode: raw?.close_reason_code ?? raw?.closeReasonCode ?? null,
    lastRejectionReasonCode:
      raw?.last_rejection_reason_code ?? raw?.lastRejectionReasonCode ?? null,
    reopenCount: Number(raw?.reopen_count ?? raw?.reopenCount ?? 0),
    reopenReason: raw?.reopen_reason ?? raw?.reopenReason ?? null,
    version: Number(raw?.version ?? 1),
    createdAt: raw?.created_at ?? raw?.createdAt ?? undefined,
    responses: Array.isArray(raw?.responses) ? raw.responses.map(normalizeResponse) : [],
  };
}

export function normalizeEffectivenessReview(raw: any): EffectivenessReview | null {
  if (!raw || typeof raw !== "object") return null;
  const outcome = text(raw?.outcome);
  if (outcome !== "EFFECTIVE" && outcome !== "INEFFECTIVE") return null;
  return {
    outcome,
    note: text(raw?.note),
    reviewedBy: text(raw?.reviewed_by ?? raw?.reviewedBy),
    reviewedAt: raw?.reviewed_at ?? raw?.reviewedAt ?? undefined,
  };
}

export function normalizeCapa(raw: any): Capa {
  const dueDate = text(raw?.due_date ?? raw?.dueDate);
  const status = text(raw?.status, "OPEN") as CapaStatus;
  return {
    id: text(raw?.id),
    auditId: text(raw?.audit_id ?? raw?.auditId),
    queryId: raw?.query_id ?? raw?.queryId ?? null,
    title: text(raw?.title, "Untitled CAPA"),
    description: text(raw?.description),
    criticality: criticality(raw?.criticality),
    status,
    ownerUserId: text(raw?.owner_user_id ?? raw?.ownerUserId),
    dueDate,
    completedBy: raw?.completed_by ?? raw?.completedBy ?? null,
    completionNote: raw?.completion_note ?? raw?.completionNote ?? null,
    effectivenessReview: normalizeEffectivenessReview(
      raw?.effectiveness_review ?? raw?.effectivenessReview),
    reopenCount: Number(raw?.reopen_count ?? raw?.reopenCount ?? 0),
    // Trust the server's answer when it sent one; otherwise recompute from the
    // same arithmetic rather than defaulting to "fine".
    isOverdue: raw?.is_overdue ?? raw?.isOverdue ?? computeOverdue(status, dueDate),
    version: Number(raw?.version ?? 1),
  };
}

/** CAPA states that still represent outstanding work. Mirrors the server's
 *  `CAPA_OUTSTANDING_STATES` — CANCELLED is resolved, not outstanding. */
export const CAPA_OUTSTANDING: ReadonlySet<string> = new Set([
  "OPEN", "IN_PROGRESS", "COMPLETED", "REOPENED",
]);

export function computeOverdue(status: string, dueDate: string, asOf?: Date): boolean {
  if (!CAPA_OUTSTANDING.has(status) || !dueDate) return false;
  const due = new Date(`${dueDate.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(due.getTime())) return false;
  const today = asOf ?? new Date();
  const todayUtc = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  // Strictly before today: a CAPA has the whole of its due date.
  return due.getTime() < todayUtc;
}

// ── Which actions a screen may offer ────────────────────────────────────────

/** The site answers only while the ball is in its court. */
export function canRespond(status: QueryStatus): boolean {
  return status === "OPEN" || status === "REOPENED";
}

/** A reviewer rules only on something that has actually been answered. */
export function canDecide(status: QueryStatus): boolean {
  return status === "RESPONDED" || status === "REOPENED";
}

export function canReopen(status: QueryStatus): boolean {
  return status === "CLOSED";
}

export function canCancelQuery(status: QueryStatus): boolean {
  return status === "OPEN" || status === "RESPONDED" || status === "REOPENED";
}

export function capaNextAction(capa: Capa, viewerId: string): "START" | "COMPLETE" | null {
  // Only the accountable owner moves the work along. A reviewer watching the
  // CAPA is not offered a button that the server would refuse.
  if (capa.ownerUserId !== viewerId) return null;
  if (capa.status === "OPEN" || capa.status === "REOPENED") return "START";
  if (capa.status === "IN_PROGRESS") return "COMPLETE";
  return null;
}

/** Effectiveness review is offered only on COMPLETED, and never to whoever
 *  completed it — mirroring the server's SELF_REVIEW_FORBIDDEN rule. */
export function canReviewEffectiveness(capa: Capa, viewerId: string): boolean {
  return capa.status === "COMPLETED" && capa.completedBy !== viewerId;
}
