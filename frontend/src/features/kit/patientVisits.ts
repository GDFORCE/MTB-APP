import { api } from "@/src/api/client";
import { computeKitSchedule, type KitVisitRow } from "./index";
import { getMyKit, type KitPatientRecord } from "./api";
import { prepText } from "./describe";

const MODALITY: Record<string, string> = { site: "At the study site", phone: "Phone call", home: "Home visit", remote: "Remote visit" };

/** What the patient needs to know for one visit: preparation, notes and the assessments planned. */
function visitDetail(record: KitPatientRecord, row: KitVisitRow) {
  const def = record.model.visits.find((v) => v.id === row.visitId)
    || record.model.events.flatMap((e) => e.visits).find((v) => v.id === row.visitId);
  const prep = def?.prep ? prepText(def.prep) : null;
  return {
    phase: row.phase, visit_type: MODALITY[row.modality] || null,
    // "Fasting 8 h before · Hold metformin", then the protocol's notes for this visit.
    preparation: [prep, ...row.notes.filter((n) => n !== prep)].filter(Boolean) as string[],
    procedures: row.procedures.map((p) => ({
      label: p.name,
      description: [p.need === "if_indicated" ? "Only if your study doctor decides it is needed" : p.need === "optional" ? "Optional" : null,
        p.conditional && p.condition ? `Only if ${p.condition}` : null,
        p.withinBefore?.from ? `Done from ${p.withinBefore.from.split("-").reverse().join("/")} (within ${"hours" in p.withinBefore ? `${p.withinBefore.hours} h` : `${p.withinBefore.days} days`} before)` : null,
      ].filter(Boolean).join(" · ") || undefined,
    })),
  };
}

/**
 * A kit patient's computed visits in the row shape the patient dashboard reads from
 * /visits/mine, so its next-visit card, progress and calendar work unchanged.
 */
export function kitVisitRows(record: KitPatientRecord) {
  try {
    const schedule = computeKitSchedule(record.model, record.site_answers, record.participant);
    let n = 0;
    return schedule.rows.flatMap((row) => row.type !== "visit" ? [] : [{
      kit: true, id: row.key, trial_id: record.patient.trial_id, visit_number: ++n, name: `${row.code} · ${row.name}`,
      status: row.status === "done" ? "completed" : row.status === "skipped" ? "cancelled" : "scheduled",
      scheduled_date: row.date, window_start: row.windowStart, window_end: row.windowEnd,
      completed_at: row.actual, protocol_id: record.trial.protocol_id, ...visitDetail(record, row),
    }]);
  } catch {
    return [];
  }
}

/** One of the signed-in kit patient's visits by its key ("c1d1#2"), or null when they are not a kit patient. */
export async function fetchMyKitVisit(key: string): Promise<any | null> {
  const record = await getMyKit().catch(() => null);
  if (!record) return null;
  return kitVisitRows(record).find((row) => row.id === key) || null;
}

/** The signed-in patient's visit rows: computed for a kit patient, from /visits/mine for everyone else. */
export async function fetchMyVisits(): Promise<{ data: any[] }> {
  const record = await getMyKit().catch(() => null);
  return record ? { data: kitVisitRows(record) } : api.get("/visits/mine");
}
