/**
 * Pure logic for the draft Visit Schedule review screen (UI-02..UI-12).
 *
 * Everything the screen shows about meaning (timing text, window text, review
 * status, approval eligibility) comes from the backend review view
 * (`GET /uctsm/schedule-versions/{id}/review-view`). This module only filters,
 * counts, labels and builds typed request bodies - it never re-derives a
 * clinical meaning and never defaults an unstated value.
 */

export type ReviewStatus = "PENDING" | "READY";
export type WindowStatus = "STATED" | "NOT_STATED" | "NEEDS_REVIEW" | "NOT_APPLICABLE";

export type ReviewIssue = {
  id: string;
  code: string;
  heading: string;
  message: string;
  blocking: boolean;
  severity: string;
  origin: string;
  citations: { page_number?: number | null; quote?: string | null }[];
  statement_ids?: string[];
  /** False for capability gaps (UNSUPPORTED_OPERATOR): never offer an acknowledge path. */
  acknowledgeable?: boolean;
  conflicting_constraints?: string[];
};

export type LedgerStatement = {
  statement_id: string;
  what?: string | null;
  disposition: string;
  disposition_label: string;
  uncompiled: boolean;
  reason?: string | null;
  operator?: string | null;
  evidence_state?: string | null;
  citations: { page?: number | null; section?: string | null; table?: string | null; quote?: string | null; verification: string }[];
  rule_refs: unknown[];
  unknown_fields: Record<string, unknown>;
  blocking_issue_codes: string[];
};

export type AdditionalRestriction = {
  fact_id?: string | null;
  status: string;
  relation?: string | null;
  required_operator?: string | null;
  summary: string;
  citations: { page?: number | null; quote?: string | null; verification: string }[];
};

export type SourceCoverage = {
  schema?: string;
  status: string;
  blocking: boolean;
  message: string;
  counts: Record<string, number>;
  gaps: { unit_id: string; page?: number | null; state: string; rationale?: string | null }[];
  unaccounted_units: string[];
  missing_references: { kind?: string; label?: string; state?: string; required?: boolean; from_pages?: number[]; text?: string }[];
  notes: string[];
  statements: {
    total: number;
    dispositions: Record<string, number>;
    uncompiled: number;
    not_linked_to_a_visit: LedgerStatement[];
  };
};

export type ReviewConstraint = {
  id: string;
  kind: string;
  summary: string;
  in_compact_summary: boolean;
  supported: boolean;
  path: string;
  rule: unknown;
  constraint_id?: string | null;
  conflicting?: boolean;
  /** Compiler v5: the protocol statement(s) this constraint was compiled from. */
  source_statements?: (Partial<LedgerStatement> & { statement_id: string; relation?: string | null; missing?: boolean })[];
};

export type ReviewEvidence = {
  id: string;
  evidence_type?: string;
  is_reviewer_decision?: boolean;
  page_number?: number | null;
  section_title?: string | null;
  table_title?: string | null;
  quote?: string | null;
  claims?: string[];
  missing?: boolean;
};

export type ReviewRow = {
  id: string;
  code: string;
  visit_name: string;
  protocol_label: string;
  event_type: string;
  timing_summary: string;
  timing_supported: boolean;
  window_summary: string;
  window_status: WindowStatus;
  applicability_summary: string;
  review_status: ReviewStatus;
  pending_reasons: string[];
  issue_refs: string[];
  question_refs: string[];
  issues: ReviewIssue[];
  group: "SCHEDULED" | "REPEATING" | "CONDITIONAL";
  origin: string;
  /** Uses (or depends on) a rule the engine cannot execute yet. */
  unsupported?: boolean;
  details: {
    statements?: LedgerStatement[];
    additional_restrictions?: AdditionalRestriction[];
    constraint_trees?: { scope: string; tree: unknown }[];
    activities: {
      id: string; code?: string | null; name: string; activity_type: string; requiredness: string;
      timing_summary?: string | null; conditions: string[]; applicability_summary: string;
      footnotes: { marker?: string | null; text: string; category: string; resolved: boolean }[];
      needs_review: boolean;
    }[];
    anchors: { code: string; display_name: string; defined: boolean; status: string; accepts: string; roles: string[] }[];
    recurrence: { type: string; summary: string; rule: unknown } | null;
    constraints: ReviewConstraint[];
    evidence: ReviewEvidence[];
    visit_mode?: string | null;
    allowed_visit_modes: string[];
    activation: string;
    manual: { origin: string; rationale?: string; review_state?: string; created_by?: string; created_at?: string } | null;
  };
};

export type SharedQuestion = {
  id: string;
  kind: string;
  title: string;
  blocking: boolean;
  issues: ReviewIssue[];
  issue_refs: string[];
  affected_row_ids: string[];
  affected_rows: { id: string; visit_name: string }[];
  actions: string[];
  options?: { code: string; display_name: string; status?: string }[];
  acknowledgeable?: boolean;
  explanation?: string;
  anchor?: { code: string; display_name?: string; status?: string; anchor_type?: string };
};

export type ReviewView = {
  view_version: string;
  draft_version_id: string;
  schedule_name: string;
  version_number: number;
  status: string;
  revision: string;
  semantic_fingerprint: string;
  extraction_status: { status: string; reason?: string | null; retry_available: boolean; run_id?: string | null };
  source_coverage_status: { status: string; blocking: boolean; message: string };
  summary: {
    displayed_rows: number; pending_rows: number; ready_rows: number; blocking_issues: number;
    warnings: number; shared_questions: number; open_shared_questions: number; document_level_issues: number;
  };
  groups: { id: string; label: string; row_ids: string[] }[];
  rows: ReviewRow[];
  shared_review_questions: SharedQuestion[];
  document_level_issues: ReviewIssue[];
  source_coverage?: SourceCoverage | null;
  /** Reviewed schedule-level holiday calendars attached to this draft. */
  holiday_calendars?: { code: string; version: string; description?: string | null;
    business_weekdays: string[]; dates: string[]; date_count: number }[];
  statement_summary?: SourceCoverage["statements"] | null;
  can_approve: boolean;
  approval_blockers: string[];
  anchors: { code: string; display_name: string; status: string; anchor_type: string; is_origin: boolean }[];
};

export type RowFilter = "ALL" | "PENDING" | "READY";
export const ROW_FILTERS: { key: RowFilter; label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "PENDING", label: "Pending" },
  { key: "READY", label: "Ready" },
];

/** Row counts per filter - unique rows, never issue or occurrence counts (UI-04). */
export function filterCounts(rows: ReviewRow[]): Record<RowFilter, number> {
  const unique = new Map(rows.map((row) => [row.id, row]));
  let pending = 0;
  for (const row of unique.values()) if (row.review_status === "PENDING") pending += 1;
  return { ALL: unique.size, PENDING: pending, READY: unique.size - pending };
}

export function filterRows(rows: ReviewRow[], filter: RowFilter): ReviewRow[] {
  if (filter === "ALL") return rows;
  return rows.filter((row) => row.review_status === filter);
}

/** Rows grouped for display, keeping the backend's group order. */
export function groupRows(view: Pick<ReviewView, "groups">, rows: ReviewRow[]) {
  const byId = new Map(rows.map((row) => [row.id, row]));
  return view.groups
    .map((group) => ({ ...group, rows: group.row_ids.flatMap((id) => byId.get(id) ? [byId.get(id)!] : []) }))
    .filter((group) => group.rows.length);
}

/** Status carried by an icon AND a word, never colour alone (UI-11). */
export function statusIndicator(row: Pick<ReviewRow, "review_status" | "pending_reasons">) {
  return row.review_status === "PENDING"
    ? { label: "Pending", icon: "alert" as const, tone: "warning" as const,
        description: row.pending_reasons.join("; ") || "Needs review" }
    : { label: "Ready", icon: "check" as const, tone: "success" as const, description: "Ready for approval" };
}

export function windowTone(status: WindowStatus): "muted" | "warning" | "default" {
  if (status === "NEEDS_REVIEW") return "warning";
  if (status === "STATED") return "default";
  return "muted";
}

export function rowAccessibilityLabel(row: ReviewRow, expanded: boolean): string {
  return [
    `${row.visit_name}.`,
    `${statusIndicator(row).label}.`,
    `Timing: ${row.timing_summary}.`,
    `Window: ${row.window_summary}.`,
    expanded ? "Collapse details" : "Expand details",
  ].join(" ");
}

export function extractionBadge(view: Pick<ReviewView, "extraction_status" | "status">) {
  const status = view.extraction_status.status;
  if (view.status === "APPROVED") return { label: "Approved", tone: "success" as const };
  if (status === "PARTIAL") return { label: "Partial extraction", tone: "warning" as const };
  if (status === "FAILED") return { label: "Extraction failed", tone: "danger" as const };
  if (status === "EMPTY") return { label: "Nothing extracted", tone: "danger" as const };
  if (status === "NOT_RECORDED") return { label: "Draft", tone: "info" as const };
  if (["QUEUED", "RUNNING", "IN_PROGRESS"].includes(status)) return { label: "Extracting…", tone: "info" as const };
  return { label: "Extracted draft", tone: "info" as const };
}

/** UI-12: a failed or partial extraction offers a way forward, never silence. */
export function recoveryNotice(view: Pick<ReviewView, "extraction_status" | "summary">): string | null {
  const { status, reason } = view.extraction_status;
  if (["PARTIAL", "FAILED", "EMPTY"].includes(status)) {
    const lead = status === "PARTIAL"
      ? "Part of the protocol could not be turned into schedule rules."
      : status === "EMPTY" ? "No visits were extracted from this protocol."
        : "The schedule extraction failed.";
    return reason ? `${lead} ${reason}` : lead;
  }
  return null;
}

export function approvalState(view: Pick<ReviewView, "can_approve" | "approval_blockers" | "status">) {
  if (view.status === "APPROVED") return { enabled: false, reason: "This version is approved." };
  if (view.can_approve) return { enabled: true, reason: "" };
  return { enabled: false, reason: view.approval_blockers.join(" ") || "Approval is not available yet." };
}

/** Error text for any review write - structured 409/422 details included. */
export function reviewErrorMessage(error: any, fallback = "The change could not be saved."): string {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    if (detail.code === "STALE_REVISION") {
      return "Someone changed this draft after you opened it. It has been reloaded — check the changes and try again.";
    }
    if (Array.isArray(detail.reasons) && detail.reasons.length) return detail.reasons.join(" ");
    if (typeof detail.message === "string") return detail.message;
  }
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item: any) => item?.msg).filter(Boolean).join("; ") || fallback;
  }
  return error?.message || fallback;
}

export function isStaleRevision(error: any): boolean {
  return error?.response?.status === 409 && error?.response?.data?.detail?.code === "STALE_REVISION";
}

// --- Edit and Add Visit forms (UI-07) ----------------------------------------------

export const TIME_UNITS = ["DAY", "WEEK", "MONTH", "YEAR", "HOUR"] as const;
export type TimeUnit = typeof TIME_UNITS[number];

export type TimingForm = {
  kind: "OFFSET" | "PROTOCOL_DAY";
  anchor: string;
  value: string;
  unit: TimeUnit;
};

export type WindowForm = { before: string; after: string; unit: TimeUnit };

const integer = (value: string) => /^-?\d+$/.test(value.trim()) ? Number(value.trim()) : null;

export function buildTiming(form: TimingForm): { timing?: Record<string, unknown>; error?: string } {
  if (!form.anchor) return { error: "Choose what this visit is counted from." };
  const value = integer(form.value);
  if (value === null) return { error: "Enter the timing as a whole number." };
  const reference = { kind: "ANCHOR", code: form.anchor };
  if (form.kind === "PROTOCOL_DAY") {
    if (value === 0) return { error: "Clinical day numbering has no Day 0." };
    return { timing: { type: "PROTOCOL_DAY", reference, day: value } };
  }
  return { timing: { type: "OFFSET", reference, offset: { value, unit: form.unit } } };
}

/** An empty window means "Not stated" (null); a half-filled one is an error. */
export function buildWindow(form: WindowForm): { window?: Record<string, unknown> | null; error?: string } {
  const before = form.before.trim(), after = form.after.trim();
  if (!before && !after) return { window: null };
  if (!before || !after) return { error: "State both sides of the window, or leave both blank for Not stated." };
  const b = integer(before), a = integer(after);
  if (b === null || a === null || b < 0 || a < 0) return { error: "Window sides must be whole numbers of zero or more." };
  return { window: { before: { value: b, unit: form.unit }, after: { value: a, unit: form.unit } } };
}

export type EditForm = {
  visitName: string;
  timing: TimingForm | null;      // null = timing not edited
  window: WindowForm | null;      // null = window not edited
  confirmRow: boolean;
  rationale: string;
};

/** Only the fields the reviewer actually changed are sent, so a label edit
 * can never carry a timing change with it. */
export function buildEditRequest(row: Pick<ReviewRow, "visit_name">, form: EditForm, revision: string) {
  const rationale = form.rationale.trim();
  if (!rationale) return { error: "Say why this change is being made." };
  const changes: Record<string, unknown> = {};
  const name = form.visitName.trim();
  if (!name) return { error: "A visit needs a name." };
  if (name !== row.visit_name) changes.display_name = name;
  if (form.timing) {
    const built = buildTiming(form.timing);
    if (built.error) return { error: built.error };
    changes.timing = built.timing;
  }
  if (form.window) {
    const built = buildWindow(form.window);
    if (built.error) return { error: built.error };
    changes.window = built.window;
  }
  if (!Object.keys(changes).length && !form.confirmRow) return { error: "Nothing has changed." };
  return { body: { expected_revision: revision, rationale, changes, confirm_row: form.confirmRow } };
}

export type AddVisitForm = {
  visitName: string;
  timing: TimingForm;
  window: WindowForm;
  activities: string;
  rationale: string;
};

export function buildAddVisitRequest(form: AddVisitForm, revision: string) {
  const rationale = form.rationale.trim();
  if (!rationale) return { error: "Say why this visit is being added." };
  const name = form.visitName.trim();
  if (!name) return { error: "A visit needs a name." };
  const timing = buildTiming(form.timing);
  if (timing.error) return { error: timing.error };
  const window = buildWindow(form.window);
  if (window.error) return { error: window.error };
  const activities = form.activities.split(/[\n,;]/).map((item) => item.trim()).filter(Boolean);
  const body: Record<string, unknown> = {
    expected_revision: revision, rationale, display_name: name, timing: timing.timing, activities,
  };
  if (window.window) body.window = window.window;
  return { body };
}

export type QuestionAnswer = {
  action: string;
  anchorCode?: string;
  displayName?: string;
  anchorType?: string;
  rationale: string;
};

export function buildQuestionRequest(question: SharedQuestion, answer: QuestionAnswer, revision: string) {
  if (!question.actions.includes(answer.action)) return { error: "Choose how to answer this question." };
  const rationale = answer.rationale.trim();
  if (!rationale) return { error: "Say why this answer is correct." };
  if ((answer.action === "CHOOSE_ORIGIN" || answer.action === "REMAP_ANCHOR") && !answer.anchorCode) {
    return { error: "Choose an anchor." };
  }
  if (answer.action === "DEFINE_ANCHOR" && (!answer.displayName?.trim() || !answer.anchorType?.trim())) {
    return { error: "Name the event and its type." };
  }
  return {
    body: {
      expected_revision: revision, rationale, action: answer.action,
      anchor_code: answer.anchorCode, display_name: answer.displayName?.trim(),
      anchor_type: answer.anchorType?.trim(),
    },
  };
}

export const QUESTION_ACTION_LABELS: Record<string, string> = {
  CHOOSE_ORIGIN: "Choose the Day 1 event",
  CONFIRM_ANCHOR: "Confirm this anchor",
  DEFINE_ANCHOR: "Define the missing event",
  REMAP_ANCHOR: "Count from an existing event",
  EDIT_RULE: "Edit an affected visit",
};

export function exportFileName(view: Pick<ReviewView, "version_number" | "status">, format: "json" | "csv") {
  return `visit-schedule-v${view.version_number}-${view.status === "APPROVED" ? "approved" : "draft"}.${format}`;
}

/** A timing prefilled from the row's canonical rule when it is a simple one. */
export function timingFormFromRule(rule: unknown, fallbackAnchor: string): TimingForm | null {
  const timing = rule as Record<string, any> | null;
  if (!timing) return null;
  if (timing.type === "OFFSET" && timing.reference?.kind === "ANCHOR") {
    return { kind: "OFFSET", anchor: timing.reference.code, value: String(timing.offset?.value ?? ""),
      unit: (timing.offset?.unit || "DAY") as TimeUnit };
  }
  if (timing.type === "PROTOCOL_DAY") {
    return { kind: "PROTOCOL_DAY", anchor: timing.reference?.code || fallbackAnchor, value: String(timing.day ?? ""), unit: "DAY" };
  }
  return null;
}

// --- Source coverage and statement ledger (extraction accountability) ------------------

const DISPOSITION_ORDER = ["COMPILED", "DUPLICATE_OF", "PENDING_INTERPRETATION", "UNSUPPORTED",
  "INVALID", "OUT_OF_SCOPE_NON_SCHEDULE"];
const DISPOSITION_WORDS: Record<string, string> = {
  COMPILED: "compiled", DUPLICATE_OF: "duplicates", PENDING_INTERPRETATION: "waiting for interpretation",
  UNSUPPORTED: "not executable yet", INVALID: "invalid", OUT_OF_SCOPE_NON_SCHEDULE: "not schedule rules",
};

/** "12 compiled · 2 waiting for interpretation · 3 not schedule rules" (zero counts omitted). */
export function dispositionSummary(dispositions: Record<string, number>): string {
  const keys = [...DISPOSITION_ORDER, ...Object.keys(dispositions).filter((key) => !DISPOSITION_ORDER.includes(key))];
  return keys.filter((key) => (dispositions[key] || 0) > 0)
    .map((key) => `${dispositions[key]} ${DISPOSITION_WORDS[key] || key.toLowerCase().replace(/_/g, " ")}`)
    .join(" · ") || "No statements recorded";
}

export function coverageCountsText(counts: Record<string, number>): string {
  return Object.entries(counts).filter(([, value]) => value > 0)
    .map(([key, value]) => `${value} ${key.toLowerCase().replace(/_/g, " ")}`).join(" · ") || "No pages recorded";
}

/** One citation as a reader sees it: page, verification in words, quote. */
export function citationLine(citation: { page?: number | null; quote?: string | null; verification?: string | null }): string {
  const verification = ({
    TEXT_MATCHED: "quote found in the document", UNVERIFIED: "quote not verified",
    REJECTED: "quote NOT found in the document", NOT_CHECKED: "not checked",
  } as Record<string, string>)[citation.verification || "NOT_CHECKED"] || String(citation.verification).toLowerCase();
  const page = citation.page ? `Page ${citation.page}` : "No page";
  return `${page} · ${verification}${citation.quote ? ` — “${citation.quote}”` : ""}`;
}

export function missingReferenceLine(reference: SourceCoverage["missing_references"][number]): string {
  const where = reference.from_pages?.length ? ` (referenced on page ${reference.from_pages.join(", ")})` : "";
  const state = reference.state === "MISSING" ? "not in the uploaded document" : "could not be located";
  return `${reference.kind ? reference.kind[0] + reference.kind.slice(1).toLowerCase() : "Reference"} ${reference.label || ""} ${state}${where}${reference.required ? " — required" : ""}`.replace(/\s+/g, " ").trim();
}

/** A capability gap has no acknowledge path; say so instead of offering one. */
export function questionResolution(question: Pick<SharedQuestion, "actions" | "acknowledgeable" | "kind">) {
  const actions = question.actions.filter((action) => action !== "EDIT_RULE");
  if (question.acknowledgeable === false || question.kind === "UNSUPPORTED_OPERATOR") {
    return { actions: [] as string[], note: "Cannot be acknowledged: restate the rule with supported operators or wait for engine support. Every visit listed stays blocked." };
  }
  if (!actions.length) return { actions, note: "Resolve this by editing one of the affected visits." };
  return { actions, note: "" };
}
