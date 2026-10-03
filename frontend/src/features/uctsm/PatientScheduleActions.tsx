import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import {
  previewAssignmentImpact, previewProcessTransition, recordOccurrenceFact, type ScheduleImpact,
} from "./api";
import {
  FACT_KINDS, PROCESS_STATES, TRANSITION_REASONS, buildAssignmentRequest,
  buildOccurrenceFactRequest, buildProcessTransitionRequest,
} from "./patientActions";

type Option = { code: string; label: string };
type VisitOption = { logicalKey: string; label: string; latestOccurrenceId: string | null };
type Mode = "process" | "fact" | "assignment";

const errorText = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item: any) => item?.msg).filter(Boolean).join("; ") || fallback;
  return detail?.message || error?.message || fallback;
};

/**
 * WP3 site actions. A process change or group change is PREVIEWED here and
 * handed to the screen's existing Schedule Impact card, where it is confirmed
 * or cancelled. A booked / performed / missed fact is recorded directly (the
 * backend re-evaluates); a correction supersedes the newest record.
 */
export function PatientScheduleActions({ patientId, horizon, processes, visits, dimensions, disabled, onProposal, onRecorded }: {
  patientId: string;
  horizon: string;
  processes: Option[];
  visits: VisitOption[];
  dimensions: Record<string, Option[]>;
  disabled: boolean;
  onProposal: (proposal: ScheduleImpact, label: string) => void;
  onRecorded: (message: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<Mode>("process");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [transition, setTransition] = useState({ target: "", toState: "", reason: "DECISION", effectiveAt: "", authority: "", note: "" });
  const [fact, setFact] = useState({ logicalKey: "", kind: "", value: "", reason: "", correct: false });
  const [assignment, setAssignment] = useState({ dimension: Object.keys(dimensions)[0] || "", value: "", effectiveFrom: "", reason: "" });

  const run = async (action: () => Promise<void>) => {
    if (busy || disabled) return;
    setBusy(true); setError("");
    try { await action(); } catch (next) { setError(errorText(next, "The action could not be completed.")); }
    finally { setBusy(false); }
  };

  const previewTransition = () => {
    const built = buildProcessTransitionRequest(transition, horizon);
    if (built.error) return setError(built.error);
    void run(async () => {
      const proposal = await previewProcessTransition(patientId, built.body!);
      const who = transition.target === "PATIENT" ? "participation" : (processes.find((p) => p.code === transition.target)?.label || transition.target);
      const verb = PROCESS_STATES.find((s) => s.value === transition.toState)?.label.toLowerCase();
      onProposal(proposal, `If ${who} is set to ${verb} from ${transition.effectiveAt}: `);
      setTransition((current) => ({ ...current, note: "" }));
    });
  };
  const saveFact = () => {
    const visit = visits.find((item) => item.logicalKey === fact.logicalKey);
    const built = buildOccurrenceFactRequest({
      ...fact, supersedesOccurrenceId: fact.correct ? visit?.latestOccurrenceId : null,
    });
    if (built.error) return setError(built.error);
    if (fact.correct && !visit?.latestOccurrenceId) return setError("This visit has no earlier record to correct.");
    void run(async () => {
      await recordOccurrenceFact(patientId, built.body!);
      setFact({ logicalKey: "", kind: "", value: "", reason: "", correct: false });
      await onRecorded(`${fact.correct ? "Correction" : "Record"} saved; the schedule was re-evaluated.`);
    });
  };
  const previewAssignment = () => {
    const built = buildAssignmentRequest(assignment, horizon);
    if (built.error) return setError(built.error);
    void run(async () => {
      const body = built.body!;
      const proposal = await previewAssignmentImpact(
        patientId, body.assignment as Record<string, string>, horizon, body.reason as string,
        assignment.effectiveFrom.trim() || undefined);
      onProposal(proposal, `If ${assignment.dimension.toLowerCase()} changes to ${assignment.value}`
        + `${assignment.effectiveFrom ? ` from ${assignment.effectiveFrom}` : ""}: `);
    });
  };

  return <Card style={{ gap: 10 }}>
    <Body weight="700">Record a change</Body>
    <View style={styles.chips} accessibilityRole="tablist">
      {([["process", "Process hold / stop"], ["fact", "Visit booked / done / missed"], ["assignment", "Group change"]] as [Mode, string][])
        .map(([key, label]) => <Chip key={key} label={label} selected={mode === key} role="tab" onPress={() => { setMode(key); setError(""); }} />)}
    </View>

    {mode === "process" && <>
      <Small>Changes only the chosen process; other treatments and follow-up continue. You will see the impact before anything is applied.</Small>
      <Label text="Which process" />
      <View style={styles.chips}>
        {processes.map((item) => <Chip key={item.code} label={item.label} selected={transition.target === item.code}
          onPress={() => setTransition((c) => ({ ...c, target: item.code }))} />)}
        <Chip label="Whole participation" selected={transition.target === "PATIENT"}
          onPress={() => setTransition((c) => ({ ...c, target: "PATIENT", toState: "STOPPED" }))} />
      </View>
      {!processes.length && <Small>This protocol version defines no separate processes; only whole-participation changes apply.</Small>}
      <Label text="Change" />
      <View style={styles.chips}>
        {PROCESS_STATES.map((item) => <Chip key={item.value} label={item.label} selected={transition.toState === item.value}
          onPress={() => setTransition((c) => ({ ...c, toState: item.value }))} />)}
      </View>
      <Label text="Why" />
      <View style={styles.chips}>
        {TRANSITION_REASONS.map((item) => <Chip key={item.value} label={item.label} selected={transition.reason === item.value}
          onPress={() => setTransition((c) => ({ ...c, reason: item.value }))} />)}
      </View>
      <Input label="Effective date (YYYY-MM-DD)" value={transition.effectiveAt} onChange={(effectiveAt) => setTransition((c) => ({ ...c, effectiveAt }))} />
      <Input label="Decided by (optional)" value={transition.authority} onChange={(authority) => setTransition((c) => ({ ...c, authority }))} />
      <Input label="Note (recorded)" value={transition.note} onChange={(note) => setTransition((c) => ({ ...c, note }))} />
      <Button onPress={previewTransition} loading={busy} disabled={busy || disabled}>Preview schedule impact</Button>
    </>}

    {mode === "fact" && <>
      <Small>A booking never changes the protocol rule; a date outside the permitted window is flagged. A missed session keeps its number.</Small>
      <Label text="Visit" />
      <View style={styles.chips}>
        {visits.map((item) => <Chip key={item.logicalKey} label={item.label} selected={fact.logicalKey === item.logicalKey}
          onPress={() => setFact((c) => ({ ...c, logicalKey: item.logicalKey }))} />)}
      </View>
      <Label text="What happened" />
      <View style={styles.chips}>
        {FACT_KINDS.map((item) => <Chip key={item.value} label={item.label} selected={fact.kind === item.value}
          onPress={() => setFact((c) => ({ ...c, kind: item.value }))} />)}
      </View>
      <Input label={fact.kind === "MISSED" ? "Date (optional, YYYY-MM-DD)" : "Date (YYYY-MM-DD)"} value={fact.value}
        onChange={(value) => setFact((c) => ({ ...c, value }))} />
      <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: fact.correct }}
        onPress={() => setFact((c) => ({ ...c, correct: !c.correct }))} style={styles.toggle}>
        <Text style={styles.toggleText}>{fact.correct ? "☑" : "☐"} This corrects the latest record for this visit</Text>
      </Pressable>
      <Input label="Reason (recorded)" value={fact.reason} onChange={(reason) => setFact((c) => ({ ...c, reason }))} />
      <Button onPress={saveFact} loading={busy} disabled={busy || disabled}>{fact.correct ? "Save correction" : "Save"}</Button>
    </>}

    {mode === "assignment" && <>
      <Small>Earlier visits keep the group the patient was in; the new group applies from the effective date. You will see the impact first.</Small>
      <Label text="Group type" />
      <View style={styles.chips}>
        {Object.keys(dimensions).map((key) => <Chip key={key} label={key.toLowerCase()} selected={assignment.dimension === key}
          onPress={() => setAssignment((c) => ({ ...c, dimension: key, value: "" }))} />)}
      </View>
      {!Object.keys(dimensions).length && <Small>This protocol version defines no groups.</Small>}
      <View style={styles.chips}>
        {(dimensions[assignment.dimension] || []).map((item) => <Chip key={item.code} label={item.label} selected={assignment.value === item.code}
          onPress={() => setAssignment((c) => ({ ...c, value: item.code }))} />)}
      </View>
      <Input label="Effective from (optional, YYYY-MM-DD)" value={assignment.effectiveFrom}
        onChange={(effectiveFrom) => setAssignment((c) => ({ ...c, effectiveFrom }))} />
      <Input label="Reason (recorded)" value={assignment.reason} onChange={(reason) => setAssignment((c) => ({ ...c, reason }))} />
      <Button onPress={previewAssignment} loading={busy} disabled={busy || disabled || !Object.keys(dimensions).length}>Preview schedule impact</Button>
    </>}
    {disabled && <Small>Confirm or cancel the pending schedule impact first.</Small>}
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
  </Card>;
}

function Chip({ label, selected, onPress, role = "radio" }: { label: string; selected: boolean; onPress: () => void; role?: "radio" | "tab" }) {
  return <Pressable accessibilityRole={role} accessibilityState={{ selected }} accessibilityLabel={label} onPress={onPress}
    style={[styles.chip, selected && styles.chipSelected]}>
    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{selected ? "✓ " : ""}{label}</Text>
  </Pressable>;
}

function Label({ text }: { text: string }) {
  return <Text style={styles.label}>{text}</Text>;
}

function Input({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <View style={{ gap: 4 }}>
    <Text style={styles.label}>{label}</Text>
    <TextInput accessibilityLabel={label} value={value} onChangeText={onChange} style={styles.input}
      placeholderTextColor={colors.mutedFg} />
  </View>;
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingHorizontal: 12, paddingVertical: 8, minHeight: 40, justifyContent: "center", borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  chipSelected: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground },
  chipTextSelected: { color: colors.primaryFg },
  label: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 12 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground, minHeight: 44 },
  toggle: { minHeight: 44, justifyContent: "center" },
  toggleText: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 13 },
  error: { color: colors.destructive, fontFamily: fonts.semibold, fontSize: 13 },
});
