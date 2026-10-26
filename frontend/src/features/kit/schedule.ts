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
//   doseTimes:{ "c1d1#1": "09:15" }
// }
//
// A visit row: { type:"visit", key, code, name, v (definition), t (planned ts or null),
//   actual, lo, hi (window), rel ("Surgery + 30 d" when t is null), how
//   ("entered"|"derived"|"done"|"pending"), cond ("yes"|"unknown"), procs [{name, if, st}],
//   occNote, first, sd (study day), weekend, alt (nearest weekday in window), endDay,
//   fromEvent, openEnded, untilPending, untilLabel }
// A decision row: { type:"decision", key, q, t, tE, at, answer }
// The code below is copied verbatim from the working app.

import { DAY, parseD, addD, dow, fmtS, arr, num, sOff } from "./utils";

export function makeAns(site, answers) { return (q) => { const a = answers?.[q]; return a === undefined || a === null || a === "" ? site?.[q] : a; }; }
export function condState(conds, ans) {
  if (!Array.isArray(conds) || !conds.length) return "yes";
  let unknown = false;
  for (const c of conds) {
    const a = ans(c.question);
    if (a === undefined || a === null || a === "") { unknown = true; continue; }
    const allowed = arr(Array.isArray(c.in) ? c.in : [c.in]).map((x) => String(x).toLowerCase());
    const norm = String(a).toLowerCase();
    const alias = norm === "yes" ? ["yes", "true", "y"] : norm === "no" ? ["no", "false", "n"] : [norm];
    if (!alias.some((x) => allowed.includes(x))) return "no";
  }
  return unknown ? "unknown" : "yes";
}
export function visitProcs(v, num, ans) {
  let procs = v.procedures.slice(), occNote = null;
  for (const o of arr(v.repeat?.overrides)) if (o.n.includes(num)) {
    procs = procs.filter((p) => !o.remove.includes(p.name));
    for (const p of o.add) if (!procs.some((x) => x.name === p.name)) procs.push(p);
    if (o.note) occNote = o.note;
  }
  return { procs: procs.map((p) => ({ ...p, st: condState(p.if, ans) })).filter((p) => p.st !== "no"), occNote };
}
export function computeSchedule(model, site, part) {
  part = part || {};
  const answers = part.answers || {}, actuals = part.actuals || {}, logged = arr(part.events).filter((e) => e && e.date);
  const ans = makeAns(site, answers);
  const byA = Object.fromEntries(model.anchors.map((a) => [a.id, a]));
  const byV = Object.fromEntries(model.visits.map((v) => [v.id, v]));
  const firstKey = (vid) => `${vid}#${Number.isFinite(+byV[vid]?.repeat?.start_n) ? +byV[vid].repeat.start_n : 1}`;
  // 1. anchors
  const A = {}, how = {};
  if (part.startAnchor && part.startDate) { A[part.startAnchor] = parseD(part.startDate); how[part.startAnchor] = "entered"; }
  for (const a of model.anchors) {
    if (a.kind === "ask" && answers["@" + a.id]) { A[a.id] = parseD(answers["@" + a.id]); how[a.id] = "entered"; }
    if (a.set_by_visit && actuals[firstKey(a.set_by_visit)]) { A[a.id] = parseD(actuals[firstKey(a.set_by_visit)]); how[a.id] = "done"; }
  }
  const evSorted = logged.slice().sort((a, b) => a.date.localeCompare(b.date));
  for (const e of evSorted) { const d = model.events.find((x) => x.id === e.type); if (d?.sets_anchor && A[d.sets_anchor] == null) { A[d.sets_anchor] = parseD(e.date); how[d.sets_anchor] = "entered"; } }
  const propagate = (X, H, withEstimates) => {
    for (let i = 0; i < 25; i++) {
      let ch = false;
      for (const a of model.anchors) {
        if (a.kind !== "ask" && a.relative_to && byA[a.relative_to]) {
          const p = a.relative_to, off = a.offset_days || 0;
          if (X[a.id] == null && X[p] != null) { X[a.id] = addD(X[p], off); if (H) H[a.id] = "derived"; ch = true; }
          else if (X[a.id] != null && X[p] == null && byA[p].kind !== "ask") { X[p] = addD(X[a.id], -off); if (H) H[p] = "derived"; ch = true; }
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
    if (c.occurrence != null) return e ? (idx + 1 === +c.occurrence ? "yes" : "no") : "yes";
    if (c.event_question != null) return e ? condState([{ question: c.event_question, in: c.in }], (q) => e.answers?.[q]) : "unknown";
    if (c.timing) {
      const tg = c.timing;
      const base = tg.anchor ? A[tg.anchor] ?? null : tg.visit ? plannedVisit(tg.visit, tg.n ?? null) : null;
      if (base == null || T == null) return "unknown";
      const d = Math.round((T - base) / DAY);
      if ((tg.min_days != null && d < tg.min_days) || (tg.max_days != null && d > tg.max_days)) return "no";
      return "yes";
    }
    if (c.question != null) return condState([c], ans);
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
  const hits = (x, vid, anchor) => (x.r.stop_all ? !x.keeps.has(anchor) : arr(x.r.stop_visits).includes(vid));
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
  const occ = {};
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

  // 2. scheduled visits (followed visits first)
  const done = new Set(), order = [];
  for (let pass = 0; pass < 20 && order.length < model.visits.length; pass++)
    for (const v of model.visits) if (!done.has(v.id) && (!v.follows || done.has(v.follows))) { done.add(v.id); order.push(v); }
  for (const v of order) {
    const cs = condState(v.include_if, ans);
    if (cs === "no") { skipped.push(v); occ[v.id] = []; continue; }
    const r = v.repeat;
    let count = 1;
    if (r) {
      const qa = r.count_question ? ans(r.count_question) : undefined;
      count = qa !== undefined && qa !== "" && Number.isFinite(+qa) ? +qa : num(r.count, 1) || 1;
      count = Math.max(1, Math.min(Math.round(count), 200));
    }
    const untilId = r?.until_anchor || null, until = untilId ? A[untilId] : null;
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
        const every = num(r.every_days);
        const pb = r.chain ? (prev.actual ?? prev.t) : prev.t;
        t = pb != null ? addD(pb, every) : null;
        const pbE = r.chain ? (prev.actual ?? prev.t ?? prev.tE) : (prev.t ?? prev.tE);
        tE = pbE != null ? addD(pbE, every) : null;
        rel = r.chain || fo ? `${prev.code}${sOff(every)}` : relOf(v.anchor, v.offset_days + n * every);
      }
      const actual = actuals[key] ? parseD(actuals[key]) : null;
      const code = r?.code_pattern ? String(r.code_pattern).replaceAll("{n}", numN) : r ? `${v.code}·${numN}` : v.code;
      const name = r?.label_pattern ? String(r.label_pattern).replaceAll("{n}", numN) : r ? `${v.name} ${numN}` : v.name;
      if (n > 0 && until != null && actual == null && (t ?? tE) != null && (t ?? tE) > until) { cutN++; continue; }
      const by = actual == null ? stopBy(v.id, v.anchor, t) : null;
      if (by) { stopped.push({ rule: by.r, at: by.T, key, code, t }); list.push({ key, code, t, tE, actual, cancelled: true, stoppedBy: by.r, stoppedAt: by.T }); continue; }
      if (endAt != null && actual == null && !endKeeps.has(v.anchor) && (t == null || t > endAt)) { cancelled++; list.push({ key, code, t, tE, actual, cancelled: true }); continue; }
      const { procs, occNote } = visitProcs(v, numN, ans);
      const row = {
        type: "visit", key, v, code, name, t, tE, actual, rel, cond: cs, procs, occNote, first: n === 0,
        lo: t != null ? addD(t, -v.window_before) : null, hi: t != null ? addD(t, v.window_after) : null,
        how: actual != null ? "done" : t != null ? (how[v.anchor] === "entered" && !fo && n === 0 ? "entered" : "derived") : "pending",
        untilPending: !!untilId && until == null, openEnded: !!r?.open_ended && n === count - 1, untilLabel: untilId ? byA[untilId]?.label : null,
        stopsIf: actual == null ? stopsIf(v.id, v.anchor) : [],
      };
      list.push(row); finish(row);
    }
    occ[v.id] = list;
    if (cutN) cut.push({ v, n: cutN, anchor: untilId });
  }

  // 3. event-triggered visits
  for (const e of evSorted) {
    const d = model.events.find((x) => x.id === e.type); if (!d) continue;
    const eAns = (q) => (e.answers && e.answers[q] != null && e.answers[q] !== "" ? e.answers[q] : ans(q));
    const ed = parseD(e.date);
    for (const v of d.visits) {
      if (condState(v.include_if, eAns) === "no") continue;
      const r = v.repeat; const count = r ? Math.max(1, Math.min(num(r.count, 1) || 1, 60)) : 1;
      for (let n = 0; n < count; n++) {
        const numN = (Number.isFinite(+r?.start_n) ? +r.start_n : 1) + n;
        const key = `ev.${e.key}.${v.id}#${numN}`;
        const t = addD(ed, v.offset_days + n * num(r?.every_days));
        if (endAt != null && e !== endEv && ed < endAt && t > endAt && !actuals[key]) { cancelled++; continue; }
        const by = actuals[key] ? null : rules.find((x) => x.T != null && x.r.stop_all && x.r.when?.event !== d.id && ed <= x.T && after(x, t));
        if (by) { stopped.push({ rule: by.r, at: by.T, key, code: v.code, t }); continue; }
        const actual = actuals[key] ? parseD(actuals[key]) : null;
        const { procs, occNote } = visitProcs(v, numN, eAns);
        const row = {
          type: "visit", key, v, t, tE: t, actual, cond: "yes", procs, occNote, first: n === 0,
          code: r?.code_pattern ? String(r.code_pattern).replaceAll("{n}", numN) : v.code,
          name: r?.label_pattern ? String(r.label_pattern).replaceAll("{n}", numN) : v.name,
          rel: `${d.label}${sOff(v.offset_days + n * num(r?.every_days))}`,
          lo: addD(t, -v.window_before), hi: addD(t, v.window_after), how: actual != null ? "done" : "derived",
          fromEvent: `${d.label} · ${fmtS(ed)}`,
        };
        finish(row);
      }
    }
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
  const warnings = [];
  const days = (n) => `${n} day${Math.abs(n) === 1 ? "" : "s"}`;
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
        const range = al.min_days != null && al.max_days != null ? `${al.min_days} to ${al.max_days} days`
          : al.min_days != null ? `at least ${days(al.min_days)}` : `at most ${days(al.max_days)}`;
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

  const sortT = (r) => r.actual ?? r.t ?? r.tE ?? Infinity;
  rows.forEach((r, i) => (r._i = i));
  rows.sort((a, b) => sortT(a) - sortT(b) || (a.type === "decision") - (b.type === "decision") || a._i - b._i);
  return { rows, skipped, cut, cancelled, A, how, E, endAt, endEv, stopped, warnings };
}
