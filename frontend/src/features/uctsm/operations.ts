/** Pure helpers for the operations screen: dead-letter replay, pilot cohorts, reminder preferences, reports. */
export const DEFAULT_TIMEZONE = "Asia/Kolkata";   // India is the deployment region; pre-filled, never assumed by the server

export const reasonBody = (reason: string, what = "this action") => {
  if (reason.trim().length < 3) throw new Error(`State why you are doing ${what}.`);
  return { reason: reason.trim() };
};

export function parsePatientIds(text: string): string[] {
  const ids = [...new Set(text.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean))];
  if (!ids.length) throw new Error("Enter at least one legacy patient id.");
  if (ids.length > 200) throw new Error("A pilot cohort holds at most 200 patients.");
  return ids;
}
export function cohortBody(name: string, idsText: string) {
  if (!name.trim()) throw new Error("Name the cohort.");
  return { name: name.trim(), external_patient_ids: parsePatientIds(idsText) };
}
/** The report printed by `python -m app.persistence.mongodb.recovery_cli snapshot`, pasted as JSON. */
export function snapshotBody(text: string) {
  let parsed: Record<string, unknown>;
  try { parsed = JSON.parse(text); } catch { throw new Error("Paste the snapshot report exactly as the snapshot command printed it (JSON)."); }
  if (parsed.status !== "ENCRYPTED_LOGICAL_SNAPSHOT") throw new Error("That report is not a completed encrypted snapshot.");
  for (const key of ["manifest_hash", "encrypted_file_hash", "created_at"])
    if (typeof parsed[key] !== "string" || !parsed[key]) throw new Error(`The snapshot report has no ${key}.`);
  return { status: parsed.status as string, manifest_hash: parsed.manifest_hash as string,
    encrypted_file_hash: parsed.encrypted_file_hash as string, created_at: parsed.created_at as string };
}
export const rollbackBody = (reason: string, acceptLoss: boolean) => ({ ...reasonBody(reason, "the rollback"), accept_plane_changes_loss: acceptLoss });

export type Readiness = { ready: boolean; state: string; patients: { external_patient_id: string; ready: boolean; reasons: string[] }[] };
const REASONS: Record<string, string> = {
  MAPPING_MISSING: "was never migrated", MAPPING_NOT_RECONCILED: "is not reconciled with the old schedule",
  PARITY_NOT_MATCH: "the two systems do not agree on its dates", PATIENT_NOT_FOUND: "was not found",
  ALREADY_AUTHORITATIVE_OR_CLASSIFIED: "is already managed in the new system",
};
export function readinessLines(readiness: Readiness): string[] {
  const blocked = readiness.patients.filter((row) => !row.ready);
  if (!blocked.length) return [readiness.ready ? "Every patient is ready." : "This cohort has no patients."];
  return blocked.map((row) => `${row.external_patient_id} ${row.reasons.map((code) => REASONS[code] ?? code).join("; ")}.`);
}
export function cutoverMessage(failure: unknown): string {
  const response = (failure as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  const detail = response?.data?.detail as { code?: string; details?: Record<string, unknown> } | string | undefined;
  if (response?.status === 403) return "You do not have the cutover grant for this action.";
  if (response?.status === 404) return "That item was not found for you.";
  if (typeof detail === "string") return detail.slice(0, 500);
  switch (detail?.code) {
    case "MAKER_CHECKER_SAME_PERSON": return "A different person with the cutover grant must promote this cohort.";
    case "COHORT_NOT_READY": return "Some patients are not ready. Check readiness.";
    case "COHORT_MEMBERS_INVALID": return "Some patient ids were not migrated or belong to another trial.";
    case "PLANE_CHANGES_WOULD_BE_LOST": {
      const d = (detail.details ?? {}) as Record<string, number>;
      return `Changes were recorded in the new system since promotion (${d.commands ?? 0} commands, ${d.occurrences ?? 0} visits). `
        + "Rolling back would lose them. Tick the acknowledgement only if a named owner accepts that.";
    }
    case "SNAPSHOT_REQUIRED": return "Take an encrypted snapshot first and paste its report.";
    case "SNAPSHOT_INVALID": return "The snapshot report is not a completed encrypted snapshot.";
    case "SNAPSHOT_STALE": return "The snapshot is too old (or from the future). Take a fresh one before promoting.";
    case "COHORT_NOT_IN_SHADOW": case "COHORT_NOT_IN_PILOT": return "The cohort is not in the right state for this step.";
    default: return "The request could not be completed. Reload and check the cohort state.";
  }
}

export type PrefsForm = { consent: "GRANTED" | "DECLINED" | "WITHDRAWN" | ""; timezone: string; delivery_time: string;
  lead_days: string; quiet_start: string; quiet_end: string };
export const emptyPrefs = (): PrefsForm => ({ consent: "", timezone: DEFAULT_TIMEZONE, delivery_time: "09:00", lead_days: "1", quiet_start: "", quiet_end: "" });
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
export function prefsBody(form: PrefsForm) {
  if (!form.consent) throw new Error("Record the patient's choice: granted, declined or withdrawn.");
  if (!form.timezone.trim()) throw new Error("A time zone is required.");
  if (!HHMM.test(form.delivery_time)) throw new Error("Delivery time must be HH:MM (24 hour).");
  const days = Number(form.lead_days);
  if (!Number.isInteger(days) || days < 0 || days > 30) throw new Error("Lead days must be a whole number from 0 to 30.");
  const quiet = [form.quiet_start.trim(), form.quiet_end.trim()];
  if (quiet.filter(Boolean).length === 1) throw new Error("Quiet hours need both a start and an end.");
  if (quiet[0] && (!HHMM.test(quiet[0]) || !HHMM.test(quiet[1]) || quiet[0] === quiet[1])) throw new Error("Quiet hours must be two different HH:MM times.");
  return { consent: form.consent, timezone: form.timezone.trim(), delivery_time: form.delivery_time, lead_days: days,
    quiet_start: quiet[0] || null, quiet_end: quiet[1] || null };
}

export type ReportRow = { patient_code: string; visit: string; status: string; target_date: string | null;
  window_earliest: string | null; window_latest: string | null; actual_visit_date: string | null;
  deviation_days: number | null; outside_window: boolean | null };
export type ScheduleReport = { profile: string; patients: { patient_code: string; rows: ReportRow[] }[]; next_cursor: string | null };
export function reportLine(row: ReportRow): string {
  const window = row.window_earliest && row.window_latest ? ` (${row.window_earliest} to ${row.window_latest})` : "";
  const actual = row.actual_visit_date ? `done ${row.actual_visit_date}` : "not done";
  const verdict = row.outside_window === null ? "" : row.outside_window ? ", OUTSIDE window" : ", inside window";
  const days = row.deviation_days === null ? "" : `, ${row.deviation_days > 0 ? "+" : ""}${row.deviation_days} d`;
  return `${row.patient_code} · ${row.visit} · target ${row.target_date ?? "not scheduled"}${window} · ${actual}${days}${verdict}`;
}
export function reportTotals(report: ScheduleReport) {
  const rows = report.patients.flatMap((patient) => patient.rows);
  return { patients: report.patients.length, visits: rows.length, outside: rows.filter((row) => row.outside_window === true).length,
    done: rows.filter((row) => !!row.actual_visit_date).length };
}
