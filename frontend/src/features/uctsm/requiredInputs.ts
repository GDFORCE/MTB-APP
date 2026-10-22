/** Patient details a clinic must enter, as the protocol rules require them (P7/P10). Pure helpers, no I/O. */
export type InputKind = "DATE" | "DATETIME" | "TEXT" | "NUMBER";
export type RequiredInput = {
  code: string; label: string; kind: InputKind; source: "DERIVED" | "CUSTOM"; reason: string; anchor_code?: string | null;
};
export type RequiredInputsView = {
  schedule_version_id: string; status: string; list_hash: string;
  derived: RequiredInput[]; custom: RequiredInput[]; effective: RequiredInput[];
  acknowledged: boolean; needs_acknowledgement: boolean;
  acknowledgement: { reviewer_id: string; reason: string } | null;
};
export type EnrolmentInputs = { schedule_version_id: string; enforced: boolean; entries: RequiredInput[] };
/** India is the deployment region; the form pre-fills this zone, the server never assumes one. */
export const DEFAULT_OFFSET = "+05:30";
export const KIND_HELP: Record<InputKind, string> = {
  DATE: "A date, as YYYY-MM-DD.", DATETIME: "A date (YYYY-MM-DD) and a time (HH:MM, 24 hour).",
  TEXT: "Free text.", NUMBER: "A number.",
};

export const FROZEN = ["APPROVED", "SUPERSEDED", "ARCHIVED", "REJECTED"];
export const editableInputs = (status: string) => !FROZEN.includes(status);

export type CustomForm = { code: string; label: string; kind: InputKind; reason: string };
export const emptyCustomForm = (): CustomForm => ({ code: "", label: "", kind: "TEXT", reason: "" });

export function customBody(form: CustomForm) {
  const code = form.code.trim().toUpperCase().replace(/\s+/g, "_");
  if (!/^[A-Z][A-Z0-9_]{1,40}$/.test(code)) throw new Error("Use 2-41 capital letters, digits or underscores for the code.");
  if (form.label.trim().length < 2) throw new Error("Give the entry a label the clinic will see.");
  if (form.reason.trim().length < 3) throw new Error("Say why the protocol needs this detail.");
  return { code, label: form.label.trim(), kind: form.kind, reason: form.reason.trim() };
}

export function acknowledgeBody(view: RequiredInputsView, reason: string) {
  if (!editableInputs(view.status)) throw new Error("Approved versions are frozen.");
  if (reason.trim().length < 3) throw new Error("State that you reviewed the list.");
  return { reason: reason.trim() };
}

export function reviewNotice(view: RequiredInputsView): string {
  if (!view.effective.length) return "This protocol needs no extra patient details beyond the reference date.";
  const names = view.effective.map((item) => item.label).join(", ");
  return view.acknowledged
    ? `Reviewed. When a patient is added, the clinic must enter: ${names}.`
    : `When a patient is added, the clinic must enter: ${names}. Review and acknowledge this list before the schedule can be approved.`;
}

// ---- the enrolment side ------------------------------------------------------------------------------
export type EntryValue = { value: string; time: string; later: boolean };
export const emptyEntry = (): EntryValue => ({ value: "", time: "", later: false });

/** The exact string the server expects, or an Error describing what to fix. */
export function entryText(entry: RequiredInput, state: EntryValue, offset: string = DEFAULT_OFFSET): string {
  const value = state.value.trim();
  if (entry.kind === "DATE") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) throw new Error(`${entry.label}: enter a date as YYYY-MM-DD.`);
    return value;
  }
  if (entry.kind === "DATETIME") {
    const time = state.time.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) throw new Error(`${entry.label}: enter the date as YYYY-MM-DD.`);
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error(`${entry.label}: enter the time as HH:MM (24 hour).`);
    return `${value}T${time}:00${offset}`;
  }
  if (entry.kind === "NUMBER") {
    if (!value || !Number.isFinite(Number(value))) throw new Error(`${entry.label}: enter a number.`);
    return value;
  }
  if (!value || value.length > 500) throw new Error(`${entry.label}: enter some text (up to 500 characters).`);
  return value;
}

/** Everything the enrol request needs; an entry is either entered or explicitly deferred, never silently skipped. */
export function buildEnrolmentInputs(entries: RequiredInput[], values: Record<string, EntryValue>, offset: string = DEFAULT_OFFSET) {
  const inputs: Record<string, string> = {};
  const deferred: string[] = [];
  const problems: string[] = [];
  for (const entry of entries) {
    const state = values[entry.code] ?? emptyEntry();
    if (state.later) { deferred.push(entry.code); continue; }
    try { inputs[entry.code] = entryText(entry, state, offset); } catch (failure) { problems.push((failure as Error).message); }
  }
  if (problems.length) throw new Error(problems.join(" "));
  return { inputs, deferred_inputs: deferred };
}

export function enrolmentReady(entries: RequiredInput[], values: Record<string, EntryValue>): boolean {
  try { buildEnrolmentInputs(entries, values); return true; } catch { return false; }
}
