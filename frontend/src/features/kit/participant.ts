// One patient's record on the kit schedule, and the changes a site makes to it during the study.
// Every change returns a new record; the engine (computeKitSchedule) turns a record into dates.
// Pure and import-free so it runs under node --test.
import type { KitModel, KitParticipant, KitSchedule, KitVisitRow } from "./index";

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

/**
 * Record a visit check for one occurrence on the date it was checked: a Yes/No ("Is ANC ≥ 1.0?"),
 * the values measured (the engine works the Yes/No out from the protocol's limits), or both.
 */
export function recordCheck(p: KitParticipant, key: string, date: string, value: "yes" | "no" | "skip" | "reduce" | null,
  values?: Record<string, number>): KitParticipant {
  const measured = values && Object.keys(values).length ? values : undefined;
  if (!value && !measured) return p;
  const entry = { date, ...(value ? { answer: value } : {}), ...(measured ? { values: measured } : {}) };
  const list = [...(p.checks?.[key] || []), entry]
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return { ...p, checks: { ...(p.checks || {}), [key]: list } };
}

/** Record (or with null, clear) a visit's time, HH:MM: the check-in time, or the dose time. */
export function setVisitTime(p: KitParticipant, key: string, time: string | null): KitParticipant {
  const times = p.doseTimes || {};
  return { ...p, doseTimes: time ? { ...times, [key]: time } : without(times, key) };
}

/** "2030" -> "20:30" while typing; a real time "HH:MM" or null. */
export function formatTimeInput(value: string): string {
  const d = value.replace(/\D/g, "").slice(0, 4);
  return d.length <= 2 ? d : `${d.slice(0, 2)}:${d.slice(2)}`;
}
export const validTime = (value: string) => (/^([01]\d|2[0-3]):[0-5]\d$/.test(value) ? value : null);

/** Enter (or with null, clear) a patient's baseline value for a measure. */
export function setBaseline(p: KitParticipant, measureId: string, value: number | null): KitParticipant {
  const baselines = p.baselines || {};
  const next = value == null ? without(baselines, measureId) : { ...baselines, [measureId]: value };
  return Object.keys(next).length ? { ...p, baselines: next } : without(p as Record<string, unknown>, "baselines") as KitParticipant;
}

/** Set (or with null, go back to the protocol's) dose level of a drug at one dose; later doses continue from it. */
export function setDoseLevel(p: KitParticipant, key: string, doseId: string, level: string | null): KitParticipant {
  const all = p.doseLevels || {};
  const here = level == null ? without(all[key] || {}, doseId) : { ...(all[key] || {}), [doseId]: level };
  const next = Object.keys(here).length ? { ...all, [key]: here } : without(all, key);
  return Object.keys(next).length ? { ...p, doseLevels: next } : without(p as Record<string, unknown>, "doseLevels") as KitParticipant;
}

/** Text typed for a measured value -> a number, or null when it isn't one (grades: whole 0-5). */
export function parseMeasured(text: string, kind: "value" | "grade"): number | null {
  const t = text.trim().replace(",", ".");
  if (!t || !/^-?\d*\.?\d+$/.test(t)) return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  return kind === "grade" ? (Number.isInteger(n) && n >= 0 && n <= 5 ? n : null) : n;
}

/** Remove one recorded check answer (by its position in date order). */
export function removeCheck(p: KitParticipant, key: string, index: number): KitParticipant {
  const list = (p.checks?.[key] || []).filter((_, i) => i !== index);
  const checks = p.checks || {};
  return { ...p, checks: list.length ? { ...checks, [key]: list } : without(checks, key) };
}

/** Answer a decision or participant question; null clears it. */
export function answer(p: KitParticipant, questionId: string, value: string | null): KitParticipant {
  return { ...p, answers: value ? { ...p.answers, [questionId]: value } : without(p.answers, questionId) };
}

/** Enter a protocol date (randomisation, surgery, ...); it replaces one counted from another date. */
export const setAnchorDate = (p: KitParticipant, anchorId: string, date: string | null) => answer(p, "@" + anchorId, date);

type KitAnchorDef = KitModel["anchors"][number];

/**
 * The protocol dates a site can enter for a patient besides the start date: randomisation / Day 1,
 * surgery and so on. Dates set by logging an event (progression, end of treatment) are left to
 * that event. Day 1 comes first.
 */
export function enterableAnchors(model: KitModel): KitAnchorDef[] {
  const byEvent = new Set(model.events.map((e) => e.sets_anchor).filter(Boolean));
  // The last of several visits (last dose) is worked out from them, never entered.
  const list = model.anchors.filter((a) => a.kind !== "start" && a.kind !== "last_visit" && !byEvent.has(a.id));
  return [...list.filter((a) => a.is_day1), ...list.filter((a) => !a.is_day1)];
}

/**
 * Where each protocol date falls in the plan, in days from the start date: from its offset to
 * another date, the planned timing the protocol gives for it (the earliest when it depends on an
 * answer) or the visit that sets it. Null when the protocol gives no timing (progression, ...).
 */
export function anchorPositions(model: KitModel): Record<string, number | null> {
  const pos: Record<string, number | null> = {};
  for (const a of model.anchors) pos[a.id] = a.kind === "start" ? 0 : null;
  const known = (id: string | null | undefined) => (id != null && pos[id] != null ? pos[id]! : null);
  const visitPos = (id: string) => {
    const v = model.visits.find((x) => x.id === id);
    const base = v ? known(v.anchor) : null;
    return base == null ? null : base + (Number(v!.offset_days) || 0);
  };
  for (let i = 0; i < 25; i++) {
    let changed = false;
    for (const a of model.anchors) {
      if (pos[a.id] != null) {
        // Counted backwards: a date known from the one it counts from also fixes that one.
        if (a.kind === "relative" && a.relative_to && pos[a.relative_to] == null
          && model.anchors.find((x) => x.id === a.relative_to)?.kind !== "ask") {
          pos[a.relative_to] = pos[a.id]! - (Number(a.offset_days) || 0); changed = true;
        }
        continue;
      }
      let at: number | null = null;
      if (a.kind === "relative" && known(a.relative_to) != null) at = known(a.relative_to)! + (Number(a.offset_days) || 0);
      if (at == null && a.kind === "ask") {
        const options = (a.estimates || []).map((e) => (known(e.relative_to) == null ? null : known(e.relative_to)! + (Number(e.offset_days) || 0)));
        if (options.length && options.every((x) => x != null)) at = Math.min(...(options as number[]));
      }
      if (at == null && a.set_by_visit) at = visitPos(a.set_by_visit);
      if (at == null && a.kind === "last_visit" && a.last_of?.length) {
        const all = a.last_of.map(visitPos);
        if (all.every((x) => x != null)) at = Math.max(...(all as number[]));
      }
      if (at != null) { pos[a.id] = at; changed = true; }
    }
    if (!changed) break;
  }
  return pos;
}

/**
 * The protocol dates to ask when the patient is added: those the protocol places at or before
 * Day 1 (run-in, randomisation, ...). Dates that fall between later visits (surgery, a second
 * period) or have no planned timing are added on the patient's schedule once they happen.
 */
export function enrolmentAnchors(model: KitModel): KitAnchorDef[] {
  const pos = anchorPositions(model);
  const day1 = model.anchors.find((a) => a.is_day1);
  const cutoff = day1 ? pos[day1.id] : 0;
  return enterableAnchors(model).filter((a) => a.is_day1 || (cutoff != null && pos[a.id] != null && pos[a.id]! <= cutoff));
}

const labelOfAnchor = (model: KitModel, id: string) => model.anchors.find((x) => x.id === id)?.label || id;

/** "The protocol allows 1 to 28 days after Screening." for a date with a range, or null. */
export function rangeText(model: KitModel, a: KitAnchorDef): string | null {
  const g = a.range;
  if (!g?.relative_to || (g.min_days == null && g.max_days == null)) return null;
  const from = labelOfAnchor(model, g.relative_to);
  const side = (n: number) => (n < 0 ? `${-n} day${n === -1 ? "" : "s"} before` : `${n} day${n === 1 ? "" : "s"} after`);
  if (g.min_days != null && g.max_days != null) {
    return g.min_days < 0 && g.max_days <= 0
      ? `The protocol allows ${-g.max_days} to ${-g.min_days} days before ${from}.`
      : `The protocol allows ${g.min_days} to ${g.max_days} days after ${from}.`;
  }
  return g.min_days != null ? `The protocol allows at least ${side(g.min_days)} ${from}.` : `The protocol allows at most ${side(g.max_days!)} ${from}.`;
}

/** Entered dates outside the range the protocol allows (a deviation: shown, never refused). */
export function anchorRangeWarnings(model: KitModel, startAnchor: string, startDate: string | null, answers: Record<string, string>) {
  const dateOf = (id: string) => (id === startAnchor ? startDate : answers["@" + id]) || null;
  const out: Record<string, string> = {};
  for (const a of model.anchors) {
    const g = a.range, own = dateOf(a.id), base = g?.relative_to ? dateOf(g.relative_to) : null;
    if (!g || !own || !base) continue;
    const d = Math.round((Date.parse(own + "T00:00:00Z") - Date.parse(base + "T00:00:00Z")) / 864e5);
    if ((g.min_days != null && d < g.min_days) || (g.max_days != null && d > g.max_days)) {
      out[a.id] = `${a.label} is ${Math.abs(d)} day${Math.abs(d) === 1 ? "" : "s"} ${d < 0 ? "before" : "after"} ${labelOfAnchor(model, g.relative_to)}. ${rangeText(model, a)}`;
    }
  }
  return out;
}

/** "Counted from Screening + 28 days" for a date the site may leave empty, or null. */
export function countedFromText(model: KitModel, a: KitAnchorDef): string | null {
  if (a.kind !== "relative" || !a.relative_to) return null;
  const from = model.anchors.find((x) => x.id === a.relative_to)?.label || a.relative_to;
  const off = Number(a.offset_days) || 0;
  return `If left empty it is counted as ${from}${off ? ` ${off > 0 ? "+" : "−"} ${Math.abs(off)} days` : ""}, `
    + "which is often not the real date. Enter it once known.";
}

/**
 * Entered dates that cannot be right: a date the protocol places after another one (Day 1 after
 * screening) entered before it. Returns { anchorId: message }.
 */
export function anchorDateProblems(model: KitModel, startAnchor: string, startDate: string | null, answers: Record<string, string>) {
  const dateOf = (id: string) => (id === startAnchor ? startDate : answers["@" + id]) || null;
  const problems: Record<string, string> = {};
  for (const a of model.anchors) {
    const own = dateOf(a.id), parentId = a.relative_to, off = Number(a.offset_days) || 0;
    if (!own || !parentId || !off) continue;
    const parent = dateOf(parentId), parentLabel = model.anchors.find((x) => x.id === parentId)?.label || parentId;
    // Counted forwards (Day 1 = screening + 28) it can't come first; backwards (screening = Day 1 − 28) it can't come after.
    if (parent && off > 0 && own < parent) problems[a.id] = `${a.label} can't be before ${parentLabel}.`;
    if (parent && off < 0 && own > parent) problems[a.id] = `${a.label} can't be after ${parentLabel}.`;
  }
  return problems;
}

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
  if (schedule.ended || rows.every((r) => r.status === "done" || r.status === "skipped")) return "completed";
  return rows.some((r) => isOverdue(r, today)) ? "overdue" : "active";
}
