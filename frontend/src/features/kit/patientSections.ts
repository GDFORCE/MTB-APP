// One patient's dated schedule as timeline sections, with what changed and why: late visits,
// visits moved by them, visits an event added or cancelled, decisions and dates still to enter.
// Pure and import-free (type imports only) so it runs under node --test.
import type { KitDecisionRow, KitModel, KitParticipant, KitSchedule, KitVisitRow } from "./index";

export type PTone = "green" | "amber" | "violet" | "red" | "grey";
export type PDot = "done" | "next" | "todo" | "overdue" | "wait" | "decision" | "event" | "struck";
export type PatientRow = {
  key: string; kind: "visit" | "decision" | "event" | "cancelled";
  date: string | null; day: string; mon: string; year: string | null;
  name: string; meta: string; dot: PDot; tags: { text: string; tone: PTone }[];
  visit?: KitVisitRow; decision?: KitDecisionRow; eventKey?: string; waitingAnchor?: string | null;
};
export type PatientSection = { key: string; label: string; range: string; rows: PatientRow[] };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = 864e5;
const parts = (iso: string) => { const [y, m, d] = iso.split("-"); return { y, m: MONTHS[+m - 1], d }; };
const short = (iso: string) => { const p = parts(iso); return `${+p.d} ${p.m}`; };
const monthYear = (iso: string) => { const p = parts(iso); return `${p.m} ${p.y}`; };
const diffDays = (a: string, b: string) => Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / DAY);
const phaseKey = (label: string) => label.trim().toLowerCase().replace(/[\s\-_]+/g, " ");
const MODALITY: Record<string, string> = { phone: "Phone call", home: "Home visit", remote: "Remote visit" };

function windowLine(row: KitVisitRow) {
  if (row.windowStart && row.windowEnd && row.windowStart !== row.windowEnd) return `Window ${short(row.windowStart)} – ${short(row.windowEnd)}`;
  return "Exact day";
}

/**
 * current        the patient's schedule as it stands
 * withoutEvents  the same with no events logged (to find visits an event cancelled)
 * baseline       the plan with no visits recorded and no events (to find visits that moved)
 */
export function patientTimeline({ model, participant, current, withoutEvents, baseline, today, conditionOf }: {
  model: KitModel; participant: KitParticipant; current: KitSchedule; withoutEvents: KitSchedule; baseline: KitSchedule;
  today: string; conditionOf: (visitId: string) => string | null;
}) {
  const defs = new Map(model.visits.map((v) => [v.id, v]));
  const askAnchors = new Set(model.anchors.filter((a) => a.kind === "ask").map((a) => a.id));
  const visitsOf = (s: KitSchedule) => s.rows.filter((r): r is KitVisitRow => r.type === "visit");
  const planned = new Map(visitsOf(baseline).map((r) => [r.key, r.date]));
  const currentVisits = visitsOf(current);
  const next = currentVisits.find((r) => r.status === "scheduled" && !!r.date && (r.windowEnd || r.date) >= today) || null;
  const endedLabel = current.ended ? model.events.find((e) => e.id === current.ended!.event)?.label || "an event" : null;

  type Draft = PatientRow & { phase: string | null };
  const drafts: Draft[] = [];
  const blank = (key: string, kind: PatientRow["kind"], date: string | null) =>
    ({ key, kind, date, day: date ? parts(date).d : "—", mon: date ? parts(date).m : "", year: null } as const);

  let moved = 0, overdue = 0, added = 0, late = 0;
  for (const row of currentVisits) {
    const tags: { text: string; tone: PTone }[] = [];
    const def = defs.get(row.visitId);
    const how = MODALITY[row.modality];
    let dot: PDot, meta: string;
    if (row.status === "done") {
      dot = "done";
      meta = `Done ${short(row.actual || row.date!)}`;
      if (row.planned && row.actual && row.planned !== row.actual) {
        const d = diffDays(row.planned, row.actual);
        late += 1;
        tags.push({ text: `${d > 0 ? "+" : "−"}${Math.abs(d)} day${Math.abs(d) === 1 ? "" : "s"}`, tone: "amber" });
        meta += ` · planned ${short(row.planned)}`;
      }
    } else if (row.status === "waiting" || !row.date) {
      dot = "wait";
      meta = row.relative || "Waits for a date";
      tags.push({ text: "Waiting", tone: "amber" });
    } else {
      const isOverdue = (row.windowEnd || row.date) < today;
      if (isOverdue) overdue += 1;
      dot = isOverdue ? "overdue" : row === next ? "next" : "todo";
      meta = (isOverdue ? "Overdue · " : "") + windowLine(row);
      const was = planned.get(row.key);
      if (was && was !== row.date) { moved += 1; tags.push({ text: `Moved · was ${short(was)}`, tone: "amber" }); }
    }
    if (how) meta = `${how} · ${meta}`;
    if (row.fromEvent) { added += 1; tags.push({ text: `Added by ${row.fromEvent.split(" · ")[0]}`, tone: "green" }); }
    if (row.dependsOnDecision) {
      const cond = conditionOf(row.visitId);
      tags.push({ text: cond ? `Only if ${cond}` : "Depends on an answer", tone: "violet" });
    }
    if (row.until) tags.push({ text: `Until ${row.until}`, tone: "amber" });
    if (row.status !== "done") for (const label of row.stopsIf || []) tags.push({ text: `Stops if ${label}`, tone: "grey" });
    drafts.push({
      ...blank(row.key, "visit", row.date), phase: row.phase || "Visit", name: `${row.code} · ${row.name}`, meta, dot, tags, visit: row,
      waitingAnchor: dot === "wait" && def && !def.follows && askAnchors.has(def.anchor) ? def.anchor : null,
    });
  }

  for (const row of current.rows) {
    if (row.type !== "decision") continue;
    const chosen = row.options.find((o) => o.value === row.answer)?.label || row.answer;
    drafts.push({
      ...blank(row.key, "decision", row.date), phase: null, name: row.text,
      meta: chosen ? `Answered: ${chosen}` : `Decision${row.at ? ` at ${row.at}` : ""}`,
      dot: "decision", tags: [], decision: row,
    });
  }

  for (const event of participant.events || []) {
    const def = model.events.find((e) => e.id === event.type);
    const forEvent = (model.rules || []).filter((r) => "event" in r.when && r.when.event === event.type);
    // With branches (before / after Week 12, grade 3 / grade 1) the one that acted is the one to show.
    const rule = forEvent.find((r) => current.stoppedBy.some((b) => b.rule === r.id))
      || forEvent.find((r) => !r.if?.length) || null;
    const stops = rule && !rule.stop_all ? rule.stop_visits.length : 0;
    const effect = def?.ends_schedule ? { text: "Ends schedule", tone: "red" as PTone }
      : stops ? { text: `Stops ${stops} visit${stops === 1 ? "" : "s"}`, tone: "red" as PTone }
      : def?.visits.length ? { text: `Adds ${def.visits.length} visit${def.visits.length === 1 ? "" : "s"}`, tone: "green" as PTone }
      : (def as { sets_anchor?: string | null } | undefined)?.sets_anchor ? { text: "Sets a date", tone: "amber" as PTone } : null;
    drafts.push({
      ...blank(`event-${event.key}`, "event", event.date), phase: null, name: def?.label || event.type,
      meta: `Event logged ${short(event.date)}`, dot: "event", tags: effect ? [effect] : [], eventKey: event.key,
    });
  }

  // Visits a logged event took away show as one crossed-out row where the schedule changed, not
  // one row each, so a long list of cancelled cycles does not break the timeline into pieces.
  // Visits a rule stopped (Injection 2 given, progression...) show the same way, one row per rule,
  // at the rule's date, saying which rule did it.
  const stoppedBy = current.stoppedBy || [];
  const stoppedKeys = new Set(stoppedBy.flatMap((g) => g.visits.map((x) => x.key)));
  for (const g of stoppedBy) {
    const codes = g.visits.map((x) => x.code);
    const at = g.date || g.visits.find((x) => x.date)?.date || null;
    drafts.push({
      ...blank(`stopped-${g.rule}`, "cancelled", at), phase: null,
      name: `${codes.length} planned visit${codes.length === 1 ? "" : "s"} stopped`,
      meta: codes.slice(0, 4).join(", ") + (codes.length > 4 ? ` and ${codes.length - 4} more` : ""), dot: "struck",
      tags: [{ text: `By ${g.label}`, tone: "red" }],
    });
  }

  const currentKeys = new Set(currentVisits.map((r) => r.key));
  const gone = visitsOf(withoutEvents).filter((r) => !currentKeys.has(r.key) && !stoppedKeys.has(r.key) && r.status !== "done" && !!r.date);
  const cancelled = gone.length + stoppedKeys.size;
  if (gone.length) {
    // Placed at the event that caused it: the ending event, else the last one logged before the
    // first cancelled visit (progression, for example, stops cycles without ending the schedule).
    const cause = [...(participant.events || [])].sort((x, y) => (x.date < y.date ? 1 : -1)).find((e) => e.date <= gone[0].date!);
    const causeLabel = endedLabel || (cause && model.events.find((e) => e.id === cause.type)?.label) || null;
    const at = current.ended?.date || cause?.date || gone[0].date!;
    const codes = gone.map((r) => r.code);
    drafts.push({
      ...blank("cancelled", "cancelled", at), phase: null,
      name: `${gone.length} planned visit${gone.length === 1 ? "" : "s"} cancelled`,
      meta: codes.slice(0, 4).join(", ") + (codes.length > 4 ? ` and ${codes.length - 4} more` : ""), dot: "struck",
      tags: [{ text: causeLabel ? `By ${causeLabel}` : "By a logged event", tone: "red" }],
    });
  }

  // Dated rows in date order, then the ones still waiting for a date in their original order.
  const ordered = drafts.map((d, i) => ({ d, i })).sort((a, b) => {
    if (a.d.date && b.d.date) return a.d.date < b.d.date ? -1 : a.d.date > b.d.date ? 1 : a.i - b.i;
    if (a.d.date) return -1;
    if (b.d.date) return 1;
    return a.i - b.i;
  }).map(({ d }) => d);

  let lastYear: string | null = null;
  let phase = ordered.find((d) => d.phase)?.phase || "Visit";
  const groups: { label: string; key: string; rows: Draft[] }[] = [];
  for (const d of ordered) {
    if (d.phase) phase = d.phase; else d.phase = phase;
    if (d.date) {
      const y = parts(d.date).y;
      if (y !== lastYear) { d.year = y; lastYear = y; }
    }
    const last = groups[groups.length - 1];
    if (last && last.key === phaseKey(d.phase)) last.rows.push(d);
    else groups.push({ label: d.phase, key: phaseKey(d.phase), rows: [d] });
  }

  const sections: PatientSection[] = groups.map((g, gi) => {
    const visits = g.rows.filter((r) => r.kind === "visit");
    const done = visits.filter((r) => r.dot === "done").length;
    const dated = g.rows.filter((r) => r.date);
    const first = dated[0]?.date, last = dated[dated.length - 1]?.date;
    const span = !first ? "Waits for a date" : monthYear(first) === monthYear(last!) ? monthYear(first) : `${monthYear(first)} – ${monthYear(last!)}`;
    const count = visits.length ? (done === visits.length ? `all ${done} done` : done ? `${done}/${visits.length} done` : `${visits.length} visit${visits.length === 1 ? "" : "s"}`) : "";
    return { key: `${g.key}-${gi}`, label: g.label, range: [span, count].filter(Boolean).join(" · "), rows: g.rows.map(({ phase: _p, ...r }) => r) };
  });

  const pendingAnchors = [...new Set(drafts.map((d) => d.waitingAnchor).filter((a): a is string => !!a))]
    .map((id) => ({ id, label: model.anchors.find((a) => a.id === id)?.label || id, waiting: drafts.filter((d) => d.waitingAnchor === id).length }));
  return {
    sections, next,
    done: currentVisits.filter((r) => r.status === "done").length, total: currentVisits.length,
    overdue, moved, late, added, cancelled, pendingAnchors,
    stopped: stoppedBy.map((g) => ({ label: g.label, count: g.visits.length, date: g.date })),
    warnings: current.warnings || [],
    pendingDecisions: drafts.filter((d) => d.kind === "decision" && !d.decision?.answer).length,
    ended: current.ended ? { label: endedLabel!, date: current.ended.date } : null,
  };
}
