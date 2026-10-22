/** Manual handling changes review policy; it never supplies a protocol date. */
export type Handling = {
  kind: "UNSUPPORTED" | "MANUAL_ONLY"; status: "PENDING" | "APPROVED";
  reason: string; owner_id: string; evidence_refs: string[]; policy_reference: string;
  requested_by: string; approved_by: string | null;
};
export type RuleView = {
  schedule_version_id: string; revision: number; status: string;
  events: { code: string; display_name: string; handling: Handling | null; evidence_refs: string[] }[];
  evidence: { id: string; page_number?: number | null; section_title?: string | null;
    source_text?: string | null }[];
  issues: { issue_code: string; message: string; blocking: boolean; details?: { field_path?: string } }[];
};
export type ManualForm = {
  kind: Handling["kind"] | null; reason: string; evidence: string[]; policy: string; responsible: boolean;
};
export const emptyManualForm = (): ManualForm => ({ kind: null, reason: "", evidence: [], policy: "", responsible: false });
export const editableRules = (status: string) => !["APPROVED", "SUPERSEDED", "ARCHIVED"].includes(status);
export function reviewFailureMessage(failure: unknown): string {
  const response = (failure as { response?: { status?: number; headers?: Record<string, unknown>;
    data?: { detail?: unknown } } } | null)?.response;
  const status = response?.status ?? 0;
  if (status === 403) {
    const code = response?.headers?.["x-authz-code"] ?? response?.headers?.["X-Authz-Code"];
    return code === "MAKER_CHECKER"
      ? "Maker-checker: the person who made the last material change cannot approve this version. Ask another authorised approver."
      : "You do not have permission to review or change this schedule. Ask an authorised reviewer.";
  }
  if (status === 404) return "That schedule was not found for you.";
  if (status === 409) return "The draft changed since you loaded it. Reload and review it again.";
  if (status === 422 && typeof response?.data?.detail === "string") return response.data.detail.slice(0, 2000);
  if (!status || status >= 500) return "The current schedule could not be loaded or saved. Reload to check its state before trying again.";
  return "The review could not be saved. Check your inputs and reload the current schedule.";
}
export function canHandleRules(mine: { capabilities: Record<string, string>;
  trial_scoped_grants: { capability: string; trial_id: string }[] } | null, trial: string, capability: string) {
  return !!mine && (Object.hasOwn(mine.capabilities, capability)
    || mine.trial_scoped_grants.some((grant) => grant.trial_id === trial && grant.capability === capability));
}
export function declareRuleBody(view: RuleView, eventCode: string, form: ManualForm) {
  if (!editableRules(view.status)) throw new Error("Approved versions are immutable; create a new draft.");
  if (!view.events.some((event) => event.code === eventCode)) throw new Error("Choose a visit in this version.");
  if (!form.kind) throw new Error("Choose manual-only handling or unsupported.");
  if (form.reason.trim().length < 3 || form.policy.trim().length < 3)
    throw new Error("State the reason and the permitted workflow or policy reference.");
  const evidence = [...new Set(form.evidence)];
  if (!evidence.length || evidence.some((id) => !view.evidence.some((source) => source.id === id)))
    throw new Error("Choose source evidence belonging to this version.");
  if (!form.responsible) throw new Error("Explicitly accept responsibility for this handling request.");
  return { schedule_version_id: view.schedule_version_id, expected_revision: view.revision,
    event_code: eventCode, kind: form.kind, reason: form.reason.trim(), evidence_refs: evidence,
    policy_reference: form.policy.trim(), accepts_responsibility: true as const };
}
export function approveRuleBody(view: RuleView, eventCode: string, reason: string) {
  const event = view.events.find((row) => row.code === eventCode);
  if (!editableRules(view.status) || event?.handling?.kind !== "MANUAL_ONLY" || event.handling.status !== "PENDING")
    throw new Error("Choose a pending manual-only request in a draft.");
  if (reason.trim().length < 3) throw new Error("State your independent review reason.");
  return { schedule_version_id: view.schedule_version_id, expected_revision: view.revision,
    event_code: eventCode, reason: reason.trim() };
}
