// @ts-nocheck
// Mechanical TypeScript port of the Protocol Visit Scheduler kit (src/engine/describe.js). The logic is
// deliberately unchanged so results match the kit; typed entry points are in ./index.ts.
// Display helpers, participant summaries and a JSON-safe schedule serializer.
// These are the non-visual parts of the app's screens, so any UI (React, Vue,
// mobile, server-rendered) can show the same words the app showed.

import { addD, isoD, sOff, todayUTC, arr, toMin, fmtMin, fmtOff } from "./utils";
import { computeSchedule } from "./schedule";

const allQuestions = (model) => model.questions.concat(...model.events.map((e) => e.questions));
export const labelOf = (model, anchorId) => model.anchors.find((a) => a.id === anchorId)?.label || anchorId;

/** An answer value as people read it: "yes" -> "Yes", "A" -> option label when short. */
export function valText(model, qid, v) {
  const q = allQuestions(model).find((x) => x.id === qid);
  if (q?.kind === "boolean" || /^(yes|no)$/i.test(String(v))) return String(v).toLowerCase() === "yes" ? "Yes" : String(v).toLowerCase() === "no" ? "No" : String(v);
  const o = q?.options?.find((o) => o.value === String(v));
  return o && o.label.length <= 24 ? o.label : String(v);
}

/** [{question:"arm", in:["A"]}] -> "Arm = A" */
export function condText(model, conds) {
  return arr(conds).map((c) => `${allQuestions(model).find((q) => q.id === c.question)?.label || c.question} = ${arr(c.in).map((v) => valText(model, c.question, v)).join(" or ")}`).join(" and ");
}

/** A visit definition's timing rule in words, e.g. "C1D1 + 7 d" or "Day 1, then every 14 d after the previous one × 4". */
export function timingText(model, v) {
  let s = v.follows
    ? `${v.follows_occurrence === "last" ? "Last " : ""}${model.visits.find((x) => x.id === v.follows)?.code || v.follows}${sOff(v.offset_days)}`
    : `${labelOf(model, v.anchor)}${sOff(v.offset_days)}`;
  const r = v.repeat;
  if (r) {
    s += `, then every ${r.every_days} d${r.chain ? " after the previous one" : ""}`;
    const cq = r.count_question && model.questions.find((q) => q.id === r.count_question);
    s += r.open_ended ? ` (shows ${r.count || 1} ahead)` : r.count_question ? ` (count from “${cq?.label || r.count_question}”)` : ` × ${r.count || 1}`;
    if (r.until_anchor) s += `, until ${labelOf(model, r.until_anchor)}`;
  }
  if (v.duration_days) s += ` · lasts ${v.duration_days} d`;
  return s;
}

/** Phase -> colour family used by the app's timeline (screening, treatment, surgery, follow-up, other). */
export function phaseGroup(ph) {
  const p = String(ph).toLowerCase();
  if (p.includes("screen") || p.includes("baseline") || p.includes("random") || p.includes("run-in") || p.includes("lead")) return "screening";
  if (p.includes("surg") || p.includes("operat") || p.includes("procedure") || p.includes("infusion") || p.includes("implant") || p.includes("event")) return "procedure";
  if (p.includes("follow") || p.includes("end") || p.includes("survival") || p.includes("eos") || p.includes("final") || p.includes("extension")) return "follow-up";
  if (p.includes("treat") || p.includes("dos") || p.includes("cycle") || p.includes("induction") || p.includes("maint") || p.includes("observ") || p.includes("period")) return "treatment";
  return "other";
}

/**
 * What the participants list shows per person: next visit + what is needed.
 * needs = decisions due within 14 days, missing event dates, or "Ended".
 */
export function participantSummary(model, site, part, today = todayUTC()) {
  const c = computeSchedule(model, site, part);
  const next = c.rows.find((r) => r.type === "visit" && r.actual == null && (r.t == null || r.hi >= today)) || null;
  let needs = c.rows.filter((r) => r.type === "decision" && r.answer == null && r.t != null && r.t <= addD(today, 14)).map((r) => r.q.label);
  for (const a of model.anchors) {
    if (a.kind === "ask" && c.A[a.id] == null && c.rows.some((r) => r.type === "visit" && r.how === "pending" && r.v.anchor === a.id && !r.untilPending)) needs.push(a.label + " date");
  }
  if (c.endEv) needs = ["Ended"];
  return {
    next: next ? { key: next.key, code: next.code, name: next.name, date: next.t != null ? isoD(next.t) : null, rel: next.t == null ? next.rel : null } : null,
    needs,
  };
}

/** Does recording this visit move other visits? (shown as a hint under "Record") */
export function recordingMovesOthers(model, v) {
  return !!(v.repeat?.chain || model.visits.some((x) => x.follows === v.id) || model.anchors.some((a) => a.set_by_visit === v.id));
}

/** Day plan: each time point as clock time + allowed window once the dose time is known. */
export function dayPlan(v, doseTime) {
  const dm = toMin(doseTime);
  return v.timepoints.map((tp) => ({
    label: tp.label,
    offsetMin: tp.offset_min,
    at: dm != null ? fmtMin(dm + tp.offset_min) : fmtOff(tp.offset_min),
    window: tp.window_before_min || tp.window_after_min
      ? dm != null ? `${fmtMin(dm + tp.offset_min - tp.window_before_min)} – ${fmtMin(dm + tp.offset_min + tp.window_after_min)}` : `−${tp.window_before_min} / +${tp.window_after_min} min`
      : "exact",
    procedures: tp.procedures.map((p) => p.name),
  }));
}

/**
 * computeSchedule() result -> plain JSON for an API response or a mobile client.
 * Every date is "YYYY-MM-DD" or null.
 */
export function serializeSchedule(model, calc, part = {}) {
  const d = (t) => (t == null ? null : isoD(t));
  const rows = calc.rows.map((r) => {
    if (r.type === "decision") {
      const q = r.q;
      return {
        type: "decision", key: r.key, questionId: q.id, text: q.text, help: q.help || null, at: r.at, date: d(r.t),
        options: q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options,
        answer: r.answer ?? null,
      };
    }
    const status = r.actual != null ? "done" : r.how === "pending" ? "waiting" : "scheduled";
    return {
      type: "visit", key: r.key, visitId: r.v.id, code: r.code, name: r.name, phase: r.v.phase, phaseGroup: phaseGroup(r.v.phase),
      modality: r.v.modality, status,
      date: d(r.actual ?? r.t), planned: d(r.t), actual: d(r.actual), relative: r.t == null ? r.rel : null, rule: r.rel,
      windowStart: d(r.lo), windowEnd: d(r.hi), windowDays: [r.v.window_before, r.v.window_after],
      studyDay: r.sd ?? null, endDay: d(r.endDay),
      weekend: !!r.weekend, nearestWeekday: d(r.alt),
      dependsOnDecision: r.cond === "unknown", fromEvent: r.fromEvent || null,
      openEnded: !!r.openEnded, until: r.untilPending && r.first ? r.untilLabel : null,
      procedures: r.procs.map((p) => ({ name: p.name, conditional: p.st === "unknown", condition: p.if?.length ? condText(model, p.if) : null })),
      notes: [r.first ? r.v.condition : null, r.occNote].filter(Boolean),
      timepoints: r.v.timepoints.length ? dayPlan(r.v, part.doseTimes?.[r.key]) : [],
      movesOthers: recordingMovesOthers(model, r.v),
      source: r.v.source || null,
    };
  });
  return {
    anchors: model.anchors.map((a) => ({ id: a.id, label: a.label, kind: a.kind, date: d(calc.A[a.id]), how: calc.A[a.id] != null ? calc.how[a.id] || "derived" : "pending", setByEvent: model.events.find((e) => e.sets_anchor === a.id)?.id || null })),
    rows,
    skipped: calc.skipped.map((v) => ({ id: v.id, code: v.code, name: v.name, onlyIf: v.include_if.length ? condText(model, v.include_if) : null })),
    stopped: calc.cut.map((x) => ({ visit: x.v.id, count: x.n, at: labelOf(model, x.anchor), date: d(calc.A[x.anchor]) })),
    ended: calc.endEv ? { event: calc.endEv.type, date: d(calc.endAt), cancelled: calc.cancelled } : null,
  };
}
