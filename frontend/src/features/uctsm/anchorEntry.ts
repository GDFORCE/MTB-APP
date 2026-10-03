/**
 * PT-01 at enrolment: every extra anchor date the site enters is explicitly
 * PLANNED or ACTUAL. A planned surgery date must never be sent as if the
 * surgery had happened, so there is no default - an entered date without a
 * choice blocks the form until the user says which it is.
 */

export type AnchorDateSource = "PLANNED" | "ACTUAL";

export const ANCHOR_SOURCE_CHOICES: { value: AnchorDateSource; label: string; hint: string }[] = [
  { value: "PLANNED", label: "Planned", hint: "Expected date; visits that need the real date keep waiting" },
  { value: "ACTUAL", label: "Actual", hint: "The event has already happened on this date" },
];

/** The sources to send for the dates that will be sent, and the codes still missing a choice. */
export function anchorDateSources(
  sentDates: Record<string, string>,
  chosen: Record<string, AnchorDateSource | undefined>,
): { sources: Record<string, AnchorDateSource>; missing: string[] } {
  const sources: Record<string, AnchorDateSource> = {};
  const missing: string[] = [];
  for (const code of Object.keys(sentDates)) {
    const value = chosen[code];
    if (value === "PLANNED" || value === "ACTUAL") sources[code] = value;
    else missing.push(code);
  }
  return { sources, missing };
}
