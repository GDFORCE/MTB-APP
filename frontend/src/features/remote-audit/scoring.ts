// Remote Audit — scoring and decision presentation (Phase 7, spec §13).
//
// THE RULE THIS FILE ENFORCES ON THE UI: the two axes are never combined.
//
// Compliance answers "did the site meet the requirements?"; evidence
// confidence answers "how far can we rely on the evidence that says so?".
// A site with perfect compliance on evidence that all failed verification is
// not "pretty good" — it is fully compliant on paper and unverifiable in
// practice, and a reviewer needs both numbers to see that. Any helper here
// that averaged them would destroy exactly the information the module exists
// to produce, so there isn't one.
//
// The second rule: blockers render before the score. `hasBlockers` and
// `sortedBlockers` exist so a screen can put them first without each screen
// re-deriving the ordering.

export type ProposedDecision =
  | "APPROVED" | "APPROVED_WITH_CONDITIONS" | "NOT_APPROVED" | "REVIEW_REQUIRED";

export type FinalDecision = "APPROVED" | "APPROVED_WITH_CONDITIONS" | "NOT_APPROVED";

export interface Blocker {
  code: string;
  severity: string;
  explanation: string;
  requirementIds: string[];
}

export interface SectionScore {
  sectionId: string;
  weightPercent: number;
  /** Unrounded. `displayPercent` is the rounded one, and nothing compares it. */
  compliancePercent: number | null;
  displayPercent: number | null;
  notApplicableCount: number;
  missingCount: number;
  waivedCount: number;
  belowMinimum: boolean;
}

export interface ScoreRun {
  scoreRunId: number;
  compliancePercent: number | null;
  evidenceConfidencePercent: number | null;
  proposedDecision: ProposedDecision;
  blockers: Blocker[];
  sections: SectionScore[];
  notes: string[];
  algorithmVersion: string;
  supersedesScoreId?: string | null;
  createdAt?: string;
}

export interface AuditDecision {
  id: string;
  decisionRunId: number;
  decision: FinalDecision;
  decidedBy: string;
  decidedAt?: string;
  machineProposed?: ProposedDecision | null;
  overridden: boolean;
  overrideReason?: string | null;
  linkedCapaIds: string[];
  signatureStatement?: string;
  supersedesDecisionId?: string | null;
}

const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

/**
 * A score is `null` when it is genuinely absent — nothing applicable was
 * scored, or no evidence was graded. NaN arrives from the backend as `null`
 * or as the string "NaN" depending on the JSON encoder, and both mean the
 * same thing. Turning either into 0 would show a catastrophic failing grade
 * for something that was never measured.
 */
export function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return null;
  return parsed;
}

export function normalizeBlocker(raw: any): Blocker {
  return {
    code: text(raw?.code, "UNKNOWN"),
    severity: text(raw?.severity, "info"),
    explanation: text(raw?.explanation),
    requirementIds: Array.isArray(raw?.requirement_ids ?? raw?.requirementIds)
      ? (raw.requirement_ids ?? raw.requirementIds).map(String)
      : [],
  };
}

export function normalizeSection(raw: any): SectionScore {
  const percent = numberOrNull(raw?.compliance_percent ?? raw?.compliancePercent);
  return {
    sectionId: text(raw?.section_id ?? raw?.sectionId),
    weightPercent: Number(raw?.weight_percent ?? raw?.weightPercent ?? 0),
    compliancePercent: percent,
    displayPercent: percent === null ? null : Math.round(percent * 10) / 10,
    notApplicableCount: Number(raw?.not_applicable_count ?? raw?.notApplicableCount ?? 0),
    missingCount: Number(raw?.missing_count ?? raw?.missingCount ?? 0),
    waivedCount: Number(raw?.waived_count ?? raw?.waivedCount ?? 0),
    belowMinimum: Boolean(raw?.below_minimum ?? raw?.belowMinimum),
  };
}

export function normalizeScoreRun(raw: any): ScoreRun | null {
  if (!raw) return null;
  return {
    scoreRunId: Number(raw?.score_run_id ?? raw?.scoreRunId ?? 1),
    compliancePercent: numberOrNull(raw?.compliance_percent ?? raw?.compliancePercent),
    evidenceConfidencePercent: numberOrNull(
      raw?.evidence_confidence_percent ?? raw?.evidenceConfidencePercent),
    // REVIEW_REQUIRED is the safe default: it declines to propose an outcome
    // rather than inventing an approval from a malformed response.
    proposedDecision: text(raw?.proposed_decision ?? raw?.proposedDecision,
                           "REVIEW_REQUIRED") as ProposedDecision,
    blockers: Array.isArray(raw?.blockers) ? raw.blockers.map(normalizeBlocker) : [],
    sections: Array.isArray(raw?.sections) ? raw.sections.map(normalizeSection) : [],
    notes: Array.isArray(raw?.notes) ? raw.notes.map(String) : [],
    algorithmVersion: text(raw?.algorithm_version ?? raw?.algorithmVersion),
    supersedesScoreId: raw?.supersedes_score_id ?? raw?.supersedesScoreId ?? null,
    createdAt: raw?.created_at ?? raw?.createdAt ?? undefined,
  };
}

export function normalizeDecision(raw: any): AuditDecision | null {
  if (!raw) return null;
  return {
    id: text(raw?.id),
    decisionRunId: Number(raw?.decision_run_id ?? raw?.decisionRunId ?? 1),
    decision: text(raw?.decision, "NOT_APPROVED") as FinalDecision,
    decidedBy: text(raw?.decided_by ?? raw?.decidedBy),
    decidedAt: raw?.decided_at ?? raw?.decidedAt ?? undefined,
    machineProposed: raw?.machine_proposed ?? raw?.machineProposed ?? null,
    overridden: Boolean(raw?.overridden),
    overrideReason: raw?.override_reason ?? raw?.overrideReason ?? null,
    linkedCapaIds: Array.isArray(raw?.linked_capa_ids ?? raw?.linkedCapaIds)
      ? (raw.linked_capa_ids ?? raw.linkedCapaIds).map(String)
      : [],
    signatureStatement: raw?.signature_statement ?? raw?.signatureStatement ?? "",
    supersedesDecisionId: raw?.supersedes_decision_id ?? raw?.supersedesDecisionId ?? null,
  };
}

// ── Presentation helpers ────────────────────────────────────────────────────

const SEVERITY_RANK: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3, info: 4,
};

/** Most severe first, so the worst thing is the first thing read. */
export function sortedBlockers(blockers: Blocker[]): Blocker[] {
  return blockers.slice().sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9));
}

export function hasCriticalBlocker(blockers: Blocker[]): boolean {
  return blockers.some((b) => b.severity === "critical");
}

/** "not scored" rather than "0%" — they are different claims. */
export function formatPercent(value: number | null): string {
  return value === null ? "not scored" : `${Math.round(value * 10) / 10}%`;
}

/**
 * Whether the decision controls should be offered at all.
 *
 * Deliberately permissive on the machine's proposal: a reviewer may override
 * NOT_APPROVED, and hiding the control would make the override path invisible
 * rather than deliberate. What it does require is that scoring has actually
 * happened — you cannot sign off an audit nobody scored.
 */
export function canDecide(run: ScoreRun | null): boolean {
  return run !== null;
}

/**
 * True when recording this decision contradicts the machine, which is what
 * makes a written reason mandatory. Mirrors the server's own rule so the UI
 * can ask for the reason before the request rather than after the rejection.
 */
export function requiresOverrideReason(run: ScoreRun | null, decision: FinalDecision): boolean {
  if (!run) return false;
  return run.proposedDecision !== decision;
}

/** A conditional approval with no conditions is an unconditional approval
 *  wearing a hat — the server refuses it, and so does the button. */
export function conditionalNeedsCapa(decision: FinalDecision, linkedCapaIds: string[]): boolean {
  return decision === "APPROVED_WITH_CONDITIONS" && linkedCapaIds.length === 0;
}
