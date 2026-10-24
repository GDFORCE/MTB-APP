// One patient's record on the kit schedule, and the changes a site makes to it during the study.
// Every change returns a new record; the engine (computeKitSchedule) turns a record into dates.
// Pure and import-free so it runs under node --test.
import type { KitParticipant, KitSchedule, KitVisitRow } from "./index";

export type KitLoggedEvent = { key: string; type: string; date: string; answers: Record<string, string> };

const without = <T extends Record<string, unknown>>(record: T, key: string): T => {
  const next = { ...record };
  delete next[key];
  return next;
};

/** Record (or with null, clear) the date a visit occurrence ("visitId#n") actually happened. */
export function recordVisit(p: KitParticipant, key: string, date: string | null): KitParticipant {
  const actuals = p.actuals || {};
  return { ...p, actuals: date ? { ...actuals, [key]: date } : without(actuals, key) };
}

/** Answer a decision or participant question; null clears it. */
export function answer(p: KitParticipant, questionId: string, value: string | null): KitParticipant {
  return { ...p, answers: value ? { ...p.answers, [questionId]: value } : without(p.answers, questionId) };
}

/** Enter the date of an "ask" anchor (surgery, end of study, ...) the protocol cannot compute. */
export const setAnchorDate = (p: KitParticipant, anchorId: string, date: string | null) => answer(p, "@" + anchorId, date);

export function logEvent(p: KitParticipant, event: Omit<KitLoggedEvent, "key">, key = `${event.type}-${Date.now().toString(36)}`): KitParticipant {
  return { ...p, events: [...(p.events || []), { ...event, key }] };
}

export function removeEvent(p: KitParticipant, key: string): KitParticipant {
  return { ...p, events: (p.events || []).filter((e) => e.key !== key) };
}

const visits = (schedule: KitSchedule) => schedule.rows.filter((r): r is KitVisitRow => r.type === "visit");

/** A scheduled visit whose window has closed without a recorded date. */
export const isOverdue = (row: KitVisitRow, today: string) =>
  row.status === "scheduled" && !!row.date && (row.windowEnd || row.date) < today;

/** The first visit still to come (or still open) on or after today. */
export function nextVisit(schedule: KitSchedule, today: string): KitVisitRow | null {
  return visits(schedule).find((r) => r.status === "scheduled" && !!r.date && (r.windowEnd || r.date) >= today) || null;
}

export function progress(schedule: KitSchedule) {
  const rows = visits(schedule);
  return { done: rows.filter((r) => r.status === "done").length, total: rows.length };
}

// Forms take dates as DD/MM/YYYY; the record stores YYYY-MM-DD.
export function formatDmyInput(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
}

export function dmyToIso(value: string): string | null {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
  if (!m) return null;
  const [day, month, year] = [+m[1], +m[2], +m[3]];
  const d = new Date(Date.UTC(year, month - 1, day));
  return d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day
    ? `${m[3]}-${m[2]}-${m[1]}` : null;
}

export const isoToDmy = (iso: string) => iso.split("-").reverse().join("/");

/** The patient-list status for a kit patient, in the same buckets the list uses for everyone. */
export function kitStatus(schedule: KitSchedule, today: string): "active" | "overdue" | "completed" | "no_visits" {
  const rows = visits(schedule);
  if (!rows.length) return "no_visits";
  if (schedule.ended || rows.every((r) => r.status === "done")) return "completed";
  return rows.some((r) => isOverdue(r, today)) ? "overdue" : "active";
}
