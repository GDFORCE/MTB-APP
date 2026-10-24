import { api } from "@/src/api/client";
import { computeKitSchedule } from "./index";
import { getMyKit, type KitPatientRecord } from "./api";

/**
 * A kit patient's computed visits in the row shape the patient dashboard reads from
 * /visits/mine, so its next-visit card, progress and calendar work unchanged.
 */
export function kitVisitRows(record: KitPatientRecord) {
  try {
    const schedule = computeKitSchedule(record.model, record.site_answers, record.participant);
    return schedule.rows.flatMap((row, index) => row.type !== "visit" ? [] : [{
      kit: true, id: row.key, trial_id: record.patient.trial_id, visit_number: index + 1, name: `${row.code} · ${row.name}`,
      status: row.status === "done" ? "completed" : "scheduled",
      scheduled_date: row.date, window_start: row.windowStart, window_end: row.windowEnd,
    }]);
  } catch {
    return [];
  }
}

/** The signed-in patient's visit rows: computed for a kit patient, from /visits/mine for everyone else. */
export async function fetchMyVisits(): Promise<{ data: any[] }> {
  const record = await getMyKit().catch(() => null);
  return record ? { data: kitVisitRows(record) } : api.get("/visits/mine");
}
