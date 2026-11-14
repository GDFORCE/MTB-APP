// Open points: what the reviewer must check against the protocol because the model cannot be
// trusted on it as it stands (backend app/kit/normalize.py, "open points"). Pure helpers; the list
// itself is in OpenPoints.tsx.
import type { KitModel, KitOpenPoint } from "./index";

export const POINT_KIND_LABEL: Record<KitOpenPoint["kind"], string> = {
  conflict: "The protocol says two different things",
  ambiguous: "The protocol can be read two ways",
  cannot_represent: "The app cannot calculate this",
  approximated: "Calculated only approximately",
  not_stated: "The protocol does not say",
  cleanup: "Changed or left out when the AI's reading was checked",
  unverified: "Protocol passage not found",
};
// Most likely to make a schedule wrong first.
const ORDER: KitOpenPoint["kind"][] = ["conflict", "cannot_represent", "approximated", "ambiguous", "cleanup", "not_stated", "unverified"];

export type OpenPointView = {
  id: string; kind: KitOpenPoint["kind"]; kindLabel: string; text: string; used: string | null; why: string | null;
  quote: string | null;
  /** "p. 12", "p. 12 (the app could not find this passage in the PDF text)", or null. */
  page: string | null;
  /** Names of the visits, dates, events or rules it concerns. */
  affects: string[]; visitIds: string[]; checked: boolean;
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
  const points = model?.open_points || [];
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
        checked: checked[p.id] ?? !!p.checked,
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
  if (!model.open_points?.length || !Object.keys(checked).length) return model;
  return { ...model, open_points: model.open_points.map((p) => (p.id in checked ? { ...p, checked: !!checked[p.id] } : p)) };
}
