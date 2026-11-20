// The patient calendar's rows for a kit patient: computed from their record and the trial's model
// with the same engine as the staff screens. Pure (no network), so it runs under node --test.
import { computeKitSchedule, type KitVisitRow } from "./index";
import type { KitPatientRecord } from "./api";
import { prepText } from "./describe";

const MODALITY: Record<string, string> = { site: "At the study site", phone: "Phone call", home: "Home visit", remote: "Remote visit" };

/** What the patient needs to know for one visit: preparation, notes and the assessments planned. */
function visitDetail(record: KitPatientRecord, row: KitVisitRow) {
  const def = record.model.visits.find((v) => v.id === row.visitId)
    || record.model.events.flatMap((e) => e.visits).find((v) => v.id === row.visitId)
    || (record.model.study_events || []).flatMap((e) => e.actions.map((a) => a.add_visit)).find((v) => v?.id === row.visitId);
  const prep = def?.prep ? prepText(def.prep) : null;
  return {
    phase: row.phase, visit_type: MODALITY[row.modality] || null,
    // "Fasting 8 h before · Hold metformin", then the protocol's notes for this visit.
    preparation: [prep, ...row.notes.filter((n) => n !== prep),
      // What the participant must do before or at the visit, where it applies to them.
      ...(row.instructions || []).filter((x) => x.kind === "prep" && !x.conditional).map((x) => x.text)].filter(Boolean) as string[],
    procedures: row.procedures.filter((p) => !record.workflow_version || !p.conditional).map((p) => ({
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
    return schedule.rows.flatMap((row) => row.type !== "visit" ||
      (record.workflow_version && row.status !== "done" && (row.status === "waiting" || row.dependsOnDecision)) ? [] : [{
      kit: true, id: row.key, trial_id: record.patient.trial_id, visit_number: ++n, name: `${row.code} · ${row.name}`,
      status: row.status === "done" ? "completed" : row.status === "skipped" ? "cancelled" : "scheduled",
      scheduled_date: row.actual || row.booked || row.date, target_date: row.planned,
      window_start: row.windowStart, window_end: row.windowEnd,
      completed_at: row.actual, protocol_id: record.trial.protocol_id, ...visitDetail(record, row),
    }]);
  } catch {
    return [];
  }
}
