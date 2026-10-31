// Typed entry points for the Protocol Visit Scheduler kit engine. The engine files
// beside this one are a mechanical port of the kit and are intentionally untyped.
import { computeSchedule } from "./schedule";
import { serializeSchedule, condText, timingText, phaseGroup, valText, ruleIfText } from "./describe";

export { condText, timingText, phaseGroup, valText, ruleIfText };

export type KitOption = { value: string; label: string };
export type KitQuestion = {
  id: string; label: string; text: string; help: string; kind: "choice" | "boolean" | "number";
  options: KitOption[]; scope: "participant" | "study" | "decision" | "event"; default?: string | number | null;
};
export type KitReviewQuestion = {
  id: string; text: string; why: string; recommended: string | null; options: KitOption[];
};
/** "required" (default), "if_indicated" (if clinically indicated / per investigator) or "optional". */
export type KitProcedureNeed = "required" | "if_indicated" | "optional";
export type KitProcedure = {
  name: string; if: unknown[]; need?: KitProcedureNeed; reuse_within_days?: number | null; source?: string | null;
  /** This test's own limit before the visit: "pregnancy test within 3 days before dosing", "labs within 72 h". */
  within_before?: { days: number } | { hours: number };
};
export type KitVisitDefinition = {
  id: string; code: string; name: string; phase: string; modality: string; anchor: string; offset_days: number;
  window_before: number; window_after: number; follows: string | null; procedures: KitProcedure[];
  condition: string | null; source?: string | null; repeat: Record<string, any> | null; include_if: unknown[];
  duration_days: number | null; timepoints: unknown[];
  /** Check in at least `hours` before the dose at `visit` (same occurrence number): "check in at least 12 h before dosing". */
  arrive_before?: { visit: string; hours: number } | null;
  /** At least `days` after the previous occurrence, or after the latest `after` visit. */
  min_gap?: { days: number; after: string | null } | null;
  prep?: { fasting_hours: number | null; notes: string[] } | null;
  /** A yes/no question asked at every occurrence before it goes ahead ("ANC ≥ 1.0 × 10⁹/L?"). */
  check?: KitVisitCheck | null;
  /** Kept after participation ends but its date had passed by then: due that day ("or as soon as possible"). */
  asap_if_past?: boolean;
};
/**
 * "No" holds the occurrence until a "Yes" (re-checked every `recheck_days`); no "Yes" within
 * `max_wait_days` of the planned date warns, and with `on_timeout` "stop" stops this visit and
 * `stop_visits` from that deadline. Null values are ones the protocol doesn't state.
 */
export type KitVisitCheck = {
  id: string; text: string; help: string | null; recheck_days: number | null; max_wait_days: number | null;
  on_timeout: "stop" | "warn"; stop_visits: string[]; source: string | null;
  /** Values entered at this check; with `limits` the Yes/No is worked out from them. */
  measures?: string[]; limits?: KitLimit[];
  /** Dose level changes when the check is passed and their conditions hold. */
  dose_changes?: KitCheckDoseChange[];
  /** A dosing table, read top to bottom: the first row whose limits hold decides this occurrence. */
  actions?: KitCheckAction[];
};
/**
 * "give" goes ahead; "reduce" goes ahead at `percent` of the dose this time only (of `dose`, or every
 * drug given there when null); "skip" is not given and not made up (later dates stay); "hold" waits as for a No.
 */
export type KitCheckAction = { do: "give" | "reduce" | "skip" | "hold"; if: KitLimit[]; percent?: number; dose?: string | null; note: string | null };
/** A value entered at visit checks (ANC, creatinine, a toxicity grade 0-5). */
export type KitMeasure = {
  id: string; label: string; unit: string | null; kind: "value" | "grade"; worse: "lower" | "higher" | null; baseline: string | null;
};
/**
 * A limit on entered values: a number, or a multiple of the patient's baseline. `of` picks the value
 * entered now ("latest"), the worst entered for this visit ("worst_here") or for the patient ("worst_so_far").
 * `held_days` (only in dose-change conditions) is how many days the visit waited for a Yes.
 */
export type KitLimit =
  /** `for_days`: met by every value entered over at least that many days ("ANC < 500 lasting 7 days or more"). */
  | { measure: string; op: "<" | "<=" | ">" | ">="; value: number; of?: "latest" | "worst_here" | "worst_so_far"; for_days?: number }
  | { measure: string; op: "<" | "<=" | ">" | ">="; baseline_factor: number; of?: "latest" | "worst_here" | "worst_so_far"; for_days?: number }
  | { any: KitLimit[] }
  | { held_days: { op: "<" | "<=" | ">" | ">="; value: number } };
/** One dose-level change: a step (-1 = one level lower), a named level, or stopping the drug. */
export type KitDoseChange = { dose: string; step: number | null; to: string | null; stop: boolean };
export type KitCheckDoseChange = KitDoseChange & {
  if: KitLimit[]; occurrence: number | null; and_later: boolean; note: string | null;
};
/** Dose levels of one drug, highest first; `start` null when the protocol doesn't state it. */
export type KitDose = {
  /** `drop_visits`: at that level these visits of the drug are not given ("second reduction: omit Day 8"). */
  id: string; label: string; levels: { id: string; label: string; drop_visits?: string[] }[]; start: string | null; visits: string[];
  below_lowest: "stop" | "warn"; stop_visits: string[]; source: string | null;
};
/** answer "skip" / "reduce": staff chose that row of the check's dosing table by hand. */
export type KitCheckAnswer = { date: string; answer?: "yes" | "no" | "skip" | "reduce"; values?: Record<string, number> };
export type KitEvent = {
  id: string; label: string; repeatable: boolean; ends_schedule: boolean; note: string | null; sets_anchor?: string | null;
  /** Visits that still happen when this event ends the schedule (safety follow-up after the last dose). */
  keeps_visits?: string[];
  questions: KitQuestion[]; visits: KitVisitDefinition[];
};
/**
 * What happens to the schedule if something happens, as read from the protocol. It acts once its
 * date is known: an anchor's date (entered, or set by its visit) or the first logged event of a type.
 */
export type KitRule = {
  id: string; label: string; when: { anchor: string } | { event: string };
  stop_visits: string[]; stop_all: boolean; same_day: "keep" | "cancel"; starts: string | null; blocks: string[];
  allowed: { relative_to: string | null; min_days: number | null; max_days: number | null; not_on_visit: string | null } | null;
  effect: string; source: string | null;
  /** All must hold for the rule to act; branches of one event are rules told apart by these. */
  if?: KitRuleCondition[];
  /** Answers that change from the rule's date on (crossover to another arm). */
  set_answers?: Record<string, string> | null;
  /** Visits paused from the rule's date until the resume date; "shift" moves later ones, "skip" drops missed ones. */
  pause?: { visits: string[]; resume: string | null; mode: "shift" | "skip" } | null;
  /** With stop_all: visits that still happen. */
  keeps_visits?: string[];
  /** Dose level changes for doses after the rule's date (a grade 3 adverse event: one level lower). */
  dose_changes?: KitDoseChange[];
};
/** An answer condition: one of some values, a comparison (age >= 65), or "or" / "not" over others. */
export type KitCondition =
  | { question: string; in: string[] }
  | { question: string; op: "=" | "!=" | "<" | "<=" | ">" | ">="; value: string | number }
  | { any: KitCondition[] }
  | { not: KitCondition }
  /** What has happened by the visit's date: an event logged (before a visit or date, with `before`),
   *  or a visit done / missed / not done. */
  | { event: string; happened: boolean; before?: { visit: string; n: number | null } | { anchor: string } }
  | { visit: string; n: number | null; status: "done" | "missed" | "not_done" };
export type KitRuleCondition =
  | KitCondition
  | { event_question: string; in: string[] }
  | { timing: { anchor?: string; visit?: string; n?: number | null; min_days: number | null; max_days: number | null } }
  | { occurrence: number };
export type KitModel = {
  protocol: { title?: string; number?: string | null; sponsor?: string | null; phase?: string | null;
    indication?: string | null; version?: string | null; design?: string };
  arms: { id: string; name: string; description: string }[];
  anchors: { id: string; label: string; kind: "start" | "relative" | "ask" | "last_visit"; question?: string | null; optional?: boolean;
    /** "last_visit": the date of the last of these visits still on the schedule (last dose given). */
    last_of?: string[];
    /** "last_visit" with daily dosing between visits: a logged early stop (a rule ending the schedule) is the last dose. */
    stop_is_last_dose?: boolean;
    relative_to?: string | null; offset_days?: number | null; is_day1?: boolean; set_by_visit?: string | null;
    estimates?: { when?: unknown[]; relative_to: string; offset_days: number }[];
    /** Where the protocol allows this date, in days after another date ("within 28 days before Day 1"). */
    range?: { relative_to: string; min_days: number | null; max_days: number | null } | null }[];
  questions: KitQuestion[];
  visits: KitVisitDefinition[];
  events: KitEvent[];
  rules?: KitRule[];
  measures?: KitMeasure[];
  doses?: KitDose[];
  review_questions: KitReviewQuestion[];
  notes: string[];
  review?: string[] | null;
};
export type KitDetails = {
  protocol_number: string; title: string; phase: string; indications: string[]; sponsor: string;
  version: string; design: string;
};
export type KitEventEvidence = { page: number | null; quote: string; found: boolean | null; found_on?: number };
export type KitEventCheckItem = {
  id: string; label: string; status: "changes" | "no_schedule_change" | "not_in_protocol";
  summary: string; evidence: KitEventEvidence[]; checklist: string | null;
};
export type KitEventCheck = { ran: boolean; changes: string[]; events: KitEventCheckItem[] };
export type KitAnalysis = {
  baseModel: KitModel; model: KitModel; reviewAnswers: Record<string, string>;
  corrections: string[] | null; warnings: string[]; fileSha256?: string | null;
  /** The events check (backend app/kit/events.py): what logging each event changes, with the
   * protocol passages; `ran` false when it could not run. Also kept on the model as `event_check`. */
  eventCheck?: KitEventCheck | null;
  /** The analysis job, so saving can store the protocol file it read. */
  jobId?: string;
  counts: { review: number; participant: number; decisions: number; events: number; tablePages: number[] };
  details: KitDetails;
};
export type KitParticipant = {
  startAnchor: string; startDate: string; answers: Record<string, string>;
  events?: { key: string; type: string; date: string; answers: Record<string, string> }[];
  actuals?: Record<string, string>; doseTimes?: Record<string, string>;
  /** Answers to visit checks, per occurrence ("c1d1#3"), in date order. */
  checks?: Record<string, KitCheckAnswer[]>;
  /** Baseline values per measure, for limits relative to baseline. */
  baselines?: Record<string, number>;
  /** Dose levels staff set at a dose ("c1d1#3": { drug_x: "-1" }); later doses continue from it. */
  doseLevels?: Record<string, Record<string, string>>;
  /** "Today" for missed-visit conditions; defaults to the device's date. */
  today?: string;
};
/** `date`: the day a time point falls on when it is not the visit's day ("24 h post-dose"). */
export type KitTimepoint = { label: string; at: string; window: string; procedures: string[]; date?: string };
export type KitVisitRow = {
  type: "visit"; key: string; visitId: string; code: string; name: string; phase: string; phaseGroup: string;
  /** "skipped": not given and not made up (the check's dosing table, or staff). */
  modality: string; status: "done" | "waiting" | "scheduled" | "skipped"; date: string | null; planned: string | null;
  actual: string | null; relative: string | null; movesOthers: boolean;
  rule: string; windowStart: string | null; windowEnd: string | null; windowDays: [number, number];
  studyDay: number | null; endDay: string | null; weekend: boolean; nearestWeekday: string | null;
  dependsOnDecision: boolean; fromEvent: string | null; openEnded: boolean; until: string | null;
  procedures: {
    name: string; conditional: boolean; condition: string | null; need: KitProcedureNeed; reuseWithinDays: number | null;
    /** Its own limit before the visit, and the first date it may be done (null while the visit date isn't known). */
    withinBefore?: ({ days: number } | { hours: number }) & { from: string | null };
  }[];
  notes: string[]; timepoints: KitTimepoint[]; source: string | null;
  /** Labels of rules that would stop this visit once they happen (their date is not known yet). */
  stopsIf: string[];
  /** The visit's yes/no check for this occurrence: "ask" (no answer yet), "ok" (Yes), "held" (No,
   * waiting for a Yes) or "resumed" (No, then Yes; the Yes date is the visit date). */
  check?: {
    /** Also "skipped" (not given, not made up) and, for a repeat-until check, "again" (not met yet) and "met" (the last one). */
    id: string; text: string; help: string | null; state: "ask" | "ok" | "held" | "resumed" | "skipped" | "again" | "met";
    /** Each recorded answer; `result` is the Yes/No used ("values": worked out from the entered values,
     * "staff": answered by hand, null: values entered but the limits could not be worked out). */
    answers: (KitCheckAnswer & {
      result: "yes" | "no" | "skip" | null; by: "values" | "staff" | null; failed: string[];
      /** Only on checks with a dosing table: which row decided it. */
      outcome?: "give" | "reduce" | "skip" | "hold" | null; outcomeText?: string | null;
    })[];
    /** The dosing table in words, and what it decided here. */
    actions: { do: KitCheckAction["do"]; percent: number | null; text: string }[];
    until: boolean; skip: { date: string; why: string | null; by: string | null } | null;
    reduce: { percent: number; dose: string | null; why: string } | null;
    nextCheck: string | null; deadline: string | null; timedOut: boolean;
    delayDays: number; source: string | null;
    /** Values to enter at this check, and the limits in words ("ANC ≥ 1.0 × 10^9/L"). */
    measures: KitMeasure[]; limits: string[];
    /** Measures whose baseline is needed to work the limits out and is not entered yet. */
    needsBaseline: string[];
  } | null;
  /** The time staff recorded for this visit, and what it is: "checkin" (counts toward the dose that must
   * wait N hours after it) or "dose" (the dose time; PK clock times count from it). null: no time asked. */
  time: string | null;
  timeKind: "checkin" | "dose" | null;
  /** The dose level of each drug given at this occurrence. */
  doses: {
    dose: string; label: string; level: string | null; levelLabel: string | null; levelIndex: number | null;
    /** Why it changed at this dose ("ANC grade 4 during the hold"); "set by staff" for a manual level. */
    changed: string | null; manual: boolean; levels: { id: string; label: string }[];
    /** Below the starting level. */
    reduced: boolean;
    /** Percent of the dose given this time only (the check's dosing table), or null. */
    percent: number | null;
  }[];
};
export type KitDecisionRow = {
  type: "decision"; key: string; questionId: string; text: string; help: string | null; at: string;
  /** "number": answered with a value (eosinophils in × ULN), not from options. */
  kind: "choice" | "boolean" | "number";
  date: string | null; options: KitOption[]; answer: string | null;
};
export type KitSchedule = {
  anchors: { id: string; label: string; kind: string; date: string | null; how: string; setByEvent: string | null }[];
  rows: (KitVisitRow | KitDecisionRow)[];
  skipped: { id: string; code: string; name: string; onlyIf: string | null }[];
  stopped: { visit: string; count: number; at: string; date: string | null }[];
  ended: { event: string; date: string | null; cancelled: number } | null;
  /** Visits each rule cancelled, grouped by rule. */
  stoppedBy: { rule: string; label: string; effect: string | null; date: string | null; visits: { key: string; code: string; date: string | null }[] }[];
  /** A rule's date breaks the protocol's limits, or one rule rules out another. */
  warnings: string[];
  /** The same checks with their kind: "order" means the date is impossible and the rule was not applied. */
  issues: { rule: string; kind: "order" | "window" | "visit_day" | "blocked" | "info"; text: string }[];
  /** When each rule may happen, as dates (null where the protocol sets no limit or a date is not known yet). */
  ruleWindows: { rule: string; label: string; anchor: string | null; event: string | null; from: string | null; to: string | null; notOn: { code: string; date: string | null } | null }[];
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
