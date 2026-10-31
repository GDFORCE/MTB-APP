// @ts-nocheck
// Mechanical TypeScript port of the Protocol Visit Scheduler kit (src/engine/schedule.js). The logic is
// deliberately unchanged so results match the kit; typed entry points are in ./index.ts.
// STEP 7 — The scheduling engine. Pure functions, no I/O, no AI.
//
// computeSchedule(model, siteAnswers, participant) -> {
//   rows,       // visits + decision points, sorted by date (see below)
//   skipped,    // visit definitions not applicable for this participant
//   cut,        // repeats stopped at their end anchor  [{ v, n, anchor }]
//   cancelled,  // number of visits cancelled by an ending event
//   A, how,     // anchor dates (timestamps) and how each was obtained
//   E,          // anchor dates incl. planning estimates (used only for ordering)
//   endAt, endEv // the ending event, if any
//   stopped,    // visits a rule cancelled  [{ rule, at, key, code, t }]
//   warnings,   // rule checks that failed  [{ rule, text }]
// }
//
// participant = {
//   startAnchor: "screening", startDate: "2026-10-22",
//   answers:  { arm: "A", wocbp: "yes", "@surgery": "2027-01-28", runin_ok: "yes" },
//   events:   [{ key, type: "pd", date: "2027-02-15", answers: { pd_conf: "yes" } }],
//   actuals:  { "neo#2": "2026-11-28" },     // visitId#occurrence -> actual date
//   doseTimes:{ "c1d1#1": "09:15" },
//   checks:   { "c1d1#3": [{ date: "2027-03-01", answer: "no" }, { date: "2027-03-08", answer: "yes" }] }
// }
//
// A visit with a `check` (a yes/no question such as "ANC ≥ 1.0 × 10⁹/L?") asks it at every
// occurrence. No answer: nothing changes. "No": the occurrence is held (no date) until a "Yes",
// which becomes its date; chained repeats move with it. No "Yes" within check.max_wait_days of
// the planned date: a warning, and with on_timeout "stop" this visit (and check.stop_visits) stop
// from that deadline. Answers are never guessed.
//
// A check with `limits` takes values (participant.checks entries with `values`, model.measures)
// and works the Yes/No out from them: a number, a multiple of the participant's baseline
// (participant.baselines), the worst value for this visit or so far. When a limit can't be worked
// out (no baseline yet), staff answer Yes/No by hand. Dose levels (model.doses) run through each
// drug's doses in date order: a passed check's dose_changes, a rule's dose_changes (doses after its
// date) and levels staff set (participant.doseLevels) change them; going below the lowest level
// stops the drug's visits only when the protocol says so (below_lowest "stop" or a change's stop).
//
// A check may carry `actions`, a dosing table read top to bottom: the first row whose limits hold
// decides what happens at this occurrence: "give", "reduce" (percent of the dose, this time only),
// "skip" (not given and not made up: the next dates stay) or "hold" (waits as for a No). A repeat with
// `until_check` comes back every interval until its check is met ("CrCl weekly until >= 50"); a No
// there never holds. A dose level may list `drop_visits` (at that level those visits are not given).
// A limit with `for_days` holds only when every value entered over at least that many days met it.
// A visit with `asap_if_past` whose date had passed when participation ended is due on that day.
//
// A visit row: { type:"visit", key, code, name, v (definition), t (planned ts or null),
//   actual, lo, hi (window), rel ("Surgery + 30 d" when t is null), how
//   ("entered"|"derived"|"done"|"pending"), cond ("yes"|"unknown"), procs [{name, if, st}],
//   occNote, first, sd (study day), weekend, alt (nearest weekday in window), endDay,
//   fromEvent, openEnded, untilPending, untilLabel }
// A decision row: { type:"decision", key, q, t, tE, at, answer }
// The code below is copied verbatim from the working app.

import { DAY, parseD, addD, dow, fmtS, arr, num, sOff, todayUTC, toMin } from "./utils";

export function makeAns(site, answers) { return (q) => { const a = answers?.[q]; return a === undefined || a === null || a === "" ? site?.[q] : a; }; }
// One condition: {question, in} (answer is one of), {question, op, value} (number or text
// comparison), {any: [...]} (at least one holds), {not: condition}, or what has happened:
// {event, happened} (an event logged on or before the date being checked) and {visit, n, status}
// ("done": its date is recorded; "missed": its window closed without one; "not_done").
// "unknown" while an answer it needs is not given; "not unknown" stays unknown. `facts` answers
// the what-happened conditions; without it they are "unknown" (or "yes" when facts === "ignore",
// used to decide whether a visit can apply at all before each occurrence is checked).
export function condOne(c, ans, facts) {
  if (!c || typeof c !== "object") return "yes";
  if (Array.isArray(c.any)) {
    const st = c.any.map((x) => condOne(x, ans, facts));
    return st.includes("yes") ? "yes" : st.includes("unknown") ? "unknown" : st.length ? "no" : "yes";
  }
  if (c.not) { const st = condOne(c.not, ans, facts); return st === "yes" ? "no" : st === "no" ? "yes" : "unknown"; }
  if (c.event != null || c.visit != null) {
    if (facts === "ignore") return "yes";
    if (!facts) return "unknown";
    if (c.event != null) {
      // "before Week 12": logged before that visit's day (planned, or recorded) or that date.
      let before = null;
      if (c.before) { before = facts.dateOf ? facts.dateOf(c.before) : null; if (before == null) return "unknown"; }
      return facts.eventBy(c.event, before) === (c.happened !== false) ? "yes" : "no";
    }
    const st = facts.visitStatus(c.visit, c.n ?? null);
    return st === (c.status || "done") || (c.status === "not_done" && st !== "done") ? "yes" : "no";
  }
  const a = ans(c.question);
  if (a === undefined || a === null || a === "") return "unknown";
  const op = c.op && c.op !== "in" ? c.op : null;
  if (op) {
    const x = Number(a), y = Number(c.value);
    if (op === "=" || op === "!=") {
      const same = Number.isFinite(x) && Number.isFinite(y) ? x === y : String(a).toLowerCase() === String(c.value).toLowerCase();
      return (op === "=") === same ? "yes" : "no";
    }
    if (!Number.isFinite(x) || !Number.isFinite(y)) return "unknown";
    const ok = op === "<" ? x < y : op === "<=" ? x <= y : op === ">" ? x > y : op === ">=" ? x >= y : null;
    return ok == null ? "unknown" : ok ? "yes" : "no";
  }
  const allowed = arr(Array.isArray(c.in) ? c.in : [c.in]).map((x) => String(x).toLowerCase());
  const norm = String(a).toLowerCase();
  const alias = norm === "yes" ? ["yes", "true", "y"] : norm === "no" ? ["no", "false", "n"] : [norm];
  return alias.some((x) => allowed.includes(x)) ? "yes" : "no";
}
export function condState(conds, ans, facts) {
  if (!Array.isArray(conds) || !conds.length) return "yes";
  let unknown = false;
  for (const c of conds) {
    const st = condOne(c, ans, facts);
    if (st === "no") return "no";
    if (st === "unknown") unknown = true;
  }
  return unknown ? "unknown" : "yes";
}
// Which occurrences a repeat override applies to: listed numbers, or every n-th from a number
// ("CT every 3rd cycle from Cycle 3" = every 3, from 3), optionally up to a number.
const overrideHits = (o, n) => (Array.isArray(o.n) && o.n.includes(n))
  || (o.every > 0 && n >= (o.from ?? 1) && (o.to == null || n <= o.to) && (n - (o.from ?? 1)) % o.every === 0);
// The interval after an occurrence that falls `at` days after the repeat's first occurrence.
// Stages change it over time ("every 8 weeks for the first year, then every 12 weeks"): until_day
// counts from that first occurrence; the last stage (no until_day) runs on.
export function everyAt(r, at) {
  for (const st of arr(r?.stages)) if (st && (st.until_day == null || at < st.until_day)) return num(st.every_days);
  return num(r?.every_days);
}
// Does a condition list depend on what has happened (an event logged, a visit done or missed)?
export const hasFacts = (conds) => arr(conds).some((c) => c && typeof c === "object"
  && (c.event != null || c.visit != null || (Array.isArray(c.any) && hasFacts(c.any)) || (c.not && hasFacts([c.not]))));
export function visitProcs(v, num, ans, facts) {
  let procs = v.procedures.slice(), occNote = null;
  for (const o of arr(v.repeat?.overrides)) if (overrideHits(o, num)) {
    procs = procs.filter((p) => !o.remove.includes(p.name));
    for (const p of o.add) if (!procs.some((x) => x.name === p.name)) procs.push(p);
    if (o.note) occNote = o.note;
  }
  return { procs: procs.map((p) => ({ ...p, st: condState(p.if, ans, facts) })).filter((p) => p.st !== "no"), occNote };
}
const OP_TEXT = { "<": "<", "<=": "≤", ">": ">", ">=": "≥" };
const compare = (op, x, y) => (op === "<" ? x < y : op === "<=" ? x <= y : op === ">" ? x > y : x >= y);
/** A check limit in words: "ANC ≥ 1.0 × 10^9/L", "Creatinine ≤ 1.5 × baseline", "worst Neuropathy grade ≥ 3". */
export function limitText(model, l) {
  if (!l || typeof l !== "object") return "";
  if (Array.isArray(l.any)) return l.any.map((x) => limitText(model, x)).join(" or ");
  if (l.held_days) return `waited ${OP_TEXT[l.held_days.op] || l.held_days.op} ${l.held_days.value} days`;
  const m = arr(model.measures).find((x) => x.id === l.measure) || { label: l.measure, unit: null, kind: "value" };
  const of = l.of === "worst_here" ? "worst " : l.of === "worst_so_far" ? "worst so far " : "";
  const what = m.kind === "grade" ? `${m.label} grade` : m.label;
  const ref = l.baseline_factor != null ? `${l.baseline_factor} × baseline` : `${l.value}${m.unit ? ` ${m.unit}` : ""}`;
  return `${of}${what} ${OP_TEXT[l.op] || l.op} ${ref}${l.for_days ? ` for ${l.for_days}+ days` : ""}`;
}
const DO_TEXT = { give: "give", skip: "skip (not made up)", hold: "hold until it recovers" };
/** One row of a check's dosing table in words: "ANC < 1000 → skip (not made up)". */
export function actionText(model, a) {
  const what = a.do === "reduce" ? `give ${a.percent}% of the dose this time` : DO_TEXT[a.do] || a.do;
  return `${arr(a.if).map((l) => limitText(model, l)).join(" and ")} → ${what}`;
}
export function computeSchedule(model, site, part) {
  part = part || {};
  const answers = part.answers || {}, actuals = part.actuals || {}, logged = arr(part.events).filter((e) => e && e.date);
  const checks = part.checks || {};
  const measures = Object.fromEntries(arr(model.measures).map((m) => [m.id, m]));
  const baselines = part.baselines || {}, doseLevels = part.doseLevels || {};
  // Every value entered at any check, for "worst so far".
  const allEntries = Object.values(checks).flatMap((list) => arr(list)).filter((x) => x && x.date && x.values);
  const worst = (mid, list) => {
    const m = measures[mid];
    let w = null;
    for (const x of list) {
      const v = x.values?.[mid];
      if (v == null || v === "" || !Number.isFinite(+v)) continue;
      w = w == null ? +v : m?.worse === "lower" ? Math.min(w, +v) : Math.max(w, +v);
    }
    return w;
  };
  // One limit against the entered values: "yes", "no" or "unknown" (a value or baseline missing).
  // ctx = { entry, here (this visit's entries up to entry), held (days waited, for dose changes) }.
  const limitState = (l, ctx) => {
    if (Array.isArray(l.any)) {
      const st = l.any.map((x) => limitState(x, ctx));
      return st.includes("yes") ? "yes" : st.includes("unknown") ? "unknown" : "no";
    }
    if (l.held_days) return ctx.held == null ? "unknown" : compare(l.held_days.op, ctx.held, num(l.held_days.value)) ? "yes" : "no";
    const m = measures[l.measure];
    if (!m) return "unknown";
    const x = l.of === "worst_here" ? worst(l.measure, ctx.here)
      : l.of === "worst_so_far" ? worst(l.measure, allEntries.filter((e) => e.date <= ctx.entry.date))
      : ctx.entry?.values?.[l.measure];
    if (x == null || x === "" || !Number.isFinite(+x)) return "unknown";
    let y = num(l.value);
    if (l.baseline_factor != null) {
      const b = baselines[l.measure];
      if (b == null || b === "" || !Number.isFinite(+b)) return "unknown";
      y = +b * num(l.baseline_factor);
    }
    if (!l.for_days) return compare(l.op, +x, y) ? "yes" : "no";
    // "Lasting 7 days or more": an unbroken run of this visit's entered values (during the hold too,
    // up to this one) that met it and spans at least that many days. Counted from entered values only.
    const list = arr(ctx.here || [ctx.entry]).filter((e) => e.values?.[l.measure] != null && Number.isFinite(+e.values[l.measure]));
    let from = null;
    for (const e of list) {
      if (!compare(l.op, +e.values[l.measure], y)) { from = null; continue; }
      if (from == null) from = e.date;
      if (Math.round((parseD(e.date) - parseD(from)) / DAY) >= num(l.for_days)) return "yes";
    }
    return "no";
  };
  const limitsState = (list, ctx) => {
    const st = list.map((l) => limitState(l, ctx));
    return st.includes("no") ? "no" : st.includes("unknown") ? "unknown" : "yes";
  };
  // A check's answers for one occurrence, each with the outcome used: "yes" (goes ahead), "no" (held,
  // or "not yet" for a repeat-until check) or "skip" (not given, not made up). Worked out from the
  // entered values where the dosing table or the limits can be, otherwise as staff answered.
  const ACT_RESULT = { give: "yes", reduce: "yes", hold: "no", skip: "skip" };
  const checkEntries = (ck, key) => {
    const lim = arr(ck.limits), acts = arr(ck.actions);
    const raw = arr(checks[key]).filter((x) => x && x.date && (ACT_RESULT[x.answer === "yes" ? "give" : x.answer === "no" ? "hold" : x.answer] || (x.values && Object.keys(x.values).length)))
      .slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    return raw.map((x, i) => {
      let result = null, by = null, failed = [], action = null, unsure = false;
      const ctx = { entry: x, here: raw.slice(0, i + 1) };
      if (acts.length && x.values) {
        for (const a of acts) {
          const st = limitsState(arr(a.if), ctx);
          if (st === "unknown") { unsure = true; break; }
          if (st === "yes") { action = a; break; }
        }
        if (action) { result = ACT_RESULT[action.do]; by = "values"; }
      }
      if (result == null && !unsure && lim.length && x.values) {
        const st = lim.map((l) => limitState(l, ctx));
        if (st.includes("no")) { result = "no"; by = "values"; failed = lim.filter((_, j) => st[j] === "no").map((l) => limitText(model, l)); }
        else if (!st.includes("unknown")) { result = "yes"; by = "values"; }
      }
      if (result == null && x.answer) {
        result = x.answer === "reduce" ? "yes" : x.answer; by = "staff";
        if (x.answer === "reduce" || x.answer === "skip") action = acts.find((a) => a.do === x.answer) || null;
      }
      return { ...x, result, by, failed, action };
    });
  };
  const baselineNeeds = (lists) => [...new Set(lists.flat().flatMap(function base(l) { return Array.isArray(l.any) ? l.any.flatMap(base) : l.baseline_factor != null ? [l.measure] : []; }))]
    .filter((mid) => baselines[mid] == null || baselines[mid] === "").map((mid) => measures[mid]?.label || mid);
  const doseCount = {};
  // Checks still "No" past their deadline stop visits (found in a first pass, applied in the next).
  const checkStops = arr(part._checkStops), timeouts = [];
  const ans = makeAns(site, answers);
  const byA = Object.fromEntries(model.anchors.map((a) => [a.id, a]));
  const byV = Object.fromEntries(model.visits.map((v) => [v.id, v]));
  const firstKey = (vid) => `${vid}#${Number.isFinite(+byV[vid]?.repeat?.start_n) ? +byV[vid].repeat.start_n : 1}`;
  // 1. anchors
  const A = {}, how = {};
  if (part.startAnchor && part.startDate) { A[part.startAnchor] = parseD(part.startDate); how[part.startAnchor] = "entered"; }
  for (const a of model.anchors) {
    // A date the site entered (randomisation, surgery, ...) wins over one counted from another date:
    // the real gap between screening and Day 1 differs from patient to patient.
    if (a.kind !== "start" && a.kind !== "last_visit" && answers["@" + a.id]) { A[a.id] = parseD(answers["@" + a.id]); how[a.id] = "entered"; }
    if (a.set_by_visit && actuals[firstKey(a.set_by_visit)]) { A[a.id] = parseD(actuals[firstKey(a.set_by_visit)]); how[a.id] = "done"; }
  }
  const evSorted = logged.slice().sort((a, b) => a.date.localeCompare(b.date));
  const today = part.today ? parseD(part.today) : todayUTC();
  for (const e of evSorted) { const d = model.events.find((x) => x.id === e.type); if (d?.sets_anchor && A[d.sets_anchor] == null) { A[d.sets_anchor] = parseD(e.date); how[d.sets_anchor] = "entered"; } }
  const propagate = (X, H, withEstimates) => {
    for (let i = 0; i < 25; i++) {
      let ch = false;
      for (const a of model.anchors) {
        if (a.kind !== "ask" && a.relative_to && byA[a.relative_to]) {
          const p = a.relative_to, off = a.offset_days || 0;
          if (X[a.id] == null && X[p] != null) { X[a.id] = addD(X[p], off); if (H) H[a.id] = "derived"; ch = true; }
          else if (X[a.id] != null && X[p] == null && byA[p].kind !== "ask" && byA[p].kind !== "last_visit") { X[p] = addD(X[a.id], -off); if (H) H[p] = "derived"; ch = true; }
        }
        if (withEstimates && a.kind === "ask" && X[a.id] == null) {
          for (const est of a.estimates || []) {
            if (condState(est.when, ans) === "no") continue;
            if (X[est.relative_to] != null) { X[a.id] = addD(X[est.relative_to], num(est.offset_days)); ch = true; }
            break;
          }
        }
      }
      if (!ch) break;
    }
  };
  propagate(A, how, false);
  const E = { ...A }; propagate(E, null, true);
  const relOf = (aid, off) => `${byA[aid]?.label || aid}${sOff(off)}`;
  const warnings = [];
  const days = (n) => `${n} day${Math.abs(n) === 1 ? "" : "s"}`;
  const rangeText = (lo, hi) => (lo != null && hi != null ? `${lo} to ${hi} days` : lo != null ? `at least ${days(lo)}` : `at most ${days(hi)}`);
  // A date the protocol allows within a range ("screening within 28 days before Day 1") is entered
  // per participant and checked against that range; it is never fixed to one typical gap.
  for (const a of model.anchors) {
    const g = a.range;
    if (!g?.relative_to || A[a.id] == null || A[g.relative_to] == null || how[a.id] === "derived") continue;
    const d = Math.round((A[a.id] - A[g.relative_to]) / DAY);
    if ((g.min_days != null && d < g.min_days) || (g.max_days != null && d > g.max_days))
      warnings.push({ rule: `range:${a.id}`, kind: "window", text: `${a.label} is ${days(d)} after ${relOf(g.relative_to, 0)}; the protocol allows ${rangeText(g.min_days, g.max_days)}.` });
  }

  // ending events
  const endEv = evSorted.find((e) => model.events.find((d) => d.id === e.type)?.ends_schedule);
  const endAt = endEv ? parseD(endEv.date) : null;
  // An anchor plus every anchor counted from it. Visits counted from the anchor an ending event
  // sets (end-of-treatment follow-up, for example) are that event's consequences, so they stay.
  const derivedFrom = (root) => {
    const s = new Set(root ? [root] : []);
    for (let i = 0; i < 25; i++) {
      let ch = false;
      for (const a of model.anchors) if (!s.has(a.id) && a.relative_to && s.has(a.relative_to)) { s.add(a.id); ch = true; }
      if (!ch) break;
    }
    return s;
  };
  const endKeeps = derivedFrom(endEv ? model.events.find((d) => d.id === endEv.type)?.sets_anchor : null);
  // Visits the protocol keeps after that event (safety follow-up 30 days after the last dose).
  const endKeepsVisits = new Set(endEv ? arr(model.events.find((d) => d.id === endEv.type)?.keeps_visits) : []);
  // The anchor of kind "last_visit" a visit's date ultimately counts from, if any.
  const lastRoot = (aid) => {
    for (let i = 0, a = byA[aid]; a && i < 25; i++, a = byA[a.relative_to]) if (a.kind === "last_visit") return a;
    return null;
  };

  // A visit occurrence's date for rule conditions: recorded if done, otherwise planned from its
  // anchor, the visit it follows or the previous occurrence. null while that is not known.
  const plannedVisit = (vid, n, depth = 0) => {
    const v = byV[vid];
    if (!v || depth > 20) return null;
    const startN = Number.isFinite(+v.repeat?.start_n) ? +v.repeat.start_n : 1;
    const k = n ?? startN;
    if (actuals[`${vid}#${k}`]) return parseD(actuals[`${vid}#${k}`]);
    if (v.repeat && k > startN) {
      if (!v.repeat.chain && !v.follows) return A[v.anchor] != null ? addD(A[v.anchor], v.offset_days + (k - startN) * num(v.repeat.every_days)) : null;
      const p = plannedVisit(vid, k - 1, depth + 1);
      return p == null ? null : addD(p, num(v.repeat.every_days));
    }
    if (v.follows) { const b = plannedVisit(v.follows, k, depth + 1); return b == null ? null : addD(b, v.offset_days); }
    return A[v.anchor] != null ? addD(A[v.anchor], v.offset_days) : null;
  };
  // A rule's conditions ("if", all must hold): an answer, an answer logged with the triggering
  // event, the trigger's timing against an anchor or a visit, or which time the event is logged.
  // "unknown" when something it depends on is not recorded yet: the rule then waits.
  const ruleCond = (c, T, e, idx) => {
    if (Array.isArray(c.any)) {
      const st = c.any.map((x) => ruleCond(x, T, e, idx));
      return st.includes("yes") ? "yes" : st.includes("unknown") ? "unknown" : st.length ? "no" : "yes";
    }
    if (c.not) { const st = ruleCond(c.not, T, e, idx); return st === "yes" ? "no" : st === "no" ? "yes" : "unknown"; }
    if (c.occurrence != null) return e ? (idx + 1 === +c.occurrence ? "yes" : "no") : "yes";
    if (c.event_question != null) return e ? condOne({ ...c, question: c.event_question }, (q) => e.answers?.[q]) : "unknown";
    if (c.timing) {
      const tg = c.timing;
      const base = tg.anchor ? A[tg.anchor] ?? null : tg.visit ? plannedVisit(tg.visit, tg.n ?? null) : null;
      if (base == null || T == null) return "unknown";
      const d = Math.round((T - base) / DAY);
      if ((tg.min_days != null && d < tg.min_days) || (tg.max_days != null && d > tg.max_days)) return "no";
      return "yes";
    }
    if (c.question != null) return condOne(c, ans);
    return "yes";
  };
  const ruleIf = (r, T, e, idx) => {
    let unknown = false;
    for (const c of arr(r.if)) { const s = ruleCond(c, T, e, idx); if (s === "no") return "no"; if (s === "unknown") unknown = true; }
    return unknown ? "unknown" : "yes";
  };

  // rules: what happens to the schedule once something happens. A rule acts only when its date is
  // known (an anchor's date, or the first logged event of its type that meets the rule's conditions);
  // until then the visits it would stop carry stopsIf so people can see what may change. Branches
  // of one event (before / after Week 12, grade 3 / grade 1, arm A / arm B) are separate rules
  // whose conditions choose which one applies.
  const waiting = [];
  const rules = arr(model.rules).map((r) => {
    let T = null;
    if (r.when?.anchor) {
      const t = A[r.when.anchor] ?? null;
      const s = t != null ? ruleIf(r, t, null, 0) : null;
      if (s === "yes") T = t; else if (s === "unknown") waiting.push({ r, at: t });
    } else if (r.when?.event) {
      const list = evSorted.filter((x) => x.type === r.when.event);
      let pending = null;
      for (let i = 0; i < list.length && T == null; i++) {
        const t = parseD(list[i].date), s = ruleIf(r, t, list[i], i);
        if (s === "yes") T = t; else if (s === "unknown" && pending == null) pending = t;
      }
      if (T == null && pending != null) waiting.push({ r, at: pending });
    }
    const opens = r.when?.anchor || model.events.find((d) => d.id === r.when?.event)?.sets_anchor || null;
    // A trigger dated before the date it is measured from (Injection 2 before Injection 1) is a
    // mistake, not a deviation: the rule is not applied and the date is flagged for correction.
    const al = r.allowed;
    let bad = null;
    if (T != null && al?.relative_to && A[al.relative_to] != null && T < A[al.relative_to] && (al.min_days ?? 0) >= 0) {
      bad = T; T = null;
      // Nothing is dated from an impossible date either: those visits wait for a corrected one.
      if (r.when?.anchor) for (const id of derivedFrom(r.when.anchor)) { A[id] = null; how[id] = "pending"; }
    }
    return { r, T, bad, keeps: r.stop_all ? derivedFrom(opens) : null };
  });
  for (const c of checkStops) rules.push({
    r: { id: c.id, label: c.label, effect: c.effect, when: {}, if: [], stop_visits: c.visits, stop_all: false, same_day: c.sameDay || "keep", blocks: [] },
    T: c.T, bad: null, keeps: null,
  });
  // Rules that change an answer from their date on (crossover to another arm): visits and
  // procedures on or after that date use the new answer.
  const switches = rules.filter((x) => x.T != null && x.r.set_answers && Object.keys(x.r.set_answers).length).sort((a, b) => a.T - b.T);
  const ansAt = (t) => {
    if (!switches.length || t == null) return ans;
    const over = {};
    for (const x of switches) if (t >= x.T) Object.assign(over, x.r.set_answers);
    return (q) => (over[q] != null ? over[q] : ans(q));
  };
  const ansLast = ansAt(Infinity);
  // Rules that pause visits (treatment interruption) until a resume date: "shift" moves later
  // visits by the length of the pause, "skip" drops the ones that fell inside it. While the resume
  // date is not known, visits after the pause wait for it.
  // What has happened, as of a date (null = as of now): for conditions on events and visits.
  const occ = {};
  const factsAt = (at) => ({
    eventBy: (id, before) => evSorted.some((e) => e.type === id && (at == null || parseD(e.date) <= at) && (before == null || parseD(e.date) < before)),
    dateOf: (ref) => (ref?.anchor ? A[ref.anchor] ?? null : ref?.visit ? plannedVisit(ref.visit, ref.n ?? null) : null),
    visitStatus: (vid, n) => {
      const list = (occ[vid] || []).filter((x) => !x.cancelled);
      const o = n != null ? list.find((x) => x.key.endsWith("#" + n)) : list[0];
      const key = o?.key || `${vid}#${n ?? (Number.isFinite(+byV[vid]?.repeat?.start_n) ? +byV[vid].repeat.start_n : 1)}`;
      if (actuals[key]) return "done";
      if (o?.check?.state === "skipped") return "not_done";
      return o?.hi != null && o.hi < Math.min(today, at ?? Infinity) ? "missed" : "pending";
    },
  });
  const pauses = rules.filter((x) => x.T != null && x.r.pause?.visits?.length).map((x) => ({
    x, visits: new Set(x.r.pause.visits), R: x.r.pause.resume ? A[x.r.pause.resume] ?? null : null,
    mode: x.r.pause.mode === "skip" ? "skip" : "shift", label: x.r.pause.resume ? byA[x.r.pause.resume]?.label : null,
  }));
  // Visits an ending rule keeps: its own list and that of the event that triggers it.
  const keptBy = (x, vid) => arr(x.r.keeps_visits).includes(vid)
    || arr(model.events.find((d) => d.id === x.r.when?.event)?.keeps_visits).includes(vid);
  const hits = (x, vid, anchor) => (x.r.stop_all ? !x.keeps.has(anchor) && !keptBy(x, vid) : arr(x.r.stop_visits).includes(vid));
  const after = (x, t) => (x.r.same_day === "cancel" ? t >= x.T : t > x.T);
  // A visit still waiting for a date when its rule has happened will not happen either.
  const stopBy = (vid, anchor, t) => rules.find((x) => x.T != null && hits(x, vid, anchor) && (t == null || after(x, t))) || null;
  // Rules that end everything (withdrawal, death) would tag every visit, so only targeted stops show.
  const stopsIf = (vid, anchor) => rules.filter((x) => x.T == null && !x.r.stop_all && hits(x, vid, anchor)).map((x) => x.r.label);
  const stopped = [];

  const day1 = model.anchors.find((a) => a.is_day1);
  const studyDay = (t) => {
    if (t == null || !day1 || A[day1.id] == null) return null;
    const d = Math.round((t - A[day1.id]) / DAY);
    return day1.day_zero ? d : d >= 0 ? d + 1 : d;
  };
  const rows = [], skipped = [], cut = [];
  let cancelled = 0;
  const finish = (row) => {
    const t = row.actual ?? row.t;
    row.sd = studyDay(t);
    if (row.t != null && row.actual == null && (dow(row.t) === 0 || dow(row.t) === 6)) {
      row.weekend = true;
      for (let k = 1; k <= 3 && !row.alt; k++) for (const s of [-k, k]) { const c = addD(row.t, s); if (c >= row.lo && c <= row.hi && dow(c) !== 0 && dow(c) !== 6) { row.alt = c; break; } }
    }
    if (row.v.duration_days && t != null) row.endDay = addD(t, row.v.duration_days - 1);
    rows.push(row);
  };

  // A repeat-until check not met within max_wait_days of the first occurrence ("CrCl still < 50 after
  // 4 weeks: discontinue cisplatin"): a warning, and with on_timeout "stop" its visits stop from then.
  const untilTimeout = (v, first, metAt, code) => {
    const ck = v.check;
    if (first == null || ck.max_wait_days == null) return;
    const deadline = addD(first, num(ck.max_wait_days));
    if (metAt != null ? metAt <= deadline : today <= deadline) return;
    const stop = ck.on_timeout === "stop";
    warnings.push({ rule: `check:${v.id}`, kind: "window", text: `${code}: “${ck.text}” not met within ${days(num(ck.max_wait_days))} of the first one (by ${fmtS(deadline)}).${stop ? " The protocol stops these visits from then." : " Check what the protocol says next."}` });
    if (stop && !timeouts.some((x) => x.id === `check:${v.id}`)) timeouts.push({
      id: `check:${v.id}`, label: `${ck.text} not met`, T: deadline, visits: [v.id, ...arr(ck.stop_visits)],
      effect: `“${ck.text}” not met within ${days(num(ck.max_wait_days))}.`,
    });
  };

  // 2. scheduled visits (followed visits first)
  // A visit counted from "the last of" other visits comes after them.
  const done = new Set(), order = [];
  const ready = (v) => (!v.follows || done.has(v.follows))
    && (!lastRoot(v.anchor) || arr(lastRoot(v.anchor).last_of).every((x) => done.has(x) || !byV[x]));
  for (let pass = 0; pass < 20 && order.length < model.visits.length; pass++)
    for (const v of model.visits) if (!done.has(v.id) && ready(v)) { done.add(v.id); order.push(v); }
  for (const v of model.visits) if (!done.has(v.id)) { done.add(v.id); order.push(v); }
  // "The last of these visits" (last dose): the latest one still on the schedule, recorded or
  // planned. Unknown while one of them has no date yet; after an early stop it is the last one given.
  const lastSet = new Set();
  const setLast = (a) => {
    lastSet.add(a.id);
    let t = null, tE = null, any = false, unknown = false;
    for (const vid of arr(a.last_of)) for (const o of occ[vid] || []) {
      if (o.cancelled || o.check?.state === "skipped") continue;
      any = true;
      const d = o.actual ?? o.t, e = d ?? o.tE;
      if (d == null) unknown = true; else if (t == null || d > t) t = d;
      if (e != null && (tE == null || e > tE)) tE = e;
    }
    if (any && !unknown && t != null) { A[a.id] = t; how[a.id] = "derived"; }
    if (any && tE != null) E[a.id] = tE;
    // Drug taken daily between visits: a logged early stop (a rule ending the schedule) is the
    // last dose, not the last visit before it.
    if (a.stop_is_last_dose) {
      const stop = rules.filter((x) => x.T != null && x.r.stop_all).reduce((m, x) => (m == null || x.T < m ? x.T : m), null);
      if (stop != null) { A[a.id] = stop; how[a.id] = "derived"; E[a.id] = stop; }
    }
    propagate(A, how, false); propagate(E, null, true);
  };
  for (const v of order) {
    const lr = lastRoot(v.anchor);
    if (lr && !lastSet.has(lr.id)) setLast(lr);
    // Conditions on what happened are checked per occurrence, at its date.
    const facty = hasFacts(v.include_if);
    let cs = condState(v.include_if, ans, facty ? "ignore" : undefined);
    // A later answer change (crossover) can bring a visit in from its date on.
    if (cs === "no" && switches.length && condState(v.include_if, ansLast) !== "no") cs = "later";
    if (cs === "no") { skipped.push(v); occ[v.id] = []; continue; }
    const r = v.repeat;
    const untilId = r?.until_anchor || null, until = untilId ? A[untilId] : null;
    let count = 1, countUnknown = false;
    if (r) {
      const qa = r.count_question ? ans(r.count_question) : undefined;
      if (qa !== undefined && qa !== "" && Number.isFinite(+qa)) count = +qa;
      else if (num(r.count, 0) > 0) count = num(r.count, 0);
      // A time limit or a known end date decides how many; generate enough and let them cut it.
      else if (r.max_days != null || (untilId && until != null)) count = 200;
      // The protocol does not say how many: show the next few, flagged, never a made-up end.
      else { count = 6; countUnknown = !r.open_ended && !untilId; }
      count = Math.max(1, Math.min(Math.round(count), 200));
    }
    const startN = Number.isFinite(+r?.start_n) ? +r.start_n : 1;
    const fo = v.follows ? (occ[v.follows] || []).filter((x) => !x.cancelled) : null;
    const lastMode = v.follows_occurrence === "last";
    if (fo && !fo.length) {
      // Every occurrence it follows was cancelled: by a rule (this visit goes with it) or by an ending event.
      const prev = occ[v.follows] || [], why = prev.find((x) => x.stoppedBy);
      if (why) {
        const key = `${v.id}#${startN}`;
        stopped.push({ rule: why.stoppedBy, at: why.stoppedAt, key, code: v.code, t: why.t != null ? addD(why.t, v.offset_days) : null });
        occ[v.id] = [{ key, code: v.code, t: null, tE: null, actual: null, cancelled: true, stoppedBy: why.stoppedBy, stoppedAt: why.stoppedAt }];
        continue;
      }
      if (prev.length) cancelled++; else skipped.push(v);
      occ[v.id] = []; continue;
    }
    const list = []; let cutN = 0;
    let untilFirst = null, untilMetAt = null;
    // A repeat-until visit with no stated count: the next ones are shown until it is met.
    if (r?.until_check && v.check) countUnknown = false;
    for (let n = 0; n < count; n++) {
      const numN = startN + n, key = `${v.id}#${numN}`;
      let t = null, tE = null, rel = "";
      if (fo && (lastMode ? n === 0 : fo.length > 1 || n === 0)) {
        const b = lastMode ? fo[fo.length - 1] : fo[n]; if (!b) break;
        const bt = b.actual ?? b.t;
        t = bt != null ? addD(bt, v.offset_days) : null;
        tE = (bt ?? b.tE) != null ? addD(bt ?? b.tE, v.offset_days) : null;
        rel = `${b.code}${sOff(v.offset_days)}`;
      } else if (n === 0) {
        t = A[v.anchor] != null ? addD(A[v.anchor], v.offset_days) : null;
        tE = E[v.anchor] != null ? addD(E[v.anchor], v.offset_days) : null;
        rel = relOf(v.anchor, v.offset_days);
      } else {
        const prev = list[list.length - 1] || null;
        if (!prev) break;
        // A fixed grid counts from planned dates, so a held or delayed occurrence doesn't move it.
        const pb = r.chain ? (prev.actual ?? prev.t) : (prev.tPlan ?? prev.t);
        const pbE = r.chain ? (prev.actual ?? prev.t ?? prev.tE) : (prev.tPlan ?? prev.t ?? prev.tE);
        const start = list[0].actual ?? list[0].t ?? list[0].tE;
        const every = everyAt(r, start != null && pbE != null ? Math.round((pbE - start) / DAY) : 0);
        t = pb != null ? addD(pb, every) : null;
        tE = pbE != null ? addD(pbE, every) : null;
        rel = r.chain || fo ? `${prev.code}${sOff(every)}` : relOf(v.anchor, v.offset_days + n * every);
      }
      const actual = actuals[key] ? parseD(actuals[key]) : null;
      const code = r?.code_pattern ? String(r.code_pattern).replaceAll("{n}", numN) : r ? `${v.code}·${numN}` : v.code;
      const name = r?.label_pattern ? String(r.label_pattern).replaceAll("{n}", numN) : r ? `${v.name} ${numN}` : v.name;
      if (n > 0 && until != null && actual == null && (t ?? tE) != null && (t ?? tE) > until) { cutN++; continue; }
      // A time limit ("treatment for up to 2 years") counts from the first occurrence.
      const first = list[0] ? list[0].actual ?? list[0].t ?? list[0].tE : null;
      if (n > 0 && r?.max_days != null && actual == null && first != null && (t ?? tE) != null && (t ?? tE) > addD(first, num(r.max_days))) break;
      let pausedBy = null;
      for (const p of pauses) {
        if (!p.visits.has(v.id) || actual != null || t == null || t <= p.x.T) continue;
        const prevT = list.length ? list[list.length - 1].t : null;
        if (p.R == null) { t = null; tE = null; rel = `after ${p.label || "the pause"} (${p.x.r.label})`; pausedBy = p; break; }
        if (p.mode === "skip" && t < p.R) { pausedBy = p; break; }
        // A visit that follows another moves with it, so only the first one after the pause shifts.
        if (p.mode === "shift" && !fo && (n === 0 || prevT == null || prevT <= p.x.T)) {
          const shift = Math.round((p.R - p.x.T) / DAY);
          t = addD(t, shift); if (tE != null) tE = addD(tE, shift);
        }
      }
      if (pausedBy && pausedBy.mode === "skip" && pausedBy.R != null) {
        stopped.push({ rule: pausedBy.x.r, at: pausedBy.x.T, key, code, t });
        list.push({ key, code, t, tE, actual, cancelled: true, stoppedBy: pausedBy.x.r, stoppedAt: pausedBy.x.T }); continue;
      }
      let check = null;
      const doseHits = [];
      if (v.check && r?.until_check) {
        // Repeats until met: a No means "not yet, comes back next time"; a Yes is the last one.
        const ck = v.check, entries = checkEntries(ck, key);
        const given = entries.filter((x) => x.result === "yes" || x.result === "no");
        const state = !given.length ? "ask" : given.some((x) => x.result === "yes") ? "met" : "again";
        if (n === 0) untilFirst = actual ?? t;
        if (state === "met") untilMetAt = parseD(given.find((x) => x.result === "yes").date);
        check = { def: ck, state, given: entries, next: null, deadline: null, timedOut: false, delay: 0, tPlan: t, until: true,
          needsBaseline: baselineNeeds([arr(ck.limits), ...arr(ck.actions).map((a) => arr(a.if))]) };
      } else if (v.check) {
        const ck = v.check, lim = arr(ck.limits);
        const entries = checkEntries(ck, key);
        // The answers up to the first that ends the wait: a Yes (goes ahead) or a skip (not given).
        const given = entries.filter((x) => x.result);
        const yesAt = given.findIndex((x) => x.result === "yes" || x.result === "skip");
        const before = yesAt < 0 ? given : given.slice(0, yesAt);
        const held = before.some((x) => x.result === "no");
        const end = yesAt >= 0 ? given[yesAt] : null;
        const endT = end ? parseD(end.date) : null;
        const yesT = end?.result === "yes" ? endT : null;
        const deadline = t != null && ck.max_wait_days != null ? addD(t, num(ck.max_wait_days)) : null;
        const state = !given.length ? "ask" : end?.result === "skip" ? "skipped" : !held ? "ok" : yesT != null ? "resumed" : "held";
        const timedOut = held && deadline != null && (endT != null ? endT > deadline : today > deadline);
        const lastNo = held ? parseD(before.filter((x) => x.result === "no").pop().date) : null;
        const next = state === "held" && ck.recheck_days ? addD(lastNo, num(ck.recheck_days)) : null;
        let delay = 0;
        const tPlan = t;
        if (actual == null && state === "resumed" && t != null && yesT > t) {
          delay = Math.round((yesT - t) / DAY);
          t = yesT; if (tE != null) tE = addD(tE, delay);
        } else if (actual == null && state === "held") {
          // Held: no date until a "Yes"; the next check only orders it among the other visits.
          t = null; tE = next ?? lastNo; rel = `held until “${ck.text}” is Yes`;
        }
        const needsBaseline = baselineNeeds([lim, ...arr(ck.actions).map((a) => arr(a.if))]);
        check = { def: ck, state, given: entries, next, deadline, timedOut, delay, tPlan, needsBaseline };
        // Skipped: not given and not made up; the occurrence keeps its planned date, so later dates stay.
        if (state === "skipped") check.skip = { date: end.date, why: end.action ? actionText(model, end.action) : null, by: end.by };
        // Reduced this time only ("50% of the dose"): later doses keep their level.
        if ((state === "ok" || state === "resumed") && end.action?.do === "reduce")
          check.reduce = { percent: end.action.percent, dose: end.action.dose || null, why: actionText(model, end.action) };
        // Dose changes once the visit goes ahead: conditions on the values entered for it (during the
        // hold too), how long it waited, and which time the conditions held.
        if (state === "ok" || state === "resumed") {
          const yes = end, upTo = entries.slice(0, entries.indexOf(yes) + 1);
          const waited = state === "resumed" && tPlan != null ? Math.max(0, Math.round((yesT - tPlan) / DAY)) : 0;
          arr(ck.dose_changes).forEach((c, j) => {
            const st = limitsState(arr(c.if), { entry: yes, here: upTo, held: waited });
            if (st === "unknown") {
              warnings.push({ rule: `dose:${key}:${j}`, kind: "info", text: `${code}: whether the dose changes (${arr(c.if).map((l) => limitText(model, l)).join(" and ")}) can't be worked out from what was entered.` });
              return;
            }
            if (st !== "yes") return;
            const cid = `${v.id}:${j}`, n = (doseCount[cid] = (doseCount[cid] || 0) + 1);
            if (c.occurrence == null || n === c.occurrence || (c.and_later && n > c.occurrence))
              doseHits.push({ change: c, why: c.note || arr(c.if).map((l) => limitText(model, l)).join(" and ") });
          });
        }
        if (timedOut) {
          const stop = ck.on_timeout === "stop";
          warnings.push({ rule: `check:${key}`, kind: "window", text: `${code}: no Yes to “${ck.text}” within ${days(num(ck.max_wait_days))} of the planned date (by ${fmtS(deadline)}).${stop ? " The protocol stops these visits from then." : " Check what the protocol says next."}` });
          if (stop && !timeouts.some((x) => x.id === `check:${v.id}`)) timeouts.push({
            id: `check:${v.id}`, label: `${ck.text} not met`, T: deadline, visits: [v.id, ...arr(ck.stop_visits)],
            effect: `No Yes to “${ck.text}” within ${days(num(ck.max_wait_days))}.`,
          });
        }
      }
      const occAns = ansAt(t ?? tE);
      const occFacts = factsAt(actual ?? t ?? tE);
      if ((switches.length || facty) && actual == null && condState(v.include_if, occAns, facty ? occFacts : undefined) === "no") {
        list.push({ key, code, t, tE, actual, cancelled: true }); continue;
      }
      const by = actual == null ? stopBy(v.id, v.anchor, t) : null;
      if (by) { stopped.push({ rule: by.r, at: by.T, key, code, t }); list.push({ key, code, t, tE, actual, cancelled: true, stoppedBy: by.r, stoppedAt: by.T }); continue; }
      const keptAfterEnd = endKeepsVisits.has(v.id) && t != null;
      if (endAt != null && actual == null && !endKeeps.has(v.anchor) && !keptAfterEnd && (t == null || t > endAt)) { cancelled++; list.push({ key, code, t, tE, actual, cancelled: true }); continue; }
      // "Or as soon as possible": kept after participation ended, but its date had already passed then.
      let asap = null;
      if (v.asap_if_past && actual == null && t != null) {
        const ends = [endAt != null && keptAfterEnd ? endAt : null,
          ...rules.filter((x) => x.T != null && x.r.stop_all && keptBy(x, v.id)).map((x) => x.T)].filter((x) => x != null);
        const from = ends.length ? Math.min(...ends) : null;
        if (from != null && t < from) { asap = { was: t, from }; t = from; if (tE != null && tE < from) tE = from; }
      }
      const { procs, occNote } = visitProcs(v, numN, occAns, occFacts);
      // A recorded date later than planned by more than the protocol allows (cycle delays).
      if (actual != null && t != null && r?.max_delay_days != null && Math.round((actual - t) / DAY) > num(r.max_delay_days)) {
        warnings.push({ rule: `delay:${key}`, kind: "window", text: `${code} was ${days(Math.round((actual - t) / DAY))} later than planned; the protocol allows up to ${days(num(r.max_delay_days))}.${r.delay_effect ? ` ${r.delay_effect}` : ""}` });
      }
      // A minimum gap to the previous occurrence or another visit ("at least 14 days between doses").
      const gap = v.min_gap;
      if (actual != null && gap?.days != null) {
        const ref = gap.after ? (occ[gap.after] || []).filter((x) => !x.cancelled && (x.actual ?? x.t) != null && (x.actual ?? x.t) <= actual).pop() : list[list.length - 1];
        const rt = ref ? ref.actual ?? ref.t : null;
        if (rt != null && Math.round((actual - rt) / DAY) < num(gap.days))
          warnings.push({ rule: `gap:${key}`, kind: "window", text: `${code} is ${days(Math.round((actual - rt) / DAY))} after ${ref.code}; the protocol requires at least ${days(num(gap.days))}.` });
      }
      const row = {
        type: "visit", key, v, code, name, t, tE, actual, rel, cond: cs === "later" ? "yes" : cs, procs, occNote, first: n === 0,
        countUnknown: countUnknown && n === count - 1,
        lo: t != null ? addD(t, -v.window_before) : null, hi: t != null ? addD(t, v.window_after) : null,
        how: actual != null ? "done" : t != null ? (how[v.anchor] === "entered" && !fo && n === 0 ? "entered" : "derived") : "pending",
        pausedBy: pausedBy ? pausedBy.x.r.label : null,
        untilPending: !!untilId && until == null, openEnded: !!r?.open_ended && n === count - 1, untilLabel: untilId ? byA[untilId]?.label : null,
        stopsIf: actual == null ? stopsIf(v.id, v.anchor) : [],
        check, tPlan: check ? check.tPlan : undefined, doseHits, doses: [], asap,
      };
      list.push(row); finish(row);
      if (check?.until && check.state === "met") break;
    }
    if (r?.until_check && v.check) untilTimeout(v, untilFirst, untilMetAt, list[list.length - 1]?.code || v.code);
    occ[v.id] = list;
    if (cutN) cut.push({ v, n: cutN, anchor: untilId });
  }

  // 3. event-triggered visits
  // An event visit may depend on an answer given when the event is logged ("event_question").
  const evCond = (c) => (!c || typeof c !== "object" ? c
    : Array.isArray(c.any) ? { ...c, any: c.any.map(evCond) }
    : c.not ? { ...c, not: evCond(c.not) }
    : c.event_question != null ? { ...c, question: c.event_question } : c);
  for (const e of evSorted) {
    const d = model.events.find((x) => x.id === e.type); if (!d) continue;
    const eAns = (q) => (e.answers && e.answers[q] != null && e.answers[q] !== "" ? e.answers[q] : ans(q));
    const ed = parseD(e.date);
    for (const v of d.visits) {
      if (condState(arr(v.include_if).map(evCond), eAns, factsAt(null)) === "no") continue;
      const r = v.repeat;
      // No count in the protocol: show the next few (flagged), never a made-up single visit.
      const count = r ? Math.max(1, Math.min(num(r.count, 0) || 6, 60)) : 1;
      const uck = r?.until_check && v.check ? v.check : null;
      const evCountUnknown = !!r && !(num(r.count, 0) > 0) && !r.open_ended && r.max_days == null && !uck;
      let untilFirst = null, untilMetAt = null, lastCode = v.code;
      const planned0 = addD(ed, v.offset_days);
      let prev = null, first = null;
      for (let n = 0; n < count; n++) {
        const numN = (Number.isFinite(+r?.start_n) ? +r.start_n : 1) + n;
        const key = `ev.${e.key}.${v.id}#${numN}`;
        const actual = actuals[key] ? parseD(actuals[key]) : null;
        // Repeats count from the event date, or, when the protocol counts from the previous visit
        // (repeat.chain), from the previous one's recorded date: a late visit moves the later ones.
        let t = planned0;
        if (prev) {
          const base = r?.chain ? (prev.actual ?? prev.t) : prev.t;
          t = addD(base, everyAt(r, Math.round((base - first) / DAY)));
        }
        if (n === 0) first = r?.chain ? (actual ?? planned0) : planned0;
        prev = { t, actual };
        if (n > 0 && r?.max_days != null && actual == null && t > addD(first, num(r.max_days))) break;
        if (endAt != null && e !== endEv && ed < endAt && t > endAt && !actual && !endKeepsVisits.has(v.id)) { cancelled++; continue; }
        // Stopped by a rule: one that ends participation (withdrawal) or one that names this visit
        // ("new therapy stops the safety visits").
        const by = actual ? null : rules.find((x) => x.T != null && after(x, t)
          && (x.r.stop_all ? x.r.when?.event !== d.id && ed <= x.T && !keptBy(x, v.id) : arr(x.r.stop_visits).includes(v.id)));
        if (by) { stopped.push({ rule: by.r, at: by.T, key, code: v.code, t }); continue; }
        const { procs, occNote } = visitProcs(v, numN, eAns, factsAt(t));
        let check = null;
        if (uck) {
          const entries = checkEntries(uck, key);
          const given = entries.filter((x) => x.result === "yes" || x.result === "no");
          const state = !given.length ? "ask" : given.some((x) => x.result === "yes") ? "met" : "again";
          if (n === 0) untilFirst = actual ?? t;
          if (state === "met") untilMetAt = parseD(given.find((x) => x.result === "yes").date);
          check = { def: uck, state, given: entries, next: null, deadline: null, timedOut: false, delay: 0, tPlan: t, until: true,
            needsBaseline: baselineNeeds([arr(uck.limits), ...arr(uck.actions).map((a) => arr(a.if))]) };
        }
        const row = {
          type: "visit", key, v, t, tE: t, actual, cond: "yes", procs, occNote, first: n === 0,
          code: r?.code_pattern ? String(r.code_pattern).replaceAll("{n}", numN) : v.code,
          name: r?.label_pattern ? String(r.label_pattern).replaceAll("{n}", numN) : v.name,
          rel: `${d.label}${sOff(Math.round((t - ed) / DAY))}`, countUnknown: evCountUnknown && n === count - 1,
          lo: addD(t, -v.window_before), hi: addD(t, v.window_after), how: actual != null ? "done" : "derived",
          fromEvent: `${d.label} · ${fmtS(ed)}`, doses: [], stopsIf: actual == null ? stopsIf(v.id, null) : [], check,
        };
        finish(row);
        lastCode = row.code;
        if (check?.state === "met") break;
      }
      if (uck) untilTimeout(v, untilFirst, untilMetAt, lastCode);
    }
  }

  // 3a. "check in at least 12 hours before dosing": staff record the check-in time on the check-in
  //     visit (participant.doseTimes, keyed like the visit); the dose visit (same occurrence number)
  //     then may not be dosed before check-in + hours. A dose time entered earlier is a deviation.
  //     Without a check-in time only the rule is shown; nothing is guessed.
  const doseTimes = part.doseTimes || {};
  for (const r of rows) {
    const ab = r.type === "visit" && !r.fromEvent ? r.v.arrive_before : null;
    if (!ab?.visit || !(num(ab.hours) > 0)) continue;
    const n = r.key.slice(r.key.lastIndexOf("#") + 1);
    const dose = rows.find((x) => x.type === "visit" && !x.fromEvent && x.key === `${ab.visit}#${n}`)
      || rows.find((x) => x.type === "visit" && !x.fromEvent && x.v.id === ab.visit);
    const day = r.actual ?? r.t, inMin = toMin(doseTimes[r.key]);
    const checkedIn = day != null && inMin != null ? day + inMin * 6e4 : null;
    const from = checkedIn != null ? checkedIn + num(ab.hours) * 36e5 : null;
    r.arrive = { hours: num(ab.hours), doseCode: dose?.code || ab.visit, checkedIn };
    if (!dose) continue;
    dose.doseFrom = { hours: num(ab.hours), checkInCode: r.code, from };
    const doseDate = dose.actual ?? dose.t, doseMin = toMin(doseTimes[dose.key]);
    if (from != null && doseDate != null && doseMin != null && doseDate + doseMin * 6e4 < from) {
      const gap = Math.round(((doseDate + doseMin * 6e4) - checkedIn) / 36e5 * 10) / 10;
      warnings.push({ rule: `arrive:${r.key}`, kind: "window", text: `${dose.code}: dosed at ${doseTimes[dose.key]} on ${fmtS(doseDate)}, ${gap} h after the ${r.code} check-in; the protocol asks for at least ${num(ab.hours)} h.` });
    }
  }

  // 3b. dose levels: each drug's doses in date order, starting from its starting level. At each dose,
  //     rules that happened before it apply once, then the dose changes of a passed check, then a
  //     level staff set. A step below the lowest level stops the drug only when the protocol says so.
  const dropped = [];
  for (const d of arr(model.doses)) {
    const levels = arr(d.levels), idxOf = (id) => levels.findIndex((l) => l.id === id);
    let idx = d.start != null && idxOf(d.start) >= 0 ? idxOf(d.start) : null;
    const doseRows = rows.filter((r) => r.type === "visit" && !r.fromEvent && arr(d.visits).includes(r.v.id) && r.check?.state !== "skipped")
      .map((r) => ({ r, at: r.actual ?? r.t ?? r.tE ?? null }))
      .sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity));
    const fromRules = rules.filter((x) => x.T != null && arr(x.r.dose_changes).some((c) => c.dose === d.id)).sort((a, b) => a.T - b.T);
    const used = new Set();
    for (const { r, at } of doseRows) {
      let changed = null, stop = null;
      const apply = (c, why) => {
        if (c.stop) { stop = why; return; }
        if (c.to != null && idxOf(c.to) >= 0) { idx = idxOf(c.to); changed = why; return; }
        if (idx == null) {
          warnings.push({ rule: `dose:${d.id}:${r.key}`, kind: "info", text: `${r.code}: ${d.label} should change (${why}), but the starting level is not stated. Set the level at this dose.` });
          return;
        }
        const next = idx - num(c.step);
        if (next >= levels.length) {
          if (d.below_lowest === "stop") { stop = `${why}; no lower level`; return; }
          warnings.push({ rule: `dose:${d.id}:${r.key}`, kind: "window", text: `${r.code}: ${d.label} would go below its lowest level (${why}). Check what the protocol says.` });
          idx = levels.length - 1;
        } else idx = Math.max(0, next);
        changed = why;
      };
      for (const x of fromRules) {
        if (used.has(x) || at == null || !after(x, at)) continue;
        used.add(x);
        for (const c of arr(x.r.dose_changes)) if (c.dose === d.id) apply(c, `${x.r.label} ${fmtS(x.T)}`);
      }
      for (const h of arr(r.doseHits)) if (h.change.dose === d.id) apply(h.change, h.why);
      const set = doseLevels[r.key]?.[d.id];
      const manual = set != null && idxOf(set) >= 0;
      if (manual) { idx = idxOf(set); changed = "set by staff"; stop = null; }
      // "Second reduction: omit Day 8": at this level the visit is not given (unless it was recorded).
      const lv = idx != null ? levels[idx] : null;
      if (!stop && !manual && r.actual == null && lv && arr(lv.drop_visits).includes(r.v.id)) {
        dropped.push({ r, rule: { id: `dose:${d.id}:${lv.id}`, label: `${d.label} ${lv.label}`, effect: `At ${lv.label}, ${d.label} is not given at ${r.v.code}.` } });
        continue;
      }
      if (stop && at != null && r.actual == null) {
        if (!timeouts.some((x) => x.id === `dose:${d.id}`)) timeouts.push({
          id: `dose:${d.id}`, label: `${d.label} stopped`, T: at, visits: [...arr(d.visits), ...arr(d.stop_visits)],
          effect: `${d.label} stopped: ${stop}.`, sameDay: "cancel",
        });
        break;
      }
      r.doses.push({ dose: d.id, label: d.label, idx, changed, manual, levels, startIdx: d.start != null && idxOf(d.start) >= 0 ? idxOf(d.start) : null });
    }
  }

  for (const x of dropped) {
    const i = rows.indexOf(x.r);
    if (i < 0) continue;
    rows.splice(i, 1);
    stopped.push({ rule: x.rule, at: x.r.t, key: x.r.key, code: x.r.code, t: x.r.t });
  }

  // 4. decision points
  for (const q of model.questions.filter((q) => q.scope === "decision")) {
    let t = null, tE = null, at = "";
    if (q.ask_at?.visit) {
      const list = (occ[q.ask_at.visit] || []).filter((x) => !x.cancelled);
      const o = q.ask_at.n != null ? list.find((x) => x.key.endsWith("#" + q.ask_at.n)) || list[0] : list[0];
      if (!o) continue;
      t = o.actual ?? o.t; tE = t ?? o.tE; at = o.code;
    } else if (q.ask_at?.anchor) { t = A[q.ask_at.anchor] ?? null; tE = t ?? E[q.ask_at.anchor] ?? null; at = byA[q.ask_at.anchor]?.label || q.ask_at.anchor; }
    if (endAt != null && (t == null || t > endAt) && ans(q.id) == null) continue;
    rows.push({ type: "decision", key: "dec." + q.id, q, t, tE, at, answer: ans(q.id) });
  }

  // 5. checks on the rules themselves: when a trigger may happen, and what one trigger rules out.
  //    kind: "order" (impossible date, rule not applied), "window" / "visit_day" (outside what the
  //    protocol allows), "blocked" (happened after something that rules it out), "info".
  for (const w of waiting)
    warnings.push({ rule: w.r.id, kind: "info", text: `${w.r.label}: whether this applies from ${fmtS(w.at)} depends on something not recorded yet (an answer or a date). Record it to update the schedule.` });
  for (const x of rules) {
    const al = x.r.allowed;
    if (x.bad != null) {
      warnings.push({ rule: x.r.id, kind: "order", text: `${x.r.label} (${fmtS(x.bad)}) is before ${relOf(al.relative_to, 0)} (${fmtS(A[al.relative_to])}), so it was not applied. Check the date.` });
      continue;
    }
    if (x.T == null) continue;
    if (al?.relative_to && A[al.relative_to] != null && (al.min_days != null || al.max_days != null)) {
      const d = Math.round((x.T - A[al.relative_to]) / DAY);
      if ((al.min_days != null && d < al.min_days) || (al.max_days != null && d > al.max_days)) {
        const range = rangeText(al.min_days, al.max_days);
        warnings.push({ rule: x.r.id, kind: "window", text: `${x.r.label} is ${days(d)} after ${relOf(al.relative_to, 0)}; the protocol allows ${range}.` });
      }
    }
    if (al?.not_on_visit) {
      const o = (occ[al.not_on_visit] || []).find((y) => !y.cancelled);
      const ot = o ? o.actual ?? o.t : null;
      if (ot != null && ot === x.T) warnings.push({ rule: x.r.id, kind: "visit_day", text: `${x.r.label} falls on the ${o.code} visit day, which the protocol does not allow.` });
    }
    for (const b of arr(x.r.blocks)) {
      const y = rules.find((z) => z.r.id === b);
      if (!y) continue;
      if (y.T == null) warnings.push({ rule: y.r.id, kind: "info", text: `${y.r.label} is no longer allowed after ${x.r.label} (${fmtS(x.T)}).` });
      else if (y.T >= x.T) warnings.push({ rule: y.r.id, kind: "blocked", text: `${y.r.label} (${fmtS(y.T)}) comes after ${x.r.label} (${fmtS(x.T)}), which rules it out.` });
    }
  }

  // A check past its deadline stops visits: run again with those stops applied.
  const fresh = timeouts.filter((x) => !checkStops.some((c) => c.id === x.id && c.T === x.T));
  if (fresh.length && (part._checkPass || 0) < 3) {
    return computeSchedule(model, site, { ...part, _checkStops: [...checkStops.filter((c) => !fresh.some((x) => x.id === c.id)), ...fresh], _checkPass: (part._checkPass || 0) + 1 });
  }

  const sortT = (r) => r.actual ?? r.t ?? r.tE ?? Infinity;
  rows.forEach((r, i) => (r._i = i));
  rows.sort((a, b) => sortT(a) - sortT(b) || (a.type === "decision") - (b.type === "decision") || a._i - b._i);
  return { rows, skipped, cut, cancelled, A, how, E, endAt, endEv, stopped, warnings };
}
