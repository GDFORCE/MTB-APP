/** P5 patient schedule commands: request construction and result reading (pure).
 *
 * These target the MongoDB patient-plane routes
 * (`/uctsm/mongo-plane/trials/{trial}/patients/{patient}/commands/...`). No screen
 * calls them yet: the clinical patient schedule screen reads the SQL plane, and
 * moving it is a data-authority decision (see docs/p5-tracks-commands-plan.md).
 * The actor and permissions are NEVER sent; the server derives them. */

export type CommandType =
  | "RECORD_ACTUAL" | "CREATE_UNSCHEDULED" | "RESCHEDULE_APPOINTMENT" | "MARK_MISSED"
  | "CANCEL_OCCURRENCE" | "OVERRIDE_OCCURRENCE" | "PAUSE_TRACK" | "RESUME_TRACK"
  | "REBASE_TRACK" | "STOP_TRACK" | "REVERSE";

export type CommandForm = {
  type: CommandType;
  logicalKey?: string;
  trackCode?: string;
  eventCode?: string;
  visitDate?: string;
  treatmentDate?: string;
  newDate?: string;
  targetDate?: string;
  windowEarliest?: string;
  windowLatest?: string;
  anchorCode?: string;
  newValue?: string;
  authority?: string;
  resumePolicy?: string;
  reversesCommandId?: string;
  effectiveAt: string;
  reason: string;
};

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function need(value: string | undefined, label: string): string | null {
  return value && value.trim() ? null : `${label} is required.`;
}

function date(value: string | undefined, label: string): string | null {
  if (!value || !ISO.test(value)) return `${label} must be a date (YYYY-MM-DD).`;
  return null;
}

/** The request body for a preview, or the first thing the form is missing. */
export function buildCommandPreview(form: CommandForm, horizon?: string):
  { body: Record<string, unknown> } | { error: string } {
  const problems: (string | null)[] = [
    need(form.reason, "Why this is being done"), date(form.effectiveAt, "The effective date"),
  ];
  const body: Record<string, unknown> = {
    type: form.type, effective_at: form.effectiveAt, reason: form.reason.trim(),
  };
  if (horizon) body.horizon = horizon;
  switch (form.type) {
    case "RECORD_ACTUAL":
      problems.push(need(form.logicalKey, "The visit"));
      if (!form.visitDate && !form.treatmentDate) problems.push("Enter the visit date, the treatment date, or both.");
      if (form.visitDate) problems.push(date(form.visitDate, "The visit date"));
      if (form.treatmentDate) problems.push(date(form.treatmentDate, "The treatment date"));
      Object.assign(body, { logical_key: form.logicalKey, visit_date: form.visitDate || null,
        treatment_date: form.treatmentDate || null });
      break;
    case "MARK_MISSED":
    case "CANCEL_OCCURRENCE":
      problems.push(need(form.logicalKey, "The visit"));
      body.logical_key = form.logicalKey;
      break;
    case "RESCHEDULE_APPOINTMENT":
      problems.push(need(form.logicalKey, "The visit"), date(form.newDate, "The new appointment date"));
      Object.assign(body, { logical_key: form.logicalKey, new_date: form.newDate });
      break;
    case "OVERRIDE_OCCURRENCE":
      problems.push(need(form.logicalKey, "The visit"), need(form.authority, "Who authorised this"));
      if (!form.targetDate && !form.windowEarliest && !form.windowLatest) {
        problems.push("Enter a date, a window, or both.");
      }
      for (const [value, label] of [[form.targetDate, "The date"], [form.windowEarliest, "The window start"],
        [form.windowLatest, "The window end"]] as const) if (value) problems.push(date(value, label));
      Object.assign(body, { logical_key: form.logicalKey, target_date: form.targetDate || null,
        window_earliest: form.windowEarliest || null, window_latest: form.windowLatest || null,
        authority: form.authority });
      break;
    case "PAUSE_TRACK":
    case "STOP_TRACK":
      problems.push(need(form.trackCode, "The track"));
      body.track_code = form.trackCode;
      break;
    case "RESUME_TRACK":
      problems.push(need(form.trackCode, "The track"));
      Object.assign(body, { track_code: form.trackCode, resume_policy: form.resumePolicy || null });
      break;
    case "REBASE_TRACK":
      problems.push(need(form.trackCode, "The track"), need(form.anchorCode, "The anchor"),
        date(form.newValue, "The new anchor date"));
      Object.assign(body, { track_code: form.trackCode, anchor_code: form.anchorCode, new_value: form.newValue });
      break;
    case "CREATE_UNSCHEDULED":
      problems.push(need(form.eventCode, "The unscheduled visit"), date(form.visitDate, "The visit date"));
      Object.assign(body, { event_code: form.eventCode, occurred_on: form.visitDate });
      break;
    case "REVERSE":
      problems.push(need(form.reversesCommandId, "The command to reverse"));
      body.reverses_command_id = form.reversesCommandId;
      break;
  }
  const first = problems.find(Boolean);
  return first ? { error: first } : { body };
}

export type ImpactRow = {
  logical_key: string;
  event_code: string;
  changed: boolean;
  change_kind: "NONE" | "PROTOCOL_TARGET" | "STATUS" | "OPERATIONAL" | "ADDED" | "REMOVED";
  protocol: {
    old_target: string | null; new_target: string | null;
    old_window: { earliest: string | null; latest: string | null };
    new_window: { earliest: string | null; latest: string | null };
  };
  status: { old: string | null; new: string | null };
  operational: { before: Record<string, any>; after: Record<string, any> };
  track: { code?: string; display_name?: string; rebase_policy?: string | null; movement?: string } | null;
  reason: string;
};

/** Three separate values per visit - never one date. */
export function describeRow(row: ImpactRow) {
  const after = row.operational.after || {};
  const appointment = after.appointment?.date ?? after.override?.target_date ?? null;
  const actual = after.actual?.visit ?? null;
  const flags: string[] = [];
  if (after.appointment?.outside_protocol_window) flags.push("Appointment is outside the protocol window");
  if (after.override?.outside_protocol_window) flags.push("Exception is outside the protocol window");
  if (after.cancelled) flags.push("Cancelled");
  return {
    track: row.track?.display_name || row.track?.code || "Not in a track",
    protocolTarget: row.protocol.new_target,
    protocolWas: row.protocol.old_target !== row.protocol.new_target ? row.protocol.old_target : null,
    window: row.protocol.new_window,
    appointment, actual, flags, changed: row.changed, why: row.reason,
  };
}

/** Changed first, but unaffected visits stay in the list (they are the reassurance). */
export function orderForConfirmation(rows: ImpactRow[]) {
  return [...rows].sort((a, b) => Number(b.changed) - Number(a.changed)
    || a.event_code.localeCompare(b.event_code));
}

export function commandErrorMessage(status: number, detail: unknown): string {
  const code = typeof detail === "object" && detail ? (detail as any).code : undefined;
  const text = typeof detail === "object" && detail ? (detail as any).message : String(detail ?? "");
  if (status === 403) return "You are not allowed to do this. Ask someone with the right permission.";
  if (code === "PROPOSAL_STALE") return "The schedule changed after this preview. Preview again before confirming.";
  if (code === "NO_ACTIVE_SCHEDULE") return "This patient has no active approved schedule.";
  if (code === "TRACK_POLICY_UNRESOLVED") return "This track's rules have not been confirmed in review yet.";
  if (code === "REBASE_NOT_ALLOWED" || code === "TRACK_NOT_STOPPABLE") return text;
  if (status === 0 || status >= 500) return "The server could not be reached. Nothing was changed.";
  return text || "The request was not accepted.";
}

export type PlaneState = "checking" | "ready" | "absent" | "denied" | "unavailable" | "mismatch" | "mixed";

/** What a failed plane probe means. Only a 404 is "this patient is not there";
 * a 403 is a permission problem and anything else is a service problem to retry. */
export function classifyProbeError(status: number | undefined): PlaneState {
  if (status === 404) return "absent";
  if (status === 401 || status === 403) return "denied";
  return "unavailable";
}

/** Commands are enabled only when the plane's pinned schedule version is the
 * version the screen displays. Existence in both stores is not parity. */
export function planeConsistency(displayedVersionId?: string | null, planeVersionId?: string | null): PlaneState {
  if (!displayedVersionId || !planeVersionId) return "mismatch";
  return displayedVersionId === planeVersionId ? "ready" : "mismatch";
}

/** The plane the HOST screen reads its schedule from. Commands act on the plane
 * they are written to, so a screen that reads another store cannot offer them:
 * the confirmed change would not appear in what it displays (version equality is
 * not parity). Only a screen that itself reads the command plane is eligible. */
export function commandEligibility(
  displayPlane: "SQL" | "MONGO", displayedVersionId?: string | null, planeVersionId?: string | null,
): PlaneState {
  if (displayPlane !== "MONGO") return "mixed";
  return planeConsistency(displayedVersionId, planeVersionId);
}

export const PLANE_MESSAGES: Record<Exclude<PlaneState, "checking" | "ready">, string> = {
  absent: "This patient is not in the patient-command plane, so schedule commands cannot be recorded here yet. Nothing has been changed.",
  denied: "You do not have permission to use schedule commands for this patient.",
  unavailable: "The command service could not be reached. Nothing has been changed.",
  mixed: "This screen shows the schedule from the main schedule store, while commands are recorded in the separate command store. Recording one here would change data this screen does not show, so commands are switched off until both use the same store.",
  mismatch: "The schedule shown here and the command service are on different schedule versions, so commands are switched off to avoid recording a change against the wrong schedule.",
};
