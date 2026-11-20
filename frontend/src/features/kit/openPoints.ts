// Open points: what the reviewer must check against the protocol because the model cannot be
// trusted on it as it stands (backend app/kit/normalize.py, "open points"). Pure helpers; the list
// itself is in OpenPoints.tsx.
import type { KitModel, KitOpenPoint } from "./index";
import { blockedReason, isCalculated, PATTERN_HANDLERS } from "./patterns";

/** Open point kinds, plus the model's pattern rules: "blocked" (nothing calculates it yet, so no
 * patient can be enrolled) and "pattern" (calculated by a named rule; check it like any other). */
export type PointKind = KitOpenPoint["kind"] | "blocked" | "pattern" | "generated";

export const POINT_KIND_LABEL: Record<PointKind, string> = {
  blocked: "The app cannot calculate this yet: patients can't be enrolled until it can",
  pattern: "Calculated by a protocol rule",
  generated: "A calculation the app wrote from the protocol: check its logic and tests before approval",
  conflict: "The protocol says two different things",
  ambiguous: "The protocol can be read two ways",
  cannot_represent: "The app cannot calculate this",
  approximated: "Calculated only approximately",
  not_stated: "The protocol does not say",
  cleanup: "Changed or left out when the AI's reading was checked",
  unverified: "Protocol passage not found",
};
// Most likely to make a schedule wrong first.
const ORDER: PointKind[] = ["blocked", "generated", "conflict", "cannot_represent", "approximated", "ambiguous", "cleanup", "not_stated", "unverified", "pattern"];

export type OpenPointView = {
  id: string; kind: PointKind; kindLabel: string; text: string; used: string | null; why: string | null;
  quote: string | null;
  /** "p. 12", "p. 12 (the app could not find this passage in the PDF text)", or null. */
  page: string | null;
  /** Names of the visits, dates, events or rules it concerns. */
  affects: string[]; visitIds: string[]; checked: boolean;
  /** false: ticking it off would not change anything (a blocked rule waits for code, not a reviewer). */
  tickable: boolean;
};

/** Every id a point may name -> what a person calls it, and whether it is a visit. */
function names(model: KitModel) {
  const out = new Map<string, { label: string; visit: boolean }>();
  for (const a of model.anchors) out.set(a.id, { label: a.label, visit: false });
  for (const e of model.events) {
    out.set(e.id, { label: e.label, visit: false });
    for (const v of e.visits) out.set(v.id, { label: v.code || v.name, visit: true });
  }
  for (const r of model.rules || []) out.set(r.id, { label: r.label, visit: false });
  for (const v of model.visits) out.set(v.id, { label: v.code || v.name, visit: true });
  return out;
}

/** The model's open points in the order to read them, with ids turned into names. */
export function openPointViews(model: KitModel | null | undefined, checked: Record<string, boolean> = {}): OpenPointView[] {
  const rules = (model?.pattern_rules || []).map((r) => {
    const blocked = !isCalculated(r);
    return {
      id: `pattern_${r.id}`, kind: blocked ? "blocked" : "pattern", text: r.label, quote: r.quote, page: r.page,
      found: r.quote_found, affects: r.visits, checked: (r as any).checked,
      why: blocked ? blockedReason(r) : PATTERN_HANDLERS[r.pattern] ? `${r.pattern}: worked out by the app's calculation for this kind of rule.`
        : `${r.pattern}: worked out by a calculation the app generated from the protocol (it passes its ${r.generated?.tests.length ?? 0} test cases).`,
      used: blocked ? "These visits are shown without this rule. Nobody can be enrolled until it is supported." : "",
    } as unknown as KitOpenPoint;
  });
  const points = [...(model?.open_points || []), ...rules];
  if (!model || !points.length) return [];
  const byId = names(model);
  return points.map((p, index) => {
    const known = (p.affects || []).filter((id) => byId.has(id));
    const page = p.page == null ? null
      : p.found === false ? `p. ${p.page} (the app could not find this passage in the PDF text)`
        : `p. ${p.found_on ?? p.page}`;
    return {
      index, view: {
        id: p.id, kind: p.kind, kindLabel: POINT_KIND_LABEL[p.kind] || POINT_KIND_LABEL.cannot_represent, text: p.text,
        used: p.used || null, why: p.why || null, quote: p.quote || null,
        page: page ?? (p.quote && p.found === false ? "The app could not find this passage in the PDF text" : null),
        affects: [...new Set(known.map((id) => byId.get(id)!.label))],
        visitIds: known.filter((id) => byId.get(id)!.visit),
        checked: (p.kind as PointKind) !== "blocked" && (checked[p.id] ?? !!p.checked),
        tickable: (p.kind as PointKind) !== "blocked",
      },
    };
  }).sort((a, b) => ORDER.indexOf(a.view.kind) - ORDER.indexOf(b.view.kind) || a.index - b.index).map((x) => x.view);
}

/** How many open points concern each visit, for the "to check" tag on the timeline. */
export function openPointsByVisit(model: KitModel | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of openPointViews(model)) for (const id of p.visitIds) out[id] = (out[id] || 0) + 1;
  return out;
}

/** The base model with the reviewer's ticks stored on its open points. */
export function withCheckedPoints<T extends KitModel>(model: T, checked: Record<string, boolean>): T {
  if ((!model.open_points?.length && !model.pattern_rules?.length) || !Object.keys(checked).length) return model;
  const out = { ...model };
  if (model.open_points) out.open_points = model.open_points.map((p) => (p.id in checked ? { ...p, checked: !!checked[p.id] } : p));
  // A calculated pattern rule the reviewer checked (blocked ones cannot be ticked off).
  if (model.pattern_rules) out.pattern_rules = model.pattern_rules.map((r) => (`pattern_${r.id}` in checked && !r.blocked
    ? { ...r, checked: !!checked[`pattern_${r.id}`] } : r));
  return out;
}
