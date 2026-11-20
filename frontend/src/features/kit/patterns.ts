// Pattern handlers: small pieces of code that calculate protocol rules the kit model's fields
// cannot hold ("every calendar month on the same day", "within 3 business days", "the first Monday
// on or after Day 210"). The AI writes such a rule as a pattern rule with a name (model.pattern_rules);
// the backend (app/kit/patterns.py) checks its params and marks it `blocked` when no handler exists
// or its params cannot be used. The engine (schedule.ts) applies only rules that are not blocked and
// whose name is in PATTERN_HANDLERS below.
//
// The names here and in backend/app/kit/patterns.py PATTERNS must be the same set: the backend
// decides what is blocked (and so what may be enrolled), this file does the dates. Both are checked
// by tests/kit-patterns.test.ts. How to add one: docs/kit-pattern-handlers.md.
import { addD, DAY, dow, isoD, parseD } from "./utils";
import { evaluateCalc, calcOutputProblem, type CalcFacts } from "./calc";

export type KitPatternRule = {
  id: string; pattern: string; label: string; visits: string[]; params: Record<string, any>;
  quote: string; page: number | null; source?: string; quote_found?: boolean;
  /** Why the app cannot calculate it yet: "no_handler" or what is wrong with its params. Absent = calculated. */
  blocked?: string;
  /** Ticked by the reviewer in Add Trial (calculated rules only). */
  checked?: boolean;
  /** A calculation the analysis generated for a rule no handler covers (backend app/kit/calc.py). */
  program?: unknown; output?: { kind: "occurrences" | "date" }; inputs?: { fact: string; id?: string }[];
  tests?: unknown[];
  /** Set by the backend when the program is valid and passes its own tests: version, content hash, results. */
  generated?: { calc_version: number; sha256: string; tests: { name: string; passed: boolean }[]; reads: string[] };
};

/** The blockers as the backend reports them (analysis result, saved model doc, admin list). */
export type KitBlocker = {
  rule_id: string; pattern: string; label: string; quote: string; page: number | null; quote_found?: boolean | null;
  visits: string[]; reason: "no_handler" | "params"; problem: string | null; has_handler: boolean;
};

/** What a handler is given for one occurrence of a visit. Dates are UTC-midnight timestamps. */
export type PatternCtx = {
  /** 0-based occurrence index. */
  n: number;
  /** What the engine counted this occurrence from: the visit's anchor (first occurrence), the visit
   * it follows, or the previous occurrence of a repeat. */
  kind: "anchor" | "follows" | "repeat";
  /** That date (null while unknown). */
  base: number | null;
  /** The visit's anchor date, for fixed grids. */
  anchor: number | null;
  /** The date the engine (or an earlier pattern rule) gave this occurrence. */
  date: number | null;
  /** Whether a repeat counts from the previous occurrence (repeat.chain). */
  chain: boolean;
  /** What a person calls the date it counts from ("Day 1", "M2"), and the visit's anchor. */
  fromLabel: string;
  anchorLabel: string;
  /** The occurrence number, and what has happened so far, for generated calculations. */
  occurrence?: number;
  facts?: () => CalcFacts;
};

export type PatternResult = {
  /** The occurrence's date; null = cannot be worked out (say why in `pending`). */
  date: number | null;
  windowBefore?: number; windowAfter?: number;
  /** How the date was worked out, shown instead of "Day 1 + 30 d". */
  rel?: string;
  /** Shown to staff once per rule, as a schedule note. */
  note?: string;
  /** Why there is no date. */
  pending?: string;
};

export type PatternHandler = { title: string; date: (params: Record<string, any>, ctx: PatternCtx) => PatternResult };

// ── date helpers ─────────────────────────────────────────────────────────
const daysInMonth = (y: number, m: number) => new Date(Date.UTC(y, m + 1, 0)).getUTCDate();

/** The same day of the month, k calendar months later. A day the month does not have
 * (31 Jan + 1 month) follows the protocol's `missing` rule; "not_stated" gives no date. */
export function addMonths(t: number, k: number, missing: string): number | null {
  const d = new Date(t), y = d.getUTCFullYear(), m = d.getUTCMonth() + k, day = d.getUTCDate();
  const last = daysInMonth(y, m);
  if (day <= last) return Date.UTC(y, m, day);
  if (missing === "last_day_of_month") return Date.UTC(y, m, last);
  if (missing === "first_day_of_next_month") return Date.UTC(y, m + 1, 1);
  return null;
}
const isBusiness = (t: number) => dow(t) !== 0 && dow(t) !== 6;
/** n business days (Monday to Friday) after t. */
export function addBusinessDays(t: number, n: number): number {
  let x = t, left = n;
  while (left > 0) { x = addD(x, 1); if (isBusiness(x)) left--; }
  return x;
}
const WEEKDAY = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
const intOf = (x: any): number | null => (Number.isInteger(x) ? x : null);

// ── the handlers ─────────────────────────────────────────────────────────
export const PATTERN_HANDLERS: Record<string, PatternHandler> = {
  CALENDAR_MONTHS: {
    title: "Calendar months (same day of the month)",
    date(p, c) {
      const months = intOf(p.months), every = intOf(p.every_months) ?? 0;
      if (months == null) return { date: c.date };
      let from = c.kind === "repeat" && !c.chain ? c.anchor : c.base, k = months;
      if (c.kind === "repeat") k = c.chain ? every : months + c.n * every;
      if (from == null) return { date: null };
      const date = addMonths(from, k, p.missing_day);
      const rel = `${c.kind === "repeat" && !c.chain ? c.anchorLabel : c.fromLabel} + ${plural(k, "calendar month")}`;
      if (date == null) {
        const d = new Date(from);
        return { date: null, rel, pending: `The protocol does not say which day to use when a month has no day ${d.getUTCDate()}. Enter the date when it is booked.` };
      }
      return { date, rel };
    },
  },
  BUSINESS_DAYS: {
    title: "Business days",
    date(p, c) {
      const n = intOf(p.business_days);
      if (n == null || n < 1) return { date: c.date };
      if (c.kind === "repeat") return { date: c.date }; // the backend does not allow it; nothing to change
      if (c.base == null) return { date: null };
      const date = addBusinessDays(c.base, n);
      const out: PatternResult = { date, rel: `${c.fromLabel} + ${plural(n, "business day")}` };
      if (p.within) {
        out.windowBefore = Math.round((date - addBusinessDays(c.base, 1)) / DAY);
        out.windowAfter = 0;
        out.rel = `within ${plural(n, "business day")} after ${c.fromLabel}`;
      }
      if (p.excludes_holidays) out.note = "Public holidays also do not count, and the app does not know them: check the date against the local holiday calendar.";
      return out;
    },
  },
  WEEKDAY: {
    title: "Moved to a weekday",
    date(p, c) {
      const want = WEEKDAY.indexOf(String(p.weekday || "").toLowerCase());
      if (want < 0 || c.date == null) return { date: c.date };
      const after = p.direction !== "on_or_before";
      let shift = after ? (want - dow(c.date) + 7) % 7 : -((dow(c.date) - want + 7) % 7);
      const name = WEEKDAY[want][0].toUpperCase() + WEEKDAY[want].slice(1);
      return { date: addD(c.date, shift), rel: `the first ${name} ${after ? "on or after" : "on or before"} the planned day` };
    },
  },
};

/** Whether something calculates this rule: a handler, or a generated calculation the backend validated. */
export const isCalculated = (r: KitPatternRule) => !r.blocked && (!!PATTERN_HANDLERS[r.pattern] || (!!r.generated && r.program != null));

/** Pattern rules the engine applies to existing occurrences, by visit id, in model order. A generated
 *  calculation that produces a visit's occurrences is applied separately (generatedOccurrenceRules). */
export function activePatternRules(model: { pattern_rules?: KitPatternRule[] } | null | undefined): Record<string, KitPatternRule[]> {
  const out: Record<string, KitPatternRule[]> = {};
  for (const r of model?.pattern_rules || []) {
    if (!isCalculated(r) || (!PATTERN_HANDLERS[r.pattern] && r.output?.kind !== "date")) continue;
    for (const vid of r.visits || []) (out[vid] = out[vid] || []).push(r);
  }
  return out;
}

/** Generated calculations that produce all the occurrences of one visit, by that visit's id. */
export function generatedOccurrenceRules(model: { pattern_rules?: KitPatternRule[] } | null | undefined): Record<string, KitPatternRule> {
  const out: Record<string, KitPatternRule> = {};
  for (const r of model?.pattern_rules || []) {
    if (isCalculated(r) && !PATTERN_HANDLERS[r.pattern] && r.output?.kind === "occurrences" && r.visits?.length === 1) out[r.visits[0]] = r;
  }
  return out;
}

/** The visits a generated calculation reads, so the engine dates them first. */
export function generatedInputs(r: KitPatternRule): string[] {
  return (r.inputs || []).filter((x) => x.fact === "visit" && x.id).map((x) => x.id as string);
}

/** The pattern rules nothing calculates yet. */
export function blockedPatternRules(model: { pattern_rules?: KitPatternRule[] } | null | undefined): KitPatternRule[] {
  return (model?.pattern_rules || []).filter((r) => !isCalculated(r));
}

/** A generated "date" calculation for one occurrence: its result in the handlers' shape. */
function generatedDate(r: KitPatternRule, c: PatternCtx): PatternResult {
  const iso = (t: number | null) => (t == null ? null : isoD(t));
  try {
    const out = evaluateCalc(r.program, c.facts ? c.facts() : {}, {
      n: c.occurrence ?? c.n + 1, date: iso(c.date), base: iso(c.base), anchor: iso(c.anchor) });
    const problem = calcOutputProblem("date", out);
    if (problem) return { date: null, pending: `The generated calculation “${r.label}” gave no usable result: ${problem}.` };
    const item = typeof out === "string" ? { date: out } : out;
    return {
      date: item.date ? parseD(item.date) : null, rel: r.label,
      ...(item.window_before != null ? { windowBefore: item.window_before } : {}),
      ...(item.window_after != null ? { windowAfter: item.window_after } : {}),
      ...(item.pending ? { pending: String(item.pending) } : {}), ...(item.note ? { note: String(item.note) } : {}),
    };
  } catch (error: any) {
    return { date: null, pending: `The generated calculation “${r.label}” failed: ${error?.message || error}.` };
  }
}

/** Run a visit's pattern rules for one occurrence, each from the date the previous one gave. */
export function applyPatternRules(rules: KitPatternRule[], ctx: PatternCtx): PatternResult & { used: KitPatternRule[]; notes: { rule: KitPatternRule; text: string }[] } {
  let res: PatternResult = { date: ctx.date };
  const used: KitPatternRule[] = [], notes: { rule: KitPatternRule; text: string }[] = [];
  for (const r of rules) {
    const h = PATTERN_HANDLERS[r.pattern] || (r.program != null ? { title: r.label, date: (_p: any, c: PatternCtx) => generatedDate(r, c) } : null);
    if (!h) continue;
    const next = h.date(r.params || {}, { ...ctx, date: res.date });
    res = { ...res, ...next, windowBefore: next.windowBefore ?? res.windowBefore, windowAfter: next.windowAfter ?? res.windowAfter,
      rel: next.rel ?? res.rel, pending: next.pending ?? (next.date == null ? res.pending : undefined) };
    used.push(r);
    if (next.note) notes.push({ rule: r, text: next.note });
    if (res.date == null) break;
  }
  return { ...res, used, notes };
}

/** Plain-words reason a blocked rule waits, for staff screens. */
export function blockedReason(r: { blocked?: string | null; pattern: string; problem?: string | null; reason?: string }): string {
  const why = r.problem ?? (r.blocked && r.blocked !== "no_handler" ? r.blocked : null);
  if (why) return `The app has a calculation for ${r.pattern}, but this rule cannot use it: ${why}.`;
  return `The app has no calculation for ${r.pattern} yet. A developer needs to add one.`;
}
