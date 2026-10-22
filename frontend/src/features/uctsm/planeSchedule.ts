/** Presentation of the MongoDB patient plane's committed calendar (pure).
 *
 * Three different things are ALWAYS shown separately: the protocol target and
 * window, the operational appointment or exception, and what actually happened.
 * An unknown window is never shown as a zero-width one. */

export type PlaneEntry = {
  logical_key: string; event_code: string; status: string; completed: boolean;
  planned_start: string | null; earliest: string | null; latest: string | null;
  window_known: boolean; original_planned: string | null;
  appointment_date: string | null; appointment_outside_window: boolean | null;
  override_target: string | null; override_window_earliest: string | null;
  override_window_latest: string | null; override_outside_window: boolean | null;
  override_authority: string | null; override_reason: string | null;
  cancelled: boolean; actual_visit_date: string | null; actual_treatment_date: string | null;
  track_name: string | null; track_code: string | null; provisional: boolean;
  evaluation_id: string;
  rule_handling?: string | null;
};

export type PlaneRow = {
  key: string; visit: string; track: string; status: string;
  protocolTarget: string; protocolWindow: string;
  appointment: string; exception: string; actualVisit: string; actualTreatment: string;
  flags: string[]; evaluationId: string;
};

const dash = "not set";

export function describeWindow(entry: Pick<PlaneEntry, "window_known" | "earliest" | "latest">): string {
  if (!entry.window_known) return "No window stated";            // unknown, not zero-width
  if (entry.earliest && entry.latest) {
    return entry.earliest === entry.latest ? `exactly ${entry.earliest}` : `${entry.earliest} to ${entry.latest}`;
  }
  return entry.earliest ? `from ${entry.earliest}` : `until ${entry.latest}`;
}

export function toPlaneRows(entries: PlaneEntry[]): PlaneRow[] {
  return entries.map((entry) => {
    const flags: string[] = [];
    if (entry.rule_handling === "MANUAL_ONLY") flags.push("Manual scheduling required; no automatic target");
    if (entry.appointment_outside_window === true) flags.push("Appointment is outside the protocol window");
    if (entry.appointment_date && entry.appointment_outside_window === null) {
      flags.push("Appointment cannot be checked: the protocol states no window");
    }
    if (entry.override_outside_window === true) flags.push("Exception is outside the protocol window");
    if (entry.cancelled) flags.push("Cancelled");
    if (entry.provisional) flags.push("Provisional: the series end is not dated yet");
    const exceptionParts = [
      entry.override_target ? `date ${entry.override_target}` : "",
      entry.override_window_earliest || entry.override_window_latest
        ? `window ${entry.override_window_earliest ?? "?"} to ${entry.override_window_latest ?? "?"}` : "",
      entry.override_authority ? `authorised by ${entry.override_authority}` : "",
    ].filter(Boolean);
    return {
      key: entry.logical_key, visit: entry.event_code, track: entry.track_name || "Not in a track",
      status: entry.status, protocolTarget: entry.planned_start ?? "not dated",
      protocolWindow: describeWindow(entry), appointment: entry.appointment_date ?? dash,
      exception: exceptionParts.length ? exceptionParts.join(", ") : dash,
      actualVisit: entry.actual_visit_date ?? "not recorded",
      actualTreatment: entry.actual_treatment_date ?? "not recorded",
      flags, evaluationId: entry.evaluation_id,
    };
  });
}

export type PatientPage = { items: { external_patient_id: string; patient_code: string }[]; next_cursor: string | null };

/** Append a page without ever duplicating a patient (a retried page is harmless). */
export function mergePage(existing: PatientPage["items"], page: PatientPage): PatientPage["items"] {
  const seen = new Set(existing.map((item) => item.external_patient_id));
  return [...existing, ...page.items.filter((item) => !seen.has(item.external_patient_id))];
}
