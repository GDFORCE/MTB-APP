import React, { useEffect, useMemo, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import {
  cancelPatientCommand, confirmPatientCommand, getPlaneCalendar, listPatientCommands,
  previewPatientCommand,
} from "./api";
import { createControllerBinding, type CommandApi, type Snapshot } from "./commandController.ts";
import {
  PLANE_MESSAGES, buildCommandPreview, describeRow, orderForConfirmation,
  type CommandForm, type CommandType, type ImpactRow,
} from "./patientCommands";

const realApi: CommandApi = {
  calendar: getPlaneCalendar, history: listPatientCommands, preview: previewPatientCommand,
  confirm: confirmPatientCommand, cancel: cancelPatientCommand,
};

const TYPES: { type: CommandType; label: string; needs: string[] }[] = [
  { type: "RECORD_ACTUAL", label: "Record visit / treatment", needs: ["visit", "visitDate", "treatmentDate"] },
  { type: "MARK_MISSED", label: "Mark missed", needs: ["visit"] },
  { type: "CREATE_UNSCHEDULED", label: "Unscheduled visit", needs: ["eventCode", "visitDate"] },
  { type: "RESCHEDULE_APPOINTMENT", label: "Reschedule appointment", needs: ["visit", "newDate"] },
  { type: "OVERRIDE_OCCURRENCE", label: "Patient-specific date / window",
    needs: ["visit", "targetDate", "windowEarliest", "windowLatest", "authority"] },
  { type: "CANCEL_OCCURRENCE", label: "Cancel future visit", needs: ["visit"] },
  { type: "PAUSE_TRACK", label: "Pause track", needs: ["track"] },
  { type: "RESUME_TRACK", label: "Resume track", needs: ["track", "resumePolicy"] },
  { type: "REBASE_TRACK", label: "Rebase track", needs: ["track", "anchorCode", "newValue"] },
  { type: "STOP_TRACK", label: "Stop track", needs: ["track"] },
];

const FIELD_LABEL: Record<string, string> = {
  visit: "Visit (e.g. IMAGING_BASELINE:0)", visitDate: "Visit date (YYYY-MM-DD)",
  treatmentDate: "Treatment date (YYYY-MM-DD)", eventCode: "Unscheduled visit code",
  newDate: "New appointment date", targetDate: "Exception date", windowEarliest: "Exception window start",
  windowLatest: "Exception window end", authority: "Authorised by", track: "Track code",
  resumePolicy: "Resume policy (only if the protocol leaves it to you)", anchorCode: "Anchor code",
  newValue: "New anchor date",
};

/**
 * P5 patient schedule commands on the MongoDB patient plane. The panel appears
 * only when THIS patient exists in that plane (the calendar probe answers); it
 * never enrols or moves anyone. The preview is the server's calculation - the
 * same one the confirmation runs - and the result is re-read after confirming.
 */
export function PatientCommandsPanel({
  trialId, externalPatientId, scheduleVersionId, displayPlane = "SQL", horizon, onChanged,
}: {
  trialId?: string; externalPatientId?: string | null; scheduleVersionId?: string | null;
  /** Which store the host screen reads its schedule from. */
  displayPlane?: "SQL" | "MONGO";
  horizon: string; onChanged?: () => void;
}) {
  // The lifecycle rules (stale responses, resets, retry keys, committed-but-
  // refresh-failed) live in the tested state machine; this component renders it.
  const [snapshot, setSnapshot] = useState<Snapshot>({
    state: "checking", calendar: [], history: [], preview: null, busy: false, error: "", notice: "",
    committedNeedsRefresh: false,
  });
  const [form, setForm] = useState<CommandForm>({ type: "RECORD_ACTUAL", effectiveAt: "", reason: "" });
  const [localError, setLocalError] = useState("");
  const binding = useRef(createControllerBinding(realApi));
  const committed = useRef(0);
  const control = binding.current;

  // setup -> cleanup -> setup always leaves a LIVE controller (see the binding)
  useEffect(() => control.mount((next) => {
    setSnapshot(next);
    if (next.notice.startsWith("Recorded") || next.notice.startsWith("Already")) committed.current += 1;
  }), []);
  useEffect(() => {
    setForm({ type: "RECORD_ACTUAL", effectiveAt: "", reason: "" });
    setLocalError("");
    control.setIdentity({ trialId, externalPatientId, scheduleVersionId, displayPlane });
  }, [trialId, externalPatientId, scheduleVersionId, displayPlane]);
  useEffect(() => { if (committed.current) onChanged?.(); }, [committed.current]);

  const { state, history, preview, busy, notice } = snapshot;
  const error = localError || snapshot.error;
  const calendar = snapshot.calendar;

  const needs = TYPES.find((item) => item.type === form.type)?.needs || [];
  const set = (key: keyof CommandForm, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const fieldKey = (name: string): keyof CommandForm => ({
    visit: "logicalKey", track: "trackCode",
  } as Record<string, keyof CommandForm>)[name] || (name as keyof CommandForm);

  const doPreview = () => {
    const built = buildCommandPreview(form, horizon);
    if ("error" in built) return setLocalError(built.error);
    setLocalError("");
    const body = { ...built.body };
    if (form.type === "OVERRIDE_OCCURRENCE") body.authority = form.authority;
    void control.preview(body);
  };
  const doConfirm = () => void control.confirm();
  const doCancel = () => void control.discard();
  const reverse = (commandId: string) => {
    const next: CommandForm = {
      type: "REVERSE", reversesCommandId: commandId, effectiveAt: form.effectiveAt, reason: form.reason };
    setForm(next);
    const built = buildCommandPreview(next, horizon);
    if ("error" in built) return setLocalError(built.error);
    setLocalError("");
    void control.preview(built.body);
  };

  const rows = useMemo(
    () => orderForConfirmation((preview?.changes || []) as ImpactRow[]), [preview]);

  if (state === "checking") return null;
  if (state !== "ready") {
    return <Card><Body weight="700">Schedule changes</Body>
      <Small>{PLANE_MESSAGES[state]}</Small>
      {state === "unavailable" && <Pressable accessibilityRole="button" onPress={() => void control.retry()}>
        <Text style={styles.link}>Try again</Text></Pressable>}
    </Card>;
  }

  return (
    <Card>
      <Body weight="700">Schedule changes</Body>
      <Small>Protocol target and window, the appointment or exception, and what actually happened are
        recorded and shown separately. Preview first; nothing is saved until you confirm.</Small>
      <View style={styles.chips}>
        {TYPES.map((item) => (
          <Pressable key={item.type} accessibilityRole="button" onPress={() => { setForm({ ...form, type: item.type }); void control.discard(); }}
            style={[styles.chip, form.type === item.type && styles.chipOn]}>
            <Text style={styles.chipText}>{item.label}</Text>
          </Pressable>))}
      </View>
      {needs.map((name) => (
        <TextInput key={name} accessibilityLabel={FIELD_LABEL[name]} placeholder={FIELD_LABEL[name]}
          placeholderTextColor={colors.mutedFg} style={styles.input}
          value={String((form as any)[fieldKey(name)] ?? "")}
          onChangeText={(value) => set(fieldKey(name), value)} />))}
      <TextInput accessibilityLabel="Effective date" placeholder="Takes effect on (YYYY-MM-DD)"
        placeholderTextColor={colors.mutedFg} style={styles.input} value={form.effectiveAt}
        onChangeText={(value) => set("effectiveAt", value)} />
      <TextInput accessibilityLabel="Reason" placeholder="Why? (recorded in the audit trail)"
        placeholderTextColor={colors.mutedFg} style={styles.input} value={form.reason}
        onChangeText={(value) => set("reason", value)} multiline />
      {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
      {!!notice && <Small>{notice}</Small>}
      {snapshot.committedNeedsRefresh && <Pressable accessibilityRole="button"
        onPress={() => void control.refreshAfterCommit()}><Text style={styles.link}>Reload the schedule</Text></Pressable>}
      {!preview && <Button onPress={doPreview} loading={busy} disabled={busy}>Preview the effect</Button>}

      {!!preview && <View style={{ gap: 8 }}>
        <Body weight="700">Before you confirm</Body>
        <Small>{preview.summary.changed} visit(s) change, {preview.summary.unchanged} do not.</Small>
        {rows.map((row) => {
          const view = describeRow(row);
          return <View key={row.logical_key} style={styles.row}>
            <Text style={styles.rowTitle}>{row.logical_key} · {view.track}{view.changed ? " · changes" : " · unchanged"}</Text>
            <Small>Protocol target: {view.protocolTarget ?? "not dated"}
              {view.protocolWas ? ` (was ${view.protocolWas})` : ""}
              {view.window.earliest ? ` · window ${view.window.earliest} to ${view.window.latest}` : ""}</Small>
            <Small>Appointment / exception: {view.appointment ?? "none"} · Actual: {view.actual ?? "not recorded"}</Small>
            {view.flags.map((flag) => <Text key={flag} style={styles.error}>{flag}</Text>)}
            <Small>{view.why}</Small>
          </View>;
        })}
        {preview.notifications?.length > 0 && <Small>
          Reminders: {preview.notifications.map((item: any) => `${item.action.toLowerCase()} ${item.logical_key}`).join(", ")}
        </Small>}
        <View style={styles.actions}>
          <Button onPress={doConfirm} loading={busy} disabled={busy}>Confirm</Button>
          <Button variant="secondary" onPress={doCancel} disabled={busy}>Discard</Button>
        </View>
      </View>}

      <Body weight="700">History</Body>
      {history.length === 0 && <Small>No commands recorded for this patient yet.</Small>}
      {history.map((item) => (
        <View key={item.id} style={styles.row}>
          <Text style={styles.rowTitle}>#{item.sequence} {item.command_type.replace(/_/g, " ").toLowerCase()}
            {item.reversed_by ? " · reversed" : ""}{item.reverses_command_id ? " · reversal" : ""}</Text>
          <Small>{item.logical_key || item.track_code || ""} · effective {item.effective_at} · {item.reason}</Small>
          {!item.reversed_by && !item.reverses_command_id && !["RECORD_ACTUAL", "MARK_MISSED", "CREATE_UNSCHEDULED"].includes(item.command_type) &&
            <Pressable accessibilityRole="button" onPress={() => reverse(item.id)}>
              <Text style={styles.link}>Reverse this</Text></Pressable>}
        </View>))}
      <Small>{calendar.length} visit(s) in the saved calendar.</Small>
    </Card>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 10, paddingVertical: 6 },
  chipOn: { backgroundColor: colors.primary },
  chipText: { fontFamily: fonts.medium, fontSize: 12, color: colors.foreground },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10,
    fontFamily: fonts.regular, color: colors.foreground },
  error: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12 },
  row: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 8, gap: 2 },
  rowTitle: { fontFamily: fonts.bold, fontSize: 13, color: colors.foreground },
  actions: { flexDirection: "row", gap: 8 },
  link: { color: colors.primary, fontFamily: fonts.medium, fontSize: 12 },
});
