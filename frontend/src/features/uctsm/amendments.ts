/** Pure view logic for protocol amendments (P6): no React, no network.
 *
 * Everything the screens show about a comparison, a mapping review, a simulation and a
 * decision is decided here so it can be tested without a renderer: which words describe a
 * blocker and what to do about it, how an effective point is validated and sent, how the
 * simulated visits are grouped, and how a refused request is explained. The server
 * recomputes and rechecks everything; nothing here is authority. */

export type AdoptionMode =
  | "REMAIN_ON_V1" | "ADOPT_IMMEDIATELY" | "ADOPT_AT_EVENT" | "ADOPT_AT_CYCLE" | "ADOPT_ON_DATE";

export type Blocker = { code: string; path?: string; detail?: string };

export type VisitImpact = {
  from_key: string | null; to_key: string | null; category: string;
  old_target: string | null; new_target: string | null;
  old_earliest?: string | null; old_latest?: string | null;
  new_earliest?: string | null; new_latest?: string | null;
  old_status: string | null; new_status: string | null; protected: boolean; reason: string;
};

export type VersionIdentity = {
  schedule_version_id: string; version_number: number; status: string;
  effective_from: string | null; approved_at: string | null; content_hash: string | null;
  verification: string; based_on_schedule_version_id?: string | null;
};

export type MappingRow = {
  id: string; mapping_type: string; source_codes: string[]; target_codes: string[];
  review_status: string; confidence: number; provenance: string; rationale: string;
  occurrence_rules: { source_code: string; source_index: number; target_code: string; target_index: number }[];
};

export type ChangeSource = {
  page_number: number | null; section_title: string | null; table_title: string | null;
  source_text: string | null;
};

export type PairView = {
  pair_id: string; trial_id: string; from: VersionIdentity; to: VersionIdentity;
  change_summary: { total: number; clinical: number; administrative: number };
  changes: {
    change_type: string; significance: string; entity_code: string; summary: string; detail?: string | null;
    sources?: { from: ChangeSource[]; to: ChangeSource[] };
  }[];
  mapping_set_revision: number; mapping_revision: string; mappings: MappingRow[];
  problems: Blocker[]; ready_for_adoption: boolean;
};

export type Simulation = {
  simulation_id: string; expires_at: string; can_confirm: boolean; direction: string;
  from: VersionIdentity; to: VersionIdentity; summary: Record<string, number>;
  visits: VisitImpact[]; blockers: Blocker[]; warnings: Blocker[]; carried_fact_count: number;
  retained_in_earlier_version: { logical_key: string }[]; adoption_point: Record<string, unknown>;
};

// ------------------------------------------------------------------------ blockers
const BLOCKERS: Record<string, { title: string; action: string }> = {
  MAPPING_UNREVIEWED: {
    title: "A visit match still needs a reviewer",
    action: "Open the comparison and confirm or reject each proposed match." },
  MAPPING_RULES_MISSING: {
    title: "A split or merge does not say which occurrence goes where",
    action: "Open the comparison and enter the occurrence rules for it." },
  MAPPING_SOURCE_COVERAGE: {
    title: "A visit of the current version has no decided match",
    action: "Replace the rejected match with a rename, split, merge or removal." },
  MAPPING_TARGET_COVERAGE: {
    title: "A visit of the new version has no decided match",
    action: "Replace the rejected match with an addition, rename, split or merge." },
  MAPPING_REVIEW_NOT_RECORDED: {
    title: "A reviewed match has no recorded review decision",
    action: "Review it again so the decision is recorded under your name." },
  MAPPING_AUTO_NOT_SERVER_DERIVED: {
    title: "An automatic match is not supported by the two versions",
    action: "Ask an administrator to check the stored mapping." },
  MAPPING_REVISION_MISMATCH: {
    title: "The stored mapping does not match its fingerprint",
    action: "Ask an administrator to check the stored mapping." },
  PAIR_NOT_PREPARED: {
    title: "The two versions have not been compared yet",
    action: "A reviewer must open the amendment comparison first." },
  PAIR_STALE: {
    title: "An approved version changed after it was compared",
    action: "Stop and ask an administrator: an approved version must never change." },
  VERSION_UNVERIFIED_LEGACY: {
    title: "A version was approved before content checks existed",
    action: "Its content cannot be proven unchanged. Ask an administrator how to proceed." },
  VERSION_NOT_APPROVED: {
    title: "A version is not approved", action: "Only approved versions can be adopted." },
  FACT_UNMAPPED: {
    title: "A recorded visit has nowhere to go in the new version",
    action: "Decide the visit's match in the comparison, or keep the patient on the current version." },
  CARRY_CONFLICT: {
    title: "Two recorded visits would land on the same visit",
    action: "The mapping must say which occurrence goes where." },
  ACTIVE_COMMANDS_NOT_CARRIED: {
    title: "The patient has a pause, stop, rebase, override or cancellation",
    action: "Reverse it first (patient schedule changes), then preview again." },
  ACTIVITY_RECORDS_NOT_CARRIED: {
    title: "Activity records belong to a visit that is re-coded",
    action: "These cannot follow the visit yet; keep the patient on the current version." },
  UNSCHEDULED_NOT_CARRIED: {
    title: "An unscheduled visit belongs to a re-coded visit",
    action: "These cannot follow the visit yet; keep the patient on the current version." },
  ASSIGNMENT_NOT_IN_TARGET: {
    title: "The patient's arm, cohort or population is not in the new version",
    action: "The new version cannot be adopted for this patient." },
  TARGET_HAS_HISTORY: {
    title: "The patient already has history on that version",
    action: "Moving back onto a version they were on before is not supported yet." },
  NOT_YET_EFFECTIVE: {
    title: "The new version is not in force yet",
    action: "Choose its effective date or later, or wait." },
  DATE_NOT_FUTURE: {
    title: "That date is not in the future",
    action: "Choose a later date, or adopt immediately." },
  DATE_BEFORE_EFFECTIVE: {
    title: "That date is before the new version takes effect", action: "Choose a later date." },
  POINT_EVENT_UNKNOWN: {
    title: "That visit is not in the current version", action: "Pick a visit of the current version." },
  POINT_OCCURRENCE_MISSING: {
    title: "That occurrence does not exist", action: "Pick an occurrence the visit actually has." },
  POINT_CYCLE_UNKNOWN: {
    title: "That cycle plan is not in the current version", action: "Pick a repeat block that has cycles." },
  POINT_CYCLE_MISSING: {
    title: "That cycle does not exist", action: "Pick a cycle the plan actually has." },
  NOTHING_TO_RETURN_FROM: {
    title: "There is no adoption to return from",
    action: "A return is only available after an amendment was adopted." },
  RETURN_IS_IMMEDIATE: { title: "A return takes effect now", action: "Choose the immediate option." },
  OPEN_DECISION_EXISTS: {
    title: "This patient already has an open amendment decision",
    action: "Cancel it, or choose to replace it." },
  SIMULATION_EXPIRED: { title: "The preview expired", action: "Run the preview again." },
  SIMULATION_NOT_ACTIVE: { title: "The preview was already used or replaced", action: "Run the preview again." },
  STALE_INPUT: {
    title: "The patient's record changed after the preview", action: "Run the preview again." },
  IMPACT_CHANGED: {
    title: "The effect on the visits is no longer what was approved",
    action: "Preview again and confirm again." },
  INPUT_CHANGED: {
    title: "An approved input changed while this waited", action: "Preview again and confirm again." },
  PATIENT_MOVED: {
    title: "The patient is no longer on the version this was decided for",
    action: "Nothing was changed." },
  REASON_REQUIRED: { title: "A reason is required", action: "Say why." },
};

export function describeBlocker(blocker: Blocker): { title: string; action: string; detail: string } {
  const known = BLOCKERS[blocker.code];
  return {
    title: known?.title ?? blocker.code.replace(/_/g, " ").toLowerCase(),
    action: known?.action ?? "Resolve this before continuing.",
    detail: blocker.detail ?? "",
  };
}

// ----------------------------------------------------------------- effective point
export type PointForm = {
  mode: AdoptionMode; eventCode?: string; occurrenceIndex?: string;
  blockCode?: string; cycleNumber?: string; onDate?: string;
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const isDate = (value: string) => DATE.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const wholeNumber = (value?: string) => (value !== undefined && /^\d+$/.test(value.trim()) ? Number(value.trim()) : null);

/** The adoption point the server validates. Never defaults a patient to adoption. */
export function buildPoint(form: PointForm): { point: Record<string, unknown> } | { error: string } {
  switch (form.mode) {
    case "REMAIN_ON_V1":
    case "ADOPT_IMMEDIATELY":
      return { point: { mode: form.mode } };
    case "ADOPT_AT_EVENT": {
      const code = (form.eventCode ?? "").trim();
      const index = wholeNumber(form.occurrenceIndex ?? "0");
      if (!code) return { error: "Enter the visit code the move should wait for." };
      if (index === null) return { error: "The occurrence must be a whole number (0 is the first)." };
      return { point: { mode: form.mode, event_code: code, occurrence_index: index } };
    }
    case "ADOPT_AT_CYCLE": {
      const code = (form.blockCode ?? "").trim();
      const cycle = wholeNumber(form.cycleNumber);
      if (!code) return { error: "Enter the repeat block (for example TREATMENT)." };
      if (cycle === null || cycle < 1) return { error: "The cycle must be a whole number from 1." };
      return { point: { mode: form.mode, repeat_block_code: code, cycle_number: cycle } };
    }
    case "ADOPT_ON_DATE": {
      const value = (form.onDate ?? "").trim();
      if (!isDate(value)) return { error: "Enter the date as YYYY-MM-DD." };
      return { point: { mode: form.mode, on_date: value } };
    }
    default:
      return { error: "Choose when the patient should move, or that they stay." };
  }
}

export const POINT_LABEL: Record<AdoptionMode, string> = {
  REMAIN_ON_V1: "Stay on the current version",
  ADOPT_IMMEDIATELY: "Move now",
  ADOPT_AT_EVENT: "Move when a visit is performed",
  ADOPT_AT_CYCLE: "Move at the start of a cycle",
  ADOPT_ON_DATE: "Move on a date",
};

export function describePoint(point: Record<string, unknown> | undefined): string {
  if (!point) return "";
  switch (point.mode) {
    case "REMAIN_ON_V1": return "Stay on the current version";
    case "ADOPT_IMMEDIATELY": return "Move now";
    case "ADOPT_AT_EVENT": return `Move when ${point.event_code}:${point.occurrence_index} is performed`;
    case "ADOPT_AT_CYCLE": return `Move at the actual start of cycle ${point.cycle_number} of ${point.repeat_block_code}`;
    case "ADOPT_ON_DATE": return `Move on ${point.on_date}`;
    default: return "";
  }
}

/** The decision a point implies: staying is REMAIN, anything else ADOPT. */
export const decisionFor = (mode: AdoptionMode, direction = "ADOPT") =>
  direction === "RETURN" ? "RETURN" : mode === "REMAIN_ON_V1" ? "REMAIN" : "ADOPT";

// --------------------------------------------------------------------- visit groups
const GROUPS: { id: string; label: string; categories: string[] }[] = [
  { id: "changed", label: "Changed", categories: ["MOVED", "STATUS_CHANGED"] },
  { id: "added", label: "New in the new version", categories: ["ADDED"] },
  { id: "protected", label: "Recorded history (kept as it happened)", categories: ["PROTECTED"] },
  { id: "waiting", label: "Waiting for something before it can be dated", categories: ["WAITING"] },
  { id: "blocked", label: "Cannot be scheduled", categories: ["BLOCKED"] },
  { id: "removed", label: "Not in the new version (history stays on the current one)", categories: ["REMOVED"] },
  { id: "unchanged", label: "Unchanged", categories: ["UNCHANGED"] },
];

export function groupVisits(visits: VisitImpact[]) {
  return GROUPS.map((group) => ({
    id: group.id, label: group.label,
    rows: visits.filter((row) => group.categories.includes(row.category)),
  })).filter((group) => group.rows.length > 0);
}

export function describeVisit(row: VisitImpact): { title: string; lines: string[] } {
  const title = row.from_key && row.to_key && row.from_key !== row.to_key
    ? `${row.from_key} → ${row.to_key}` : (row.to_key ?? row.from_key ?? "");
  const lines: string[] = [];
  if (row.old_target || row.new_target) {
    lines.push(`Target: ${row.old_target ?? "not dated"} → ${row.new_target ?? "not dated"}`);
  }
  if (row.old_earliest || row.new_earliest) {
    const window = (earliest?: string | null, latest?: string | null) =>
      earliest || latest ? `${earliest ?? "?"} to ${latest ?? "?"}` : "no window";
    lines.push(`Window: ${window(row.old_earliest, row.old_latest)} → ${window(row.new_earliest, row.new_latest)}`);
  }
  if (row.old_status !== row.new_status) lines.push(`Status: ${row.old_status ?? "none"} → ${row.new_status ?? "none"}`);
  if (row.reason) lines.push(row.reason);
  return { title, lines };
}

/** The plain-language consequences shown before anyone confirms. */
export function consequences(sim: Pick<Simulation, "summary" | "carried_fact_count" | "retained_in_earlier_version" | "warnings">): string[] {
  const s = sim.summary || {};
  const lines: string[] = [];
  const count = (n: number | undefined, text: string) => { if (n) lines.push(`${n} ${text}`); };
  count(s.MOVED, "visit(s) get a different date or window");
  count(s.STATUS_CHANGED, "visit(s) change status");
  count(s.ADDED, "new visit(s) are added");
  count(s.REMOVED, "visit(s) are not in the new version");
  count(s.PROTECTED, "recorded visit(s) are kept exactly as they happened");
  count(s.WAITING, "visit(s) cannot be dated until something is recorded");
  count(s.BLOCKED, "visit(s) cannot be scheduled");
  count(s.UNCHANGED, "visit(s) do not change");
  if (sim.carried_fact_count) lines.push(`${sim.carried_fact_count} recorded fact(s) are carried to the matching new visit; the originals stay on the current version`);
  if (sim.retained_in_earlier_version?.length) lines.push(`${sim.retained_in_earlier_version.length} recorded fact(s) belong to visits that no longer exist and stay in the current version's history`);
  for (const warning of sim.warnings || []) lines.push(describeBlocker(warning).title);
  return lines;
}

/** "page 12, Schedule of Assessments: <the text>" - the protocol's own words for one change. */
export function describeSource(source: ChangeSource): string {
  const where = [source.page_number ? `page ${source.page_number}` : "", source.section_title ?? "",
    source.table_title ?? ""].filter(Boolean).join(", ");
  return `${where || "protocol text"}${source.source_text ? `: ${source.source_text}` : ""}`;
}

// -------------------------------------------------------------------------- mapping
export const MAPPING_TYPE_LABEL: Record<string, string> = {
  EQUIVALENT: "Same visit", RENAMED: "Renamed visit", SPLIT: "Split into several",
  MERGED: "Merged into one", REMOVED: "Removed", ADDED: "New",
};

export function describeMapping(row: MappingRow): string {
  const left = row.source_codes.join(" + ") || "—";
  const right = row.target_codes.join(" + ") || "—";
  return `${MAPPING_TYPE_LABEL[row.mapping_type] ?? row.mapping_type}: ${left} → ${right}`;
}

export function mappingActions(row: MappingRow): string[] {
  if (row.review_status === "REJECTED") return [];
  const needsRules = (row.mapping_type === "SPLIT" || row.mapping_type === "MERGED");
  const actions: string[] = [];
  if (row.review_status === "PROPOSED" && (!needsRules || row.occurrence_rules.length > 0)) actions.push("CONFIRM");
  if (needsRules) actions.push("SET_RULES");
  if (row.review_status !== "REJECTED") actions.push("REJECT");
  return actions;
}

/** "WEEK_4:0>WEEK_4A:0, WEEK_4:1>WEEK_4B:0" -> occurrence rules. */
export function parseRules(text: string): { rules: any[] } | { error: string } {
  const rules: any[] = [];
  for (const part of text.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)) {
    const match = /^([^:>\s]+):(\d+)\s*>\s*([^:>\s]+):(\d+)$/.exec(part);
    if (!match) return { error: `Could not read "${part}". Write it as CODE:INDEX>CODE:INDEX.` };
    rules.push({ source_code: match[1], source_index: Number(match[2]), target_code: match[3], target_index: Number(match[4]) });
  }
  return rules.length ? { rules } : { error: "Enter at least one rule, e.g. WEEK_4:0>WEEK_4A:0." };
}

export const pendingMappings = (view: Pick<PairView, "mappings">) =>
  view.mappings.filter((row) => row.review_status === "PROPOSED");

// -------------------------------------------------------------------------- decisions
export const DECISION_LABEL: Record<string, string> = {
  WAITING: "Waiting for its point", EXECUTING: "Being applied", APPLIED: "Applied",
  REMAINING: "Staying on the current version", NEEDS_RECONFIRMATION: "Needs a new confirmation",
  BLOCKED: "Could not be applied", CANCELLED: "Cancelled",
};

export function describeDecision(decision: { decision_type: string; status: string; mode: string; adoption_point?: Record<string, unknown> }): string {
  const what = decision.decision_type === "REMAIN" ? "Stay on the current version"
    : decision.decision_type === "RETURN" ? "Return to the earlier version"
    : describePoint(decision.adoption_point) || "Move to the new version";
  return `${what} · ${DECISION_LABEL[decision.status] ?? decision.status}`;
}

// --------------------------------------------------------------------------- errors
export type AmendmentFailure = { message: string; blockers: Blocker[]; kind: string };

/** A refused or failed request in words that say what to do; never a raw error. */
export function failureOf(failure: any): AmendmentFailure {
  const status: number = failure?.response?.status ?? failure?.status ?? 0;
  const detail = failure?.response?.data?.detail ?? failure?.detail;
  if (status === 403) return { kind: "forbidden", blockers: [], message: "You do not have permission to do this. Ask someone who holds the amendment right." };
  if (status === 404) return { kind: "absent", blockers: [], message: "That record was not found for you. Nothing was changed." };
  if (status === 409 && detail && typeof detail === "object") {
    const blockers: Blocker[] = Array.isArray(detail.blockers) ? detail.blockers : [];
    if (detail.code === "AMENDMENT_BLOCKED") {
      return { kind: "blocked", blockers, message: blockers.length
        ? "This cannot be applied yet. See what to resolve below. Nothing was changed."
        : (detail.message ?? "This cannot be applied. Nothing was changed.") };
    }
    if (detail.code === "AMENDMENT_INTEGRITY") {
      return { kind: "integrity", blockers: [], message: "An approved version no longer matches what was approved. Nothing was changed - tell an administrator." };
    }
    if (detail.code === "PLANE_NOT_AUTHORITATIVE") {
      return { kind: "authority", blockers: [], message: "This patient's schedule is not managed here, so it cannot be moved from this screen." };
    }
    return { kind: "conflict", blockers: [], message: "Someone else changed this first. Reload and try again." };
  }
  if (status === 409) return { kind: "conflict", blockers: [], message: "Someone else changed this first. Reload and try again." };
  if (status === 422) return { kind: "invalid", blockers: [], message: typeof detail === "string" ? detail : "Some of what you entered is not valid." };
  if (status === 0 || status >= 500) return { kind: "unavailable", blockers: [], message: "The server could not be reached. Nothing was changed; try again." };
  return { kind: "error", blockers: [], message: "That did not work. Nothing was changed." };
}

export const SIMULATION_NOTE =
  "This is a preview. Nothing is saved until you confirm, and it expires after 30 minutes or as soon as the patient's record changes.";
