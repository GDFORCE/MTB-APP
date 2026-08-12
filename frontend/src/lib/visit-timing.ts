export type VisitTiming = {
  day_offset?: number | null;
  day_end?: number | null;
  hour_offset?: number | null;
  hour_end?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
  relative_to?: string | null;
  relative_offset_days?: number | null;
  source_day_label?: string | null;
  // Compatibility with early extraction payloads that used this name. New
  // writes use source_day_label, which is the backend's canonical field.
  source_timing_label?: string | null;
};

export type VisitWindow = {
  window_days?: number | null;
  window_before?: number | null;
  window_after?: number | null;
};

const cleanLabel = (value?: string | null) => value?.trim() || "";

/**
 * Human-readable protocol timing. The stored offset is calendar arithmetic,
 * not a study-day label: offset 0 means the baseline date and may represent
 * protocol Day 0 or Day 1. Prefer the protocol's exact source label whenever
 * it is available; otherwise use an explicit baseline-relative description.
 */
export function formatVisitTiming(
  timing: VisitTiming,
  unspecifiedLabel = "Timing not specified",
): string {
  const sourceLabel = cleanLabel(timing.source_day_label)
    || cleanLabel(timing.source_timing_label);
  if (sourceLabel) return sourceLabel;

  const hourOffset = timing.hour_offset;
  const hasHourTiming = typeof hourOffset === "number"
    && Number.isFinite(hourOffset)
    && (
      hourOffset !== 0
      || typeof timing.hour_end === "number"
      || timing.hour_offset_basis != null
    );
  if (hasHourTiming) {
    if (timing.hour_offset_basis === "absolute") {
      return formatAbsoluteHourRange(hourOffset, timing.hour_end);
    }
    if (typeof timing.day_offset !== "number" || !Number.isFinite(timing.day_offset)) {
      return unspecifiedLabel;
    }
    const day = formatBaselineOffset(timing.day_offset, unspecifiedLabel);
    return `${day} ${formatHourRange(hourOffset, timing.hour_end)}`;
  }

  const relativeTo = cleanLabel(timing.relative_to);
  if (
    relativeTo
    && (typeof timing.day_offset !== "number" || !Number.isFinite(timing.day_offset))
  ) {
    const gap = timing.relative_offset_days;
    if (typeof gap !== "number" || !Number.isFinite(gap)) return `Relative to ${relativeTo}`;
    if (gap === 0) return `At ${relativeTo}`;
    return gap > 0
      ? `${gap} ${gap === 1 ? "day" : "days"} after ${relativeTo}`
      : `${Math.abs(gap)} ${gap === -1 ? "day" : "days"} before ${relativeTo}`;
  }

  const start = formatBaselineOffset(timing.day_offset, unspecifiedLabel);
  if (
    typeof timing.day_offset === "number"
    && typeof timing.day_end === "number"
    && Number.isFinite(timing.day_end)
    && timing.day_end !== timing.day_offset
  ) {
    return `${start} to ${formatBaselineOffset(timing.day_end, unspecifiedLabel)}`;
  }
  return start;
}

const signedNumber = (value: number) => value >= 0 ? `+${value}` : String(value);

const hourUnit = (value: number) => Math.abs(value) === 1 ? "hour" : "hours";

function formatHourRange(start: number, end?: number | null): string {
  if (typeof end === "number" && Number.isFinite(end) && end !== start) {
    return `${signedNumber(start)} to ${signedNumber(end)} hours`;
  }
  return `${signedNumber(start)} ${hourUnit(start)}`;
}

function formatAbsoluteHourRange(start: number, end?: number | null): string {
  if (typeof end === "number" && Number.isFinite(end) && end !== start) {
    return `Baseline ${signedNumber(start)} to ${signedNumber(end)} hours`;
  }
  return `Baseline ${signedNumber(start)} ${hourUnit(start)}`;
}

export function formatBaselineOffset(
  offset?: number | null,
  unspecifiedLabel = "Timing not specified",
): string {
  if (typeof offset !== "number" || !Number.isFinite(offset)) return unspecifiedLabel;
  if (offset === 0) return "Baseline";
  return offset > 0
    ? `Baseline +${offset} ${offset === 1 ? "day" : "days"}`
    : `Baseline ${offset} ${offset === -1 ? "day" : "days"}`;
}

export function parseOptionalDayOffset(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isInteger(parsed) ? parsed : null;
}

export function formatVisitWindow(window: VisitWindow, compact = false): string {
  const symmetric = typeof window.window_days === "number" && Number.isFinite(window.window_days)
    ? Math.max(0, window.window_days)
    : 0;
  const hasAsymmetricWindow = typeof window.window_before === "number"
    || typeof window.window_after === "number";
  if (hasAsymmetricWindow) {
    const before = typeof window.window_before === "number" ? window.window_before : symmetric;
    const after = typeof window.window_after === "number" ? window.window_after : symmetric;
    return `-${before}${compact ? "d" : " days"} / +${after}${compact ? "d" : " days"}`;
  }
  return `±${symmetric}${compact ? "d" : ` ${symmetric === 1 ? "day" : "days"}`}`;
}

/**
 * Format the calendar date encoded at the start of an ISO value without
 * allowing the device timezone to move it to the previous/next day.
 */
export function formatIsoCalendarDate(
  value?: string | null,
  fallback = "Date not available",
): string {
  const match = value?.trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])/);
  if (!match) return fallback;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return fallback;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
