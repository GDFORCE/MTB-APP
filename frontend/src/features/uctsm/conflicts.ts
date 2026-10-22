/** A conflicting or ambiguous reading is settled only by naming the protocol text that supports it. */
export type ConflictEvidence = {
  evidence_id: string; type: string; page: number | null; section: string | null; table: string | null;
  row: string | null; column: string | null; quote: string;
};
export type Conflict = {
  event_id: string; event_code: string; display_name: string; status: "AMBIGUOUS" | "CONFLICTING" | "UNRESOLVED";
  evidence: ConflictEvidence[]; missing_evidence_ids: string[]; resolvable: boolean;
};
export type ConflictForm = { chosen: string[]; reading: string; reason: string };
export const emptyConflictForm = (): ConflictForm => ({ chosen: [], reading: "", reason: "" });

export function evidenceLocation(item: ConflictEvidence): string {
  const parts = [item.page ? `Page ${item.page}` : "Page not stated", item.section || "Section not stated"];
  if (item.table) parts.push(`Table ${item.table}`);
  if (item.row) parts.push(`Row ${item.row}`);
  if (item.column) parts.push(`Column ${item.column}`);
  return parts.join(" · ");
}

/** The request body for the resolve route. It carries no actor, status or date: the server decides those. */
export function resolveConflictBody(conflict: Conflict, form: ConflictForm) {
  if (!conflict.resolvable)
    throw new Error("This visit has no quoted protocol text to confirm. Review the source before it can be resolved.");
  const chosen = [...new Set(form.chosen)];
  if (!chosen.length) throw new Error("Choose the protocol text that supports the confirmed reading.");
  if (chosen.some((id) => !conflict.evidence.some((item) => item.evidence_id === id && item.quote.trim())))
    throw new Error("Choose evidence that belongs to this visit and quotes the protocol.");
  if (form.reading.trim().length < 3) throw new Error("State the reading you are confirming.");
  if (form.reason.trim().length < 3) throw new Error("State why this reading is supported.");
  return { chosen_evidence_ids: chosen, confirmed_reading: form.reading.trim(), reason: form.reason.trim() };
}

export function conflictSummary(conflicts: Conflict[]): string {
  if (!conflicts.length) return "No unresolved interpretations.";
  const blocked = conflicts.filter((item) => !item.resolvable).length;
  return `${conflicts.length} unresolved ${conflicts.length === 1 ? "interpretation" : "interpretations"}`
    + (blocked ? `, ${blocked} without quoted protocol text` : "") + ". Approval stays blocked until each is resolved.";
}
