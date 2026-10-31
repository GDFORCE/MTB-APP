// @ts-nocheck
// Mechanical TypeScript port of the Protocol Visit Scheduler kit (src/engine/describe.js). The logic is
// deliberately unchanged so results match the kit; typed entry points are in ./index.ts.
// Display helpers, participant summaries and a JSON-safe schedule serializer.
// These are the non-visual parts of the app's screens, so any UI (React, Vue,
// mobile, server-rendered) can show the same words the app showed.

import { addD, isoD, sOff, todayUTC, arr, toMin, fmtMin, fmtOff, fmtS, DAY } from "./utils";
import { computeSchedule, limitText, actionText } from "./schedule";

const allQuestions = (model) => model.questions.concat(...model.events.map((e) => e.questions));
export const labelOf = (model, anchorId) => model.anchors.find((a) => a.id === anchorId)?.label || anchorId;

/** An answer value as people read it: "yes" -> "Yes", "A" -> option label when short. */
export function valText(model, qid, v) {
  const q = allQuestions(model).find((x) => x.id === qid);
  if (q?.kind === "boolean" || /^(yes|no)$/i.test(String(v))) return String(v).toLowerCase() === "yes" ? "Yes" : String(v).toLowerCase() === "no" ? "No" : String(v);
  const o = q?.options?.find((o) => o.value === String(v));
  return o && o.label.length <= 24 ? o.label : String(v);
}

const OPS = { "=": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥" };
/** One condition in words: "Arm = A", "Age ≥ 65", "(Arm = A or Prior therapy = Yes)", "not Cohort = 2". */
export function condPart(model, c) {
  if (!c || typeof c !== "object") return "";
  if (Array.isArray(c.any)) return `(${c.any.map((x) => condPart(model, x)).filter(Boolean).join(" or ")})`;
  if (c.not) return `not ${condPart(model, c.not)}`;
  if (c.event != null) {
    const before = c.before?.anchor ? ` before ${labelOf(model, c.before.anchor)}`
      : c.before?.visit ? ` before ${model.visits.find((v) => v.id === c.before.visit)?.code || c.before.visit}${c.before.n != null ? ` #${c.before.n}` : ""}` : "";
    return `${model.events.find((e) => e.id === c.event)?.label || c.event} ${c.happened === false ? "not logged" : "logged"}${before}`;
  }
  if (c.visit != null) {
    const code = model.visits.find((v) => v.id === c.visit)?.code || c.visit;
    return `${code}${c.n != null ? ` #${c.n}` : ""} ${c.status === "missed" ? "missed" : c.status === "not_done" ? "not done" : "done"}`;
  }
  const label = allQuestions(model).find((q) => q.id === c.question)?.label || c.question;
  if (c.op && c.op !== "in") return `${label} ${OPS[c.op] || c.op} ${valText(model, c.question, c.value)}`;
  return `${label} = ${arr(c.in).map((v) => valText(model, c.question, v)).join(" or ")}`;
}
/** [{question:"arm", in:["A"]}] -> "Arm = A" */
export function condText(model, conds) {
  return arr(conds).map((c) => condPart(model, c)).filter(Boolean).join(" and ");
}

/** A rule's conditions in words: "if Arm = A, before Cycle 3 and Grade = 3". "" when it has none. */
export function ruleIfText(model, r) {
  const visitCode = (id) => model.visits.find((v) => v.id === id)?.code || id;
  const ordinal = (n) => (n === 1 ? "first" : n === 2 ? "second" : n === 3 ? "third" : `${n}th`);
  const parts = arr(r?.if).map((c) => {
    if (c.occurrence != null) return `the ${ordinal(+c.occurrence)} time`;
    if (c.event_question != null) return condText(model, [{ question: c.event_question, in: c.in }]);
    if (c.timing) {
      const t = c.timing, what = t.anchor ? labelOf(model, t.anchor) : `${visitCode(t.visit)}${t.n != null ? ` #${t.n}` : ""}`;
      if (t.max_days != null && t.min_days == null) return t.max_days < 0 ? `before ${what}${t.max_days < -1 ? ` by ${-t.max_days}+ days` : ""}` : `up to ${t.max_days} days after ${what}`;
      if (t.min_days != null && t.max_days == null) return t.min_days <= 0 ? `on or after ${what}` : `${t.min_days}+ days after ${what}`;
      return `${t.min_days} to ${t.max_days} days after ${what}`;
    }
    if (c.question != null || c.any || c.not) return condPart(model, c);
    return "";
  }).filter(Boolean);
  return parts.length ? `If ${parts.join(" and ")}` : "";
}

/** A visit definition's timing rule in words, e.g. "C1D1 + 7 d" or "Day 1, then every 14 d after the previous one × 4". */
export function timingText(model, v) {
  let s = v.follows
    ? `${v.follows_occurrence === "last" ? "Last " : ""}${model.visits.find((x) => x.id === v.follows)?.code || v.follows}${sOff(v.offset_days)}`
    : `${labelOf(model, v.anchor)}${sOff(v.offset_days)}`;
  const r = v.repeat;
  if (r) {
    const stages = arr(r.stages).filter((st) => st && st.every_days);
    s += stages.length
      ? `, then ${stages.map((st) => `every ${st.every_days} d${st.until_day != null ? ` until day ${st.until_day}` : ""}`).join(", then ")}${r.chain ? " (after the previous one)" : ""}`
      : `, then every ${r.every_days} d${r.chain ? " after the previous one" : ""}`;
    const cq = r.count_question && model.questions.find((q) => q.id === r.count_question);
    s += r.open_ended ? ` (shows ${r.count || 6} ahead)` : r.count_question ? ` (count from “${cq?.label || r.count_question}”)`
      : r.count ? ` × ${r.count}` : r.max_days != null || r.until_anchor || (r.until_check && v.check) ? "" : " (number of times not stated)";
    if (r.max_days != null) s += `, for up to ${r.max_days} d`;
    if (r.until_anchor) s += `, until ${labelOf(model, r.until_anchor)}`;
    if (r.until_check && v.check) s += `, until “${v.check.text}” is met`;
    if (r.max_delay_days != null) s += ` · may be delayed up to ${r.max_delay_days} d`;
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
  const next = c.rows.find((r) => r.type === "visit" && r.actual == null && r.check?.state !== "skipped" && (r.t == null || r.hi >= today)) || null;
  let needs = c.rows.filter((r) => r.type === "decision" && r.answer == null && r.t != null && r.t <= addD(today, 14)).map((r) => r.q.label);
  // Checks due within 14 days, and held visits waiting for their next check.
  for (const r of c.rows) {
    if (r.type !== "visit" || !r.check || r.actual != null) continue;
    if ((r.check.state === "ask" && r.t != null && r.t <= addD(today, 14)) || r.check.state === "held") needs.push(`${r.code} check`);
  }
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

/** Day plan: each time point as clock time + allowed window once the dose time is known. A time point
 *  on a later day ("24 h post-dose") also gets that day's date when the visit's date is known. */
export function dayPlan(v, doseTime, day = null) {
  const dm = toMin(doseTime);
  return v.timepoints.map((tp) => ({
    ...(dm != null && day != null && Math.floor((dm + tp.offset_min) / 1440) !== 0
      ? { date: isoD(addD(day, Math.floor((dm + tp.offset_min) / 1440))) } : {}),
    label: tp.label,
    offsetMin: tp.offset_min,
    at: dm != null ? fmtMin(dm + tp.offset_min) : fmtOff(tp.offset_min),
    window: tp.window_before_min || tp.window_after_min
      ? dm != null ? `${fmtMin(dm + tp.offset_min - tp.window_before_min)} – ${fmtMin(dm + tp.offset_min + tp.window_after_min)}` : `−${tp.window_before_min} / +${tp.window_after_min} min`
      : "exact",
    procedures: tp.procedures.map((p) => p.name),
  }));
}

/** Preparation for a visit in words: "Fasting 8 h before · Hold metformin". */
export function prepText(prep) {
  if (!prep) return null;
  const parts = [];
  if (prep.fasting_hours) parts.push(`Fasting ${prep.fasting_hours} h before`);
  for (const x of arr(prep.notes)) if (x) parts.push(String(x));
  return parts.length ? parts.join(" · ") : null;
}

/** What a visit's yes/no check did, in words, or null when it changed nothing yet. */
function checkNote(r) {
  const c = r.check;
  if (!c || r.actual != null) return null;
  const lastNo = [...c.given].reverse().find((x) => x.result === "no");
  if (c.state === "held") {
    return `Held: ${lastNo.failed?.length ? `${lastNo.failed.join(", ")} not met` : `“${c.def.text}” was No`} on ${fmtS(Date.parse(lastNo.date + "T00:00:00Z"))}.`
      + (c.next != null ? ` Next check ${fmtS(c.next)}.` : "")
      + (c.deadline != null ? ` Needs a Yes by ${fmtS(c.deadline)}.` : "");
  }
  if (c.state === "resumed" && c.delay > 0) return `Delayed ${c.delay} day${c.delay === 1 ? "" : "s"}: “${c.def.text}” was No, then Yes.`;
  if (c.state === "skipped") return `Skipped on ${fmtS(Date.parse(c.skip.date + "T00:00:00Z"))}${c.skip.why ? ` (${c.skip.why})` : ""}: not made up; later dates stay.`;
  if (c.state === "again") return `“${c.def.text}” not met yet: it comes back at the next one.`;
  if (c.state === "met") return `“${c.def.text}” met: this is the last one.`;
  return null;
}

/** "Give 50% of the dose this time (ANC 1000-1499)." */
function reduceNote(r) {
  const c = r.check?.reduce;
  return c ? `This time only: ${c.percent}% of the dose (${c.why}). Later doses keep their level.` : null;
}

/** 1768249800000 -> "20:30" (UTC, as every date in the engine). */
const hm = (t) => { const m = Math.round((((t % DAY) + DAY) % DAY) / 6e4); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };

/** "Check in at least 12 h before …" on the check-in visit, "Dose from 08:30, 13 Jan …" on the dose visit. */
function timeNotes(r) {
  const out = [];
  if (r.arrive && r.arrive.checkedIn == null && r.actual == null)
    out.push(`Record the check-in time: the ${r.arrive.doseCode} dose must be at least ${r.arrive.hours} h after it.`);
  if (r.doseFrom) out.push(r.doseFrom.from != null
    ? `Dose from ${hm(r.doseFrom.from)}, ${fmtS(r.doseFrom.from)} (${r.doseFrom.hours} h after the ${r.doseFrom.checkInCode} check-in).`
    : `Dose at least ${r.doseFrom.hours} h after the ${r.doseFrom.checkInCode} check-in.`);
  return out;
}

/** "Drug X: 75 mg (reduced: worst ANC < 0.5)" for each dose whose level changed at this visit. */
function doseNotes(r) {
  return arr(r.doses).filter((x) => x.changed).map((x) => {
    const lv = x.idx != null ? x.levels[x.idx]?.label : null;
    return `${x.label}: ${lv || "level not stated"} (${x.manual ? "set by staff" : `changed: ${x.changed}`}).`;
  });
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
        type: "decision", key: r.key, questionId: q.id, kind: q.kind, text: q.text, help: q.help || null, at: r.at, date: d(r.t),
        options: q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options,
        answer: r.answer ?? null,
      };
    }
    const status = r.actual != null ? "done" : r.check?.state === "skipped" ? "skipped" : r.how === "pending" ? "waiting" : "scheduled";
    return {
      type: "visit", key: r.key, visitId: r.v.id, code: r.code, name: r.name, phase: r.v.phase, phaseGroup: phaseGroup(r.v.phase),
      modality: r.v.modality, status,
      date: d(r.actual ?? r.t), planned: d(r.t), actual: d(r.actual), relative: r.t == null ? r.rel : null, rule: r.rel,
      windowStart: d(r.lo), windowEnd: d(r.hi), windowDays: [r.v.window_before, r.v.window_after],
      studyDay: r.sd ?? null, endDay: d(r.endDay),
      weekend: !!r.weekend, nearestWeekday: d(r.alt),
      dependsOnDecision: r.cond === "unknown", fromEvent: r.fromEvent || null,
      openEnded: !!r.openEnded, until: r.untilPending && r.first ? r.untilLabel : null,
      procedures: r.procs.map((p) => ({
        name: p.name, conditional: p.st === "unknown", condition: p.if?.length ? condText(model, p.if) : null,
        need: p.need || "required", reuseWithinDays: p.reuse_within_days ?? null,
        // "Within 3 days before": from which date it may be done, once the visit's date is known.
        ...(p.within_before ? { withinBefore: { ...p.within_before,
          from: (r.actual ?? r.t) != null ? d(addD(r.actual ?? r.t, -Math.ceil(p.within_before.hours != null ? p.within_before.hours / 24 : p.within_before.days))) : null } } : {}),
      })),
      notes: [
        r.first ? r.v.condition : null, r.occNote, r.first ? prepText(r.v.prep) : null,
        r.countUnknown ? "The protocol doesn't say how many times this repeats; the next ones are shown." : null,
        r.pausedBy ? `Paused: ${r.pausedBy}.` : null,
        checkNote(r),
        reduceNote(r),
        r.asap ? `Planned ${fmtS(r.asap.was)}, which had passed when participation ended (${fmtS(r.asap.from)}): due as soon as possible.` : null,
        ...doseNotes(r),
        ...timeNotes(r),
      ].filter(Boolean),
      // A time staff record for this visit: the check-in time, or the dose time (PK clock times).
      time: part.doseTimes?.[r.key] ?? null,
      timeKind: r.arrive ? "checkin" : r.doseFrom || r.v.timepoints.length ? "dose" : null,
      check: r.check ? {
        id: r.check.def.id, text: r.check.def.text, help: r.check.def.help || null, state: r.check.state,
        answers: r.check.given.map((x) => ({
          date: x.date, ...(x.answer ? { answer: x.answer } : {}), ...(x.values ? { values: x.values } : {}),
          result: x.result, by: x.by, failed: x.failed,
          // With a dosing table: which row decided it.
          ...(arr(r.check.def.actions).length ? {
            outcome: x.action ? x.action.do : x.result === "yes" ? "give" : x.result === "no" ? "hold" : x.result === "skip" ? "skip" : null,
            outcomeText: x.action ? actionText(model, x.action) : null,
          } : {}),
        })),
        actions: arr(r.check.def.actions).map((a) => ({ do: a.do, percent: a.percent ?? null, text: actionText(model, a) })),
        until: !!r.check.until, skip: r.check.skip || null, reduce: r.check.reduce || null,
        nextCheck: d(r.check.next), deadline: d(r.check.deadline), timedOut: r.check.timedOut, delayDays: r.check.delay,
        source: r.check.def.source || null,
        measures: arr(r.check.def.measures).map((id) => arr(model.measures).find((m) => m.id === id)).filter(Boolean),
        limits: arr(r.check.def.limits).map((l) => limitText(model, l)),
        needsBaseline: r.check.needsBaseline || [],
      } : null,
      doses: arr(r.doses).map((x) => ({
        dose: x.dose, label: x.label, level: x.idx != null ? x.levels[x.idx].id : null,
        // This dose only, from the check's dosing table ("50% this time").
        percent: r.check?.reduce && (r.check.reduce.dose == null || r.check.reduce.dose === x.dose) ? r.check.reduce.percent : null,
        levelLabel: x.idx != null ? x.levels[x.idx].label : null, levelIndex: x.idx, changed: x.changed, manual: x.manual,
        reduced: x.idx != null && x.startIdx != null && x.idx > x.startIdx,
        levels: x.levels.map((l) => ({ id: l.id, label: l.label })),
      })),
      timepoints: r.v.timepoints.length ? dayPlan(r.v, part.doseTimes?.[r.key], r.actual ?? r.t) : [],
      movesOthers: recordingMovesOthers(model, r.v),
      source: r.v.source || null,
      stopsIf: r.stopsIf || [],
    };
  });
  const groups = new Map();
  for (const s of calc.stopped || []) {
    if (!groups.has(s.rule.id)) groups.set(s.rule.id, { rule: s.rule.id, label: s.rule.label, effect: s.rule.effect || null, date: d(s.at), visits: [] });
    groups.get(s.rule.id).visits.push({ key: s.key, code: s.code, date: d(s.t) });
  }
  return {
    anchors: model.anchors.map((a) => ({ id: a.id, label: a.label, kind: a.kind, date: d(calc.A[a.id]), how: calc.A[a.id] != null ? calc.how[a.id] || "derived" : "pending", setByEvent: model.events.find((e) => e.sets_anchor === a.id)?.id || null })),
    rows,
    skipped: calc.skipped.map((v) => ({ id: v.id, code: v.code, name: v.name, onlyIf: v.include_if.length ? condText(model, v.include_if) : null })),
    stopped: calc.cut.map((x) => ({ visit: x.v.id, count: x.n, at: labelOf(model, x.anchor), date: d(calc.A[x.anchor]) })),
    ended: calc.endEv ? { event: calc.endEv.type, date: d(calc.endAt), cancelled: calc.cancelled } : null,
    stoppedBy: [...groups.values()],
    warnings: (calc.warnings || []).map((w) => w.text),
    issues: calc.warnings || [],
    // When each rule may happen, as dates, for the screens that ask for a rule's date.
    ruleWindows: arr(model.rules).filter((r) => r.allowed).map((r) => {
      const al = r.allowed, from = al.relative_to ? calc.A[al.relative_to] : null;
      const nv = al.not_on_visit ? calc.rows.find((x) => x.type === "visit" && x.v.id === al.not_on_visit) : null;
      return {
        rule: r.id, label: r.label, anchor: r.when?.anchor || null, event: r.when?.event || null,
        from: from != null && al.min_days != null ? d(addD(from, al.min_days)) : null,
        to: from != null && al.max_days != null ? d(addD(from, al.max_days)) : null,
        notOn: nv ? { code: nv.code, date: d(nv.actual ?? nv.t) } : null,
      };
    }),
  };
}
