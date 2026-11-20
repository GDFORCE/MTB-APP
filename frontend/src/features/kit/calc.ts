// Generated calculations ("calc v1"): protocol-specific logic the analysis writes as data, evaluated
// here by a fixed interpreter with a step budget (no code is ever executed). Same semantics as
// backend/app/kit/calc.py, which validates each calculation and runs its test cases before it can
// be approved; both are checked against tests/fixtures shared vectors. The language is described in
// calc.py. Dates inside the interpreter are CDate values; results come out as plain JSON with
// "YYYY-MM-DD" dates.

const MAX_STEPS = 200_000;
const MAX_LIST = 500;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const DAY = 864e5;

export class CalcError extends Error {}

class CDate {
  readonly t: number;
  constructor(t: number) { this.t = t; }
  iso() { return new Date(this.t).toISOString().slice(0, 10); }
}

const toDate = (x: any): CDate | null => {
  if (x == null) return null;
  if (x instanceof CDate) return x;
  if (typeof x === "string" && ISO.test(x)) {
    const [y, m, d] = x.split("-").map(Number);
    return new CDate(Date.UTC(y, m - 1, d));
  }
  throw new CalcError(`expected a date, got ${JSON.stringify(x)}`);
};
const toNum = (x: any): number | null => {
  if (x == null) return null;
  if (typeof x !== "number" || !Number.isFinite(x)) throw new CalcError(`expected a number, got ${JSON.stringify(x)}`);
  return x;
};
const plain = (v: any): any => {
  if (v instanceof CDate) return v.iso();
  if (Array.isArray(v)) return v.map(plain);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, plain(x)]));
  return v;
};
const same = (a: any, b: any) => JSON.stringify(plain(a)) === JSON.stringify(plain(b));
const textOf = (x: any) => (x == null ? "" : x instanceof CDate ? x.iso() : typeof x === "boolean" ? String(x) : String(x));

function compare(op: string, a: any, b: any): boolean {
  if (a == null || b == null) {
    const both = a == null && b == null;
    return op === "==" ? both : op === "!=" ? !both : false;
  }
  if (typeof a === "boolean" || typeof b === "boolean") {
    if (op === "==" || op === "!=") return (a === b) === (op === "==");
    throw new CalcError("cannot order yes/no values");
  }
  if (a instanceof CDate || b instanceof CDate) { a = toDate(a)!.t; b = toDate(b)!.t; }
  else if (typeof a === "object" || typeof b === "object") {
    if (op === "==" || op === "!=") return same(a, b) === (op === "==");
    throw new CalcError("cannot order records or lists");
  }
  if (op === "==") return a === b;
  if (op === "!=") return a !== b;
  if (typeof a !== typeof b) throw new CalcError(`cannot compare ${JSON.stringify(a)} with ${JSON.stringify(b)}`);
  return op === "<" ? a < b : op === "<=" ? a <= b : op === ">" ? a > b : a >= b;
}

export type CalcFacts = {
  visits?: Record<string, any[]>; anchors?: Record<string, string | null>; answers?: Record<string, any>;
  events?: Record<string, string[]>; today?: string | null;
};

/** The program's result as plain JSON. Throws CalcError. */
export function evaluateCalc(program: any, facts: CalcFacts, self?: Record<string, any> | null): any {
  let steps = 0;
  const fact = (kind: string, id: any): any => {
    if (kind === "today") return toDate(facts.today ?? null);
    if (kind === "visit") return (facts.visits?.[id] || []).map((o) => ({
      ...o, planned: toDate(o.planned ?? null), actual: toDate(o.actual ?? null),
      window_start: toDate(o.window_start ?? null), window_end: toDate(o.window_end ?? null),
    }));
    if (kind === "anchor") return toDate(facts.anchors?.[id] ?? null);
    if (kind === "answer") return facts.answers?.[id] ?? null;
    if (kind === "event_dates") return (facts.events?.[id] || []).map((x) => toDate(x)!).sort((a, b) => a.t - b.t);
    throw new CalcError(`unknown fact ${kind}`);
  };
  const op = (name: string, args: any[], env: Record<string, any>): any => {
    if (name === "if") return ev(ev(args[0], env) === true ? args[1] : args[2], env);
    if (name === "and") return args.every((a) => ev(a, env) === true);
    if (name === "or") return args.some((a) => ev(a, env) === true);
    const v = args.map((a) => ev(a, env));
    switch (name) {
      case "not": return v[0] !== true;
      case "is_null": return v[0] == null;
      case "coalesce": return v.find((x) => x != null) ?? null;
      case "==": case "!=": case "<": case "<=": case ">": case ">=": return compare(name, v[0], v[1]);
      case "count": return (v[0] || []).length;
      case "first": return (v[0] || [])[0] ?? null;
      case "last": { const l = v[0] || []; return l.length ? l[l.length - 1] : null; }
      case "nth": { const l = v[0] || [], i = toNum(v[1]); return i != null && i >= 1 && i <= l.length ? l[Math.trunc(i) - 1] : null; }
      case "text": return v.map(textOf).join("");
      default: break;
    }
    if (v.some((x) => x == null)) return null;
    switch (name) {
      case "add_days": {
        const n = toNum(v[1])!;
        if (!Number.isInteger(n)) throw new CalcError("add_days needs a whole number of days");
        return new CDate(toDate(v[0])!.t + n * DAY);
      }
      case "days_between": return Math.round((toDate(v[1])!.t - toDate(v[0])!.t) / DAY);
      case "weekday": return new Date(toDate(v[0])!.t).getUTCDay();
      case "min": case "max": {
        const dates = v.some((x) => x instanceof CDate || typeof x === "string");
        const vals = v.map((x) => (dates ? toDate(x)!.t : toNum(x)!));
        const m = name === "min" ? Math.min(...vals) : Math.max(...vals);
        return dates ? new CDate(m) : m;
      }
      default: break;
    }
    const n = v.map((x) => toNum(x)!);
    switch (name) {
      case "+": return n.reduce((a, b) => a + b, 0);
      case "*": return n.reduce((a, b) => a * b, 1);
      case "-": return n[0] - n[1];
      case "/": if (n[1] === 0) throw new CalcError("division by zero"); return n[0] / n[1];
      case "%": if (n[1] === 0) throw new CalcError("division by zero"); return n[0] - n[1] * Math.floor(n[0] / n[1]);
      case "floor": return Math.floor(n[0]);
      default: throw new CalcError(`unknown operation ${name}`);
    }
  };
  const ev = (node: any, env: Record<string, any>): any => {
    if (++steps > MAX_STEPS) throw new CalcError("the calculation took too many steps");
    if (node === null || typeof node === "boolean" || typeof node === "number") return node;
    if (typeof node !== "object" || Array.isArray(node)) throw new CalcError("not an expression");
    if ("str" in node) return node.str;
    if ("date" in node && Object.keys(node).length === 1) return toDate(node.date);
    if ("var" in node) {
      const [name, ...path] = String(node.var).split(".");
      if (!(name in env)) throw new CalcError(`“${node.var}” is not defined`);
      let value = env[name];
      for (const p of path) value = value && typeof value === "object" && !(value instanceof CDate) ? value[p] ?? null : null;
      return value ?? null;
    }
    if ("fact" in node) return fact(node.fact, node.id);
    if ("op" in node) return op(node.op, node.args || [], env);
    if ("map" in node || "filter" in node) {
      const items = ev("map" in node ? node.map : node.filter, env);
      if (items == null) return [];
      if (!Array.isArray(items)) throw new CalcError("map/filter needs a list");
      const out = "map" in node ? items.map((x) => ev(node.do, { ...env, [node.as]: x }))
        : items.filter((x) => ev(node.where, { ...env, [node.as]: x }) === true);
      if (out.length > MAX_LIST) throw new CalcError("a list is too long");
      return out;
    }
    if ("let" in node) {
      const inner = { ...env };
      for (const [name, expr] of Object.entries(node.let)) inner[name] = ev(expr, inner);
      return ev(node.in, inner);
    }
    if ("record" in node) return Object.fromEntries(Object.entries(node.record).map(([k, x]) => [k, ev(x, env)]));
    throw new CalcError("unknown expression");
  };
  const env: Record<string, any> = {};
  if (self) env.this = Object.fromEntries(Object.entries(self).map(([k, x]) => [k, ["date", "base", "anchor"].includes(k) ? toDate(x) : x]));
  return plain(ev(program, env));
}

/** Why a result does not have the declared shape, or null. */
export function calcOutputProblem(kind: string, out: any): string | null {
  const one = (item: any, where: string): string | null => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return `${where} is not a record`;
    if (item.date == null && !item.pending) return `${where} has no date and no reason (pending)`;
    if (item.date != null && !(typeof item.date === "string" && ISO.test(item.date))) return `${where} has a date that is not a date`;
    for (const k of ["window_before", "window_after"]) {
      if (item[k] != null && (typeof item[k] !== "number" || item[k] < 0)) return `${where} has an unusable ${k}`;
    }
    return null;
  };
  if (kind === "date") return typeof out === "string" && ISO.test(out) ? null : one(out, "the result");
  if (!Array.isArray(out)) return "the result is not a list of occurrences";
  const seen = new Set<number>();
  for (let i = 0; i < out.length; i++) {
    const p = one(out[i], `occurrence ${i + 1}`);
    if (p) return p;
    const n = out[i].n;
    if (!Number.isInteger(n) || n < 1 || seen.has(n)) return `occurrence ${i + 1} needs its own whole number n (1 or more)`;
    seen.add(n);
  }
  return null;
}
