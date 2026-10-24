// The protocol schedule as timeline sections: one section per run of visits with the same phase.
// Pure and import-free (type imports only) so it runs under node --test.
import type { KitDecisionRow, KitModel, KitSchedule, KitVisitRow } from "./index";

export type Tone = "green" | "amber" | "violet" | "red";
export type Dot = "site" | "remote" | "decision" | "repeat" | "wait";
export type TimelineRow = {
  key: string; when: string; days: number | null; name: string; meta: string; dot: Dot;
  tags: { text: string; tone: Tone }[]; timepoints: string[]; visitIds: string[]; occurrences: number;
};
export type TimelineSection = { key: string; label: string; range: string; rows: TimelineRow[]; visitIds: string[] };
export type EventSummary = { id: string; label: string; effect: string; tone: Tone };

const DAY = 864e5;
export const daysBetween = (from: string, to: string) =>
  Math.round((Date.parse(to + "T00:00:00Z") - Date.parse(from + "T00:00:00Z")) / DAY);

/** Time from the schedule's start, as people say it: "Day 8", "3 mo", "2 y". */
export function relLabel(days: number, studyDay?: number | null): string {
  if (days < 28) {
    const n = studyDay ?? days;
    return `Day ${n < 0 ? "−" + Math.abs(n) : n}`;
  }
  if (days < 730) return `${Math.round(days / 30.4375)} mo`;
  return `${Math.round(days / 365.25)} y`;
}

/** "26 visits over 14 years": the length of the schedule in its largest sensible unit. */
export function spanText(days: number): string {
  if (days >= 730) return `${Math.round(days / 365.25)} years`;
  if (days >= 60) return `${Math.round(days / 30.4375)} months`;
  if (days >= 14) return `${Math.round(days / 7)} weeks`;
  return `${days} days`;
}

export function windowText([before, after]: [number, number]): string {
  if (!before && !after) return "exact day";
  if (before === after) return `±${before} days`;
  return `−${before} / +${after} days`;
}

const MODALITY: Record<string, string> = { site: "Site visit", phone: "Phone call", home: "Home visit", remote: "Remote visit" };

/** Labels that differ only in case, spaces or hyphens are the same phase. Nothing else is merged. */
export const phaseKey = (label: string) => label.trim().toLowerCase().replace(/[\s\-_]+/g, " ");

/** "11 y" + "14 y" -> "11–14 y"; anything else -> "first – last". */
export function joinWhen(first: string, last: string): string {
  if (first === last) return first;
  const a = /^(\d+) (\S+)$/.exec(first), b = /^(\d+) (\S+)$/.exec(last);
  if (a && b && a[2] === b[2]) return `${a[1]}–${b[1]} ${a[2]}`;
  return `${first} – ${last}`;
}

type Draft = TimelineRow & { phase: string; visitId: string | null; firstWhen: string; lastWhen: string; lastCode: string };

/**
 * Build the timeline. `schedule` is the model computed for a stand-in patient whose start anchor
 * is `startIso` and who has answered nothing, so every visit shows with its offset from the start.
 * `conditionOf(visitId)` gives the "only if" text for a visit that applies to some patients.
 */
export function protocolTimeline(
  model: KitModel, schedule: KitSchedule, startIso: string, conditionOf: (visitId: string) => string | null,
) {
  const defs = new Map(model.visits.map((v) => [v.id, v]));
  const drafts: Draft[] = [];
  let phase = "Visit";
  for (const row of schedule.rows as (KitVisitRow | KitDecisionRow)[]) {
    if (row.type === "decision") {
      const days = row.date ? daysBetween(startIso, row.date) : null;
      const when = days == null ? "Later" : relLabel(days);
      drafts.push({
        key: row.key, phase, visitId: null, days, when, firstWhen: when, lastWhen: when, lastCode: "", name: row.text,
        meta: `Decision${row.at ? ` at ${row.at}` : ""} · answered for each patient`, dot: "decision",
        tags: [{ text: row.options.map((o) => o.label).join(" / "), tone: "violet" }], timepoints: [], visitIds: [], occurrences: 0,
      });
      continue;
    }
    phase = row.phase || "Visit";
    const def = defs.get(row.visitId);
    const days = row.date ? daysBetween(startIso, row.date) : null;
    const tags: { text: string; tone: Tone }[] = [];
    if (row.dependsOnDecision) {
      const cond = conditionOf(row.visitId);
      tags.push({ text: cond ? `Only if ${cond}` : "Only for some patients", tone: "amber" });
    }
    if (row.until) tags.push({ text: `Until ${row.until}`, tone: "amber" });
    if (def?.duration_days) tags.push({ text: `Lasts ${def.duration_days} days`, tone: "green" });
    if (row.timepoints.length) tags.push({ text: `${row.timepoints.length} time points`, tone: "green" });
    const meta = [MODALITY[row.modality] || "Visit", days == null ? row.relative : null, windowText(row.windowDays),
      row.procedures.length ? `${row.procedures.length} assessment${row.procedures.length === 1 ? "" : "s"}` : null]
      .filter(Boolean).join(" · ");
    const when = days == null ? "Waits" : relLabel(days, row.studyDay);
    drafts.push({
      key: row.key, phase, visitId: row.visitId, days, when, firstWhen: when, lastWhen: when, lastCode: row.code,
      name: `${row.code} · ${row.name}`, meta, dot: days == null ? "wait" : row.modality === "site" ? "site" : "remote",
      tags, timepoints: row.timepoints.map((tp) => tp.label), visitIds: [row.visitId], occurrences: 1,
    });
  }

  // Consecutive rows whose phases match form one section; a phase that comes back later starts a new one.
  const groups: { label: string; key: string; rows: Draft[] }[] = [];
  for (const d of drafts) {
    const last = groups[groups.length - 1];
    if (last && last.key === phaseKey(d.phase)) last.rows.push(d);
    else groups.push({ label: d.phase, key: phaseKey(d.phase), rows: [d] });
  }

  const sections: TimelineSection[] = groups.map((g, gi) => {
    // A repeating visit shows once, where it first occurs, with its count.
    const rows: Draft[] = [];
    const seen = new Map<string, Draft>();
    for (const d of g.rows) {
      const def = d.visitId ? defs.get(d.visitId) : undefined;
      const prior = d.visitId && def?.repeat ? seen.get(d.visitId) : undefined;
      if (prior) {
        prior.occurrences += 1;
        prior.lastWhen = d.when;
        prior.lastCode = d.lastCode;
        continue;
      }
      const copy: Draft = { ...d, tags: [...d.tags] };
      rows.push(copy);
      if (d.visitId && def?.repeat) seen.set(d.visitId, copy);
    }
    for (const r of rows) {
      const def = r.visitId ? defs.get(r.visitId) : undefined;
      if (!def?.repeat) continue;
      const every = Number(def.repeat.every_days) || 0;
      if (r.occurrences > 1) {
        // The definition's own code ("V21-24"), not the generated occurrence codes, which can
        // repeat codes used elsewhere in the protocol.
        r.when = joinWhen(r.firstWhen, r.lastWhen);
        r.name = `${def.code || `${r.name.split(" · ")[0]}–${r.lastCode}`} · ${def.name}`;
      }
      if (r.dot !== "wait") r.dot = "repeat";
      r.meta = [every ? `Every ${every} days` : null, r.meta].filter(Boolean).join(" · ");
      r.tags.push({ text: def.repeat.open_ended ? `Shows ${r.occurrences} ahead` : `Repeats ×${r.occurrences}`, tone: "green" });
    }
    const dated = rows.filter((r) => r.days != null);
    const count = rows.reduce((n, r) => n + r.occurrences, 0);
    const visits = `${count} visit${count === 1 ? "" : "s"}`;
    const range = dated.length
      ? `${joinWhen(dated[0].firstWhen, dated[dated.length - 1].lastWhen)} · ${visits}`
      : `Waits for a date · ${visits}`;
    const visitIds = [...new Set(rows.flatMap((r) => r.visitIds))];
    return {
      key: `${g.key}-${gi}`, label: g.label, range, visitIds,
      rows: rows.map(({ phase: _p, visitId: _v, firstWhen: _f, lastWhen: _l, lastCode: _c, ...r }) => r),
    };
  });

  const datedVisits = drafts.filter((d) => d.visitId && d.days != null);
  return {
    sections,
    visitCount: datedVisits.length,
    waitingCount: drafts.filter((d) => d.visitId && d.days == null).length,
    spanDays: datedVisits.reduce((m, d) => Math.max(m, d.days ?? 0), 0),
  };
}

export function eventSummaries(model: KitModel): EventSummary[] {
  const label = (id: string) => model.anchors.find((a) => a.id === id)?.label || id;
  return model.events.map((e) => {
    const sets = (e as { sets_anchor?: string | null }).sets_anchor;
    if (e.ends_schedule) return { id: e.id, label: e.label, effect: "Ends schedule", tone: "red" };
    if (e.visits.length) return { id: e.id, label: e.label, effect: `Adds ${e.visits.length} visit${e.visits.length === 1 ? "" : "s"}`, tone: "green" };
    if (sets) return { id: e.id, label: e.label, effect: `Sets ${label(sets)}`, tone: "amber" };
    return { id: e.id, label: e.label, effect: "Recorded", tone: "green" };
  });
}

/** Rename every visit in a section; sections whose names then match join on their own. */
export function renamePhase<T extends { visits: { id: string; phase: string }[] }>(model: T, visitIds: string[], label: string): T {
  const ids = new Set(visitIds);
  return { ...model, visits: model.visits.map((v) => (ids.has(v.id) ? { ...v, phase: label } : v)) };
}
