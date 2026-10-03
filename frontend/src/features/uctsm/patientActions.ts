/**
 * WP3 patient actions: pure request builders and display lines.
 *
 * Process transitions and assignment changes are PREVIEWED and only applied on
 * confirmation. Occurrence facts (booked / performed / missed) are recorded
 * directly and re-evaluated; a correction supersedes an earlier record, which
 * stays as history. Nothing here defaults an unstated value.
 */

export const PROCESS_STATES = [
  { value: "HELD", label: "Hold" },
  { value: "ACTIVE", label: "Resume" },
  { value: "STOPPED", label: "Stop" },
  { value: "COMPLETED", label: "Complete" },
] as const;

export const TRANSITION_REASONS = [
  { value: "DECISION", label: "Clinical decision" },
  { value: "WITHDRAWAL_FROM_TREATMENT", label: "Withdrawn from treatment" },
  { value: "WITHDRAWAL_OF_CONSENT", label: "Withdrew consent" },
  { value: "END_OF_FOLLOW_UP", label: "End of follow-up" },
  { value: "PROTOCOL_COMPLETION", label: "Protocol completed" },
] as const;

export const FACT_KINDS = [
  { value: "BOOKED", label: "Booked" },
  { value: "PERFORMED", label: "Performed" },
  { value: "MISSED", label: "Missed" },
] as const;

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const validDate = (value: string) => ISO.test(value.trim()) && !Number.isNaN(Date.parse(value.trim()));

export type TransitionForm = {
  /** A process code, or "PATIENT" for the whole participation. */
  target: string;
  toState: string;
  reason: string;
  effectiveAt: string;
  authority: string;
  note: string;
};

export function buildProcessTransitionRequest(form: TransitionForm, horizon: string) {
  if (!form.target) return { error: "Choose which process changes." };
  if (!form.toState) return { error: "Choose hold, resume, stop or complete." };
  if (!form.reason) return { error: "Choose why." };
  if (!validDate(form.effectiveAt)) return { error: "Enter the effective date as YYYY-MM-DD." };
  if (!form.note.trim()) return { error: "Add a note; it is recorded with the change." };
  const patientLevel = form.target === "PATIENT";
  if (patientLevel && form.toState !== "STOPPED") {
    return { error: "A whole-participation change can only stop participation." };
  }
  if (form.reason === "WITHDRAWAL_OF_CONSENT" && !patientLevel) {
    return { error: "Withdrawal of consent stops the whole participation; choose the patient." };
  }
  return {
    body: {
      horizon, reason: form.note.trim(),
      process_transition: {
        target: patientLevel ? { type: "PATIENT" } : { type: "PROCESS", process_code: form.target },
        to_state: form.toState, effective_at: form.effectiveAt.trim(), reason: form.reason,
        authority: form.authority.trim() || undefined,
      },
    },
  };
}

export type FactForm = {
  logicalKey: string;
  kind: string;
  value: string;
  reason: string;
  /** Set when correcting an earlier record of the same occurrence. */
  supersedesOccurrenceId?: string | null;
};

export function buildOccurrenceFactRequest(form: FactForm) {
  if (!form.logicalKey) return { error: "Choose the visit." };
  if (!form.kind) return { error: "Choose booked, performed or missed." };
  const needsDate = form.kind === "BOOKED" || form.kind === "PERFORMED";
  if (needsDate && !validDate(form.value)) return { error: "Enter the date as YYYY-MM-DD." };
  if (!needsDate && form.value.trim() && !validDate(form.value)) return { error: "Enter the date as YYYY-MM-DD." };
  if (!form.reason.trim()) return { error: "Add a reason; it is recorded with the fact." };
  const body: Record<string, unknown> = { logical_key: form.logicalKey, kind: form.kind, reason: form.reason.trim() };
  if (form.value.trim()) body.value = form.value.trim();
  if (form.supersedesOccurrenceId) body.supersedes_occurrence_id = form.supersedesOccurrenceId;
  return { body };
}

export type AssignmentForm = { dimension: string; value: string; effectiveFrom: string; reason: string };

export function buildAssignmentRequest(form: AssignmentForm, horizon: string) {
  if (!form.dimension) return { error: "Choose the group type." };
  if (!form.value.trim()) return { error: "Choose the new group." };
  if (form.effectiveFrom.trim() && !validDate(form.effectiveFrom)) return { error: "Enter the effective date as YYYY-MM-DD." };
  if (!form.reason.trim()) return { error: "Add a reason; it is recorded with the change." };
  return {
    body: {
      assignment: { [form.dimension]: form.value.trim() }, horizon, reason: form.reason.trim(),
      ...(form.effectiveFrom.trim() ? { assignment_effective_from: form.effectiveFrom.trim() } : {}),
    },
  };
}
