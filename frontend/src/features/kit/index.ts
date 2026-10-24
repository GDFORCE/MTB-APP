// Typed entry points for the Protocol Visit Scheduler kit engine. The engine files
// beside this one are a mechanical port of the kit and are intentionally untyped.
import { computeSchedule } from "./schedule";
import { serializeSchedule, condText, timingText, phaseGroup, valText } from "./describe";

export { condText, timingText, phaseGroup, valText };

export type KitOption = { value: string; label: string };
export type KitQuestion = {
  id: string; label: string; text: string; help: string; kind: "choice" | "boolean" | "number";
  options: KitOption[]; scope: "participant" | "study" | "decision" | "event"; default?: string | number | null;
};
export type KitReviewQuestion = {
  id: string; text: string; why: string; recommended: string | null; options: KitOption[];
};
export type KitProcedure = { name: string; if: unknown[] };
export type KitVisitDefinition = {
  id: string; code: string; name: string; phase: string; modality: string; anchor: string; offset_days: number;
  window_before: number; window_after: number; follows: string | null; procedures: KitProcedure[];
  condition: string | null; source?: string | null; repeat: Record<string, any> | null; include_if: unknown[];
  duration_days: number | null; timepoints: unknown[];
};
export type KitEvent = {
  id: string; label: string; repeatable: boolean; ends_schedule: boolean; note: string | null;
  questions: KitQuestion[]; visits: KitVisitDefinition[];
};
export type KitModel = {
  protocol: { title?: string; number?: string | null; sponsor?: string | null; phase?: string | null;
    indication?: string | null; version?: string | null; design?: string };
  arms: { id: string; name: string; description: string }[];
  anchors: { id: string; label: string; kind: "start" | "relative" | "ask"; question?: string | null; optional?: boolean }[];
  questions: KitQuestion[];
  visits: KitVisitDefinition[];
  events: KitEvent[];
  review_questions: KitReviewQuestion[];
  notes: string[];
  review?: string[] | null;
};
export type KitDetails = {
  protocol_number: string; title: string; phase: string; indications: string[]; sponsor: string;
  version: string; design: string;
};
export type KitAnalysis = {
  baseModel: KitModel; model: KitModel; reviewAnswers: Record<string, string>;
  corrections: string[] | null; warnings: string[];
  counts: { review: number; participant: number; decisions: number; events: number; tablePages: number[] };
  details: KitDetails;
};
export type KitParticipant = {
  startAnchor: string; startDate: string; answers: Record<string, string>;
  events?: { key: string; type: string; date: string; answers: Record<string, string> }[];
  actuals?: Record<string, string>; doseTimes?: Record<string, string>;
};
export type KitTimepoint = { label: string; at: string; window: string; procedures: string[] };
export type KitVisitRow = {
  type: "visit"; key: string; visitId: string; code: string; name: string; phase: string; phaseGroup: string;
  modality: string; status: "done" | "waiting" | "scheduled"; date: string | null; planned: string | null;
  actual: string | null; relative: string | null; movesOthers: boolean;
  rule: string; windowStart: string | null; windowEnd: string | null; windowDays: [number, number];
  studyDay: number | null; endDay: string | null; weekend: boolean; nearestWeekday: string | null;
  dependsOnDecision: boolean; fromEvent: string | null; openEnded: boolean; until: string | null;
  procedures: { name: string; conditional: boolean; condition: string | null }[];
  notes: string[]; timepoints: KitTimepoint[]; source: string | null;
};
export type KitDecisionRow = {
  type: "decision"; key: string; questionId: string; text: string; help: string | null; at: string;
  date: string | null; options: KitOption[]; answer: string | null;
};
export type KitSchedule = {
  anchors: { id: string; label: string; kind: string; date: string | null; how: string; setByEvent: string | null }[];
  rows: (KitVisitRow | KitDecisionRow)[];
  skipped: { id: string; code: string; name: string; onlyIf: string | null }[];
  stopped: { visit: string; count: number; at: string; date: string | null }[];
  ended: { event: string; date: string | null; cancelled: number } | null;
};

/** Model + site settings + one participant's answers -> the dated schedule. */
export function computeKitSchedule(
  model: KitModel, siteAnswers: Record<string, string>, participant: KitParticipant,
): KitSchedule {
  const part = { events: [], actuals: {}, doseTimes: {}, ...participant };
  return serializeSchedule(model, computeSchedule(model, siteAnswers, part), part);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2026-11-02" -> { day: "02", month: "Nov", weekday: "Mon", year: "2026" }, without Intl. */
export function isoParts(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return {
    day: String(d).padStart(2, "0"), month: MONTHS[m - 1], year: String(y),
    weekday: WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()],
  };
}

export const isIsoDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)
  && !Number.isNaN(Date.parse(value + "T00:00:00Z"));

export function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
