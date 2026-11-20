// History-based visit intervals ("treat and extend"): the interval to the next occurrence of a
// repeating visit is worked out from the interval that led to the previous one, the finding
// confirmed there and the history of earlier occurrences. Pure functions, no I/O.
//
// A repeat carries the operation in `repeat.adaptive` (normalized by backend app/kit/adaptive.py):
//   decide_from_n   the first occurrence whose finding sets the next interval (after loading doses)
//   basis           "actual": the next target counts from the previous occurrence's recorded date
//                   "target": from the previous occurrence's target date
//   start_days      the current interval at decide_from_n
//   min_days, max_days
//   finding         {id, label, kind: "count"|"boolean"|"number", roles}: confirmed at each occurrence
//   active_if       when a finding counts as activity (for the historical breakpoint)
//   steps           first match wins: {if: {op, value} | {in: [...]}, last?: "extended"|"not_extended",
//                   add_days | set_days | keep}
//   breakpoint      {after_extensions, cap_offset_days, count_activity_at}: once that many extension
//                   attempts were made, an extension is capped at the longest interval that ended with
//                   activity + offset ("extended_intervals": only intervals an extension reached count;
//                   "any_interval": every one). Recorded the first time it applies; keeps applying.
//   recommendation  {id, label, unit_days, roles}: an external recommendation (reading centre)
//   precedence      "calculated" | "recommendation" | "recommendation_if_given" (calculated until the
//                   recommendation is recorded, then the recommendation) | "shorter" | "longer" | "review"
// Nothing is guessed: a missing finding, recommendation or decision leaves the next occurrence pending.

export const CHOICE_INPUT = "@interval";

/** A participant's confirmed value for one input at one occurrence, or null. */
export function observed(part: any, key: string, input: string): any {
  const x = part?.observations?.[key]?.[input];
  if (x == null) return null;
  const value = typeof x === "object" ? x.value : x;
  return value === "" || value == null ? null : value;
}

function test(c: any, value: any): boolean {
  if (!c || typeof c !== "object") return false;
  if (Array.isArray(c.in)) return c.in.map((x: any) => String(x).toLowerCase()).includes(String(value).toLowerCase());
  const x = Number(value), y = Number(c.value);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
  switch (c.op) {
    case "=": return x === y;
    case "!=": return x !== y;
    case "<": return x < y;
    case "<=": return x <= y;
    case ">": return x > y;
    case ">=": return x >= y;
    default: return false;
  }
}

export type AdaptiveNeed = { input: string; label: string; kind: string; roles: string[]; at: string; options?: number[] };
export type AdaptiveDecision = {
  key: string; code: string; current: number; finding: any; calculated: number | null; recommended: number | null;
  interval: number | null; attempt: boolean; capApplied: number | null; limitedBy: "min" | "max" | null;
  needs: AdaptiveNeed[]; notes: string[]; problem: string | null;
  /** How the interval that led here was reached (null for the first decision). */
  reachedBy: "extended" | "reduced" | "same" | null;
  /** Booked on the calculated interval while the recommendation that would replace it is awaited. */
  provisional?: boolean;
};

export function newAdaptiveState(cfg: any) {
  return { C: Number(cfg.start_days), extensions: 0, lastChange: null as null | "extended" | "reduced" | "same", cap: null as number | null,
    history: [] as AdaptiveDecision[], broken: null as AdaptiveNeed[] | null };
}

const weeks = (d: number) => (d % 7 === 0 ? `${d / 7} weeks` : `${d} days`);

/**
 * The decision made at one occurrence: the next interval, or what is still needed. Mutates `st`
 * only when the decision is complete.
 */
export function decide(cfg: any, st: ReturnType<typeof newAdaptiveState>, part: any, key: string, code: string): AdaptiveDecision {
  const out: AdaptiveDecision = { key, code, current: st.C, finding: null, calculated: null, recommended: null, interval: null,
    attempt: false, capApplied: null, limitedBy: null, needs: [], notes: [], problem: null, reachedBy: st.lastChange };
  if (st.broken) {
    out.needs = st.broken.map((n) => ({ ...n }));
    out.problem = "An earlier interval could not be worked out, so this one cannot either.";
    return out;
  }
  const f = cfg.finding;
  const finding = observed(part, key, f.id);
  out.finding = finding;
  if (finding == null) out.needs.push({ input: f.id, label: f.label, kind: f.kind, roles: f.roles, at: key });
  const rec = cfg.recommendation;
  const recValue = rec ? observed(part, key, rec.id) : null;
  out.recommended = recValue != null && Number.isFinite(+recValue) ? Math.round(+recValue * (rec.unit_days || 1)) : null;
  const needsRec = rec && out.recommended == null && ["recommendation", "shorter", "longer", "review"].includes(cfg.precedence);
  if (needsRec) out.needs.push({ input: rec.id, label: rec.label, kind: "number", roles: rec.roles, at: key });
  if (finding == null) return out;

  const step = (cfg.steps || []).find((s: any) => test(s.if, finding)
    && (!s.last || (s.last === "extended" ? st.lastChange === "extended" : st.lastChange !== "extended")));
  if (!step) {
    out.problem = `The approved workflow has no interval rule for the finding “${finding}”.`;
    return out;
  }
  const raw = step.set_days != null ? Number(step.set_days) : step.keep ? st.C : st.C + Number(step.add_days || 0);
  out.attempt = raw > st.C;
  let n = raw;
  if (n < cfg.min_days) { n = cfg.min_days; out.limitedBy = "min"; }
  if (n > cfg.max_days) { n = cfg.max_days; out.limitedBy = "max"; }
  const bp = cfg.breakpoint;
  if (bp && out.attempt && st.extensions >= bp.after_extensions && st.cap == null) {
    const active = st.history.filter((h) => test(cfg.active_if, h.finding)
      && (bp.count_activity_at !== "extended_intervals" || h.reachedBy === "extended")).map((h) => h.current);
    if (active.length) st.cap = Math.max(cfg.min_days, Math.max(...active) + Number(bp.cap_offset_days));
  }
  if (st.cap != null && n > st.cap) { n = st.cap; out.capApplied = st.cap; }
  out.calculated = n;

  const R = out.recommended;
  let final: number | null = null;
  switch (cfg.precedence) {
    case "calculated":
      final = n;
      if (R != null && R !== n) out.notes.push(`The ${rec.label.toLowerCase()} was ${weeks(R)}; the approved workflow books the calculated ${weeks(n)}.`);
      break;
    case "recommendation_if_given":
      if (R == null) {
        final = n; out.provisional = true;
        out.notes.push(`Provisional: the calculated ${weeks(n)} books the visit until the ${rec.label.toLowerCase()} is recorded.`);
      } else {
        final = R;
        if (R !== n) out.notes.push(`Booked on the ${rec.label.toLowerCase()} (${weeks(R)}); the calculated interval was ${weeks(n)}.`);
      }
      break;
    case "recommendation":
      if (R != null) {
        final = R;
        if (R !== n) out.notes.push(`Booked on the ${rec.label.toLowerCase()} (${weeks(R)}); the calculated interval was ${weeks(n)}.`);
      }
      break;
    case "shorter":
      if (R != null) final = Math.min(n, R);
      break;
    case "longer":
      if (R != null) final = Math.min(Math.max(n, R), st.cap ?? Infinity, cfg.max_days);
      break;
    case "review": {
      if (R == null) break;
      if (R === n) { final = n; break; }
      const choice = observed(part, key, CHOICE_INPUT);
      if (choice != null && (+choice === n || +choice === R)) {
        final = +choice;
        out.notes.push(`The investigator chose ${weeks(final)} (calculated ${weeks(n)}, ${rec.label.toLowerCase()} ${weeks(R)}).`);
      } else {
        out.needs.push({ input: CHOICE_INPUT, label: `Choose the interval: calculated ${weeks(n)} or ${rec.label.toLowerCase()} ${weeks(R)}`,
          kind: "choice", roles: ["pi"], at: key, options: [n, R] });
      }
      break;
    }
    default:
      out.problem = "The workflow does not say whether the calculated interval or the recommendation decides the booking.";
  }
  if (final == null) return out;
  if (final < cfg.min_days || final > cfg.max_days) out.notes.push(`${weeks(final)} is outside the protocol's ${weeks(cfg.min_days)} to ${weeks(cfg.max_days)}.`);
  out.interval = final;
  out.needs = [];
  st.history.push(out);
  st.extensions += out.attempt ? 1 : 0;
  st.lastChange = final > st.C ? "extended" : final < st.C ? "reduced" : "same";
  st.C = final;
  return out;
}

/** "8 weeks → 10 weeks (no activity)" style text for one decision. */
export function decisionText(cfg: any, d: AdaptiveDecision): string {
  if (d.interval == null) return d.problem || `Waiting for ${d.needs.map((n) => n.label.toLowerCase()).join(" and ")}.`;
  const parts = [`${weeks(d.current)} → ${weeks(d.interval)}`, `${cfg.finding.label}: ${d.finding}`];
  if (d.capApplied != null) parts.push(`capped at ${weeks(d.capApplied)} (historical limit)`);
  if (d.limitedBy) parts.push(`limited to the ${d.limitedBy === "min" ? "minimum" : "maximum"}`);
  return parts.join(" · ");
}

/** What staff may record at an occurrence of this visit: the finding, the recommendation and, under
 *  "review" precedence, the investigator's interval choice (backend adaptive_inputs, same rules). */
export function adaptiveInputs(model: any, visitId: string): { id: string; label: string; kind: string; roles: string[] }[] {
  const a = (model?.visits || []).find((v: any) => v.id === visitId)?.repeat?.adaptive;
  if (!a?.finding) return [];
  const out = [a.finding];
  if (a.recommendation) out.push({ ...a.recommendation, label: `${a.recommendation.label} (${a.recommendation.unit_days === 7 ? "weeks" : a.recommendation.unit_days === 1 ? "days" : `× ${a.recommendation.unit_days} days`})` });
  if (a.precedence === "review") out.push({ id: CHOICE_INPUT, label: "Interval chosen by the investigator (days)", kind: "number", roles: ["pi"] });
  return out;
}
