import React, { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams } from "expo-router";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  createCohort, getCohortReadiness, getNotificationPreferences, getScheduleReport, listDeadLetters, promoteCohort,
  putNotificationPreferences, replayDeadLetter, rollbackCohort,
} from "./api";
import { reviewFailureMessage } from "./manualRules.ts";
import {
  cohortBody, cutoverMessage, emptyPrefs, prefsBody, readinessLines, reasonBody, reportLine, reportTotals,
  rollbackBody, snapshotBody, type PrefsForm, type Readiness, type ScheduleReport,
} from "./operations.ts";

const first = (value?: string | string[]) => (Array.isArray(value) ? value[0] : value);

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const run = useCallback(async (work: () => Promise<string | void>, describe: (failure: unknown) => string = reviewFailureMessage) => {
    setBusy(true); setError(""); setNotice("");
    try { const message = await work(); if (message) setNotice(message); }
    catch (failure) { setError((failure as Error).message && !(failure as { response?: unknown }).response ? (failure as Error).message : describe(failure)); }
    finally { setBusy(false); }
  }, []);
  return { busy, error, notice, run };
}
const Status = ({ action }: { action: ReturnType<typeof useAction> }) => <>
  {action.busy && <ActivityIndicator />}
  {!!action.error && <Text style={styles.error} accessibilityLiveRegion="polite">{action.error}</Text>}
  {!!action.notice && <Small accessibilityLiveRegion="polite">{action.notice}</Small>}
</>;
const Input = (props: React.ComponentProps<typeof TextInput>) => <TextInput placeholderTextColor={colors.mutedFg} {...props} style={styles.input} />;

function DeadLetters() {
  const action = useAction();
  const [rows, setRows] = useState<Awaited<ReturnType<typeof listDeadLetters>>["messages"] | null>(null);
  const [reason, setReason] = useState("");
  const load = () => action.run(async () => { setRows((await listDeadLetters()).messages); });
  return <Card>
    <Body weight="700">Failed notification messages</Body>
    <Small>Messages that ran out of retries. Replaying needs the replay grant and a reason; the action is audited, and a message the receiver already handled is not applied twice.</Small>
    <Status action={action} />
    <Button variant="secondary" disabled={action.busy} onPress={() => void load()}>Load failed messages</Button>
    {rows && !rows.length && <Small>No failed messages.</Small>}
    {rows?.map((row) => <View key={row.id} style={styles.row}>
      <Text style={styles.name}>{row.event_type} · {row.aggregate_type}</Text>
      <Small>{row.attempts} attempts · {row.last_error_code ?? "no error code"} · {row.created_at}</Small>
      <Button disabled={action.busy} onPress={() => void action.run(async () => {
        await replayDeadLetter(row.id, reasonBody(reason, "the replay")); setReason(""); await load(); return "Message queued again.";
      })}>Replay this message</Button>
    </View>)}
    {!!rows?.length && <Input accessibilityLabel="Replay reason" placeholder="Why replay (required)" value={reason} onChangeText={setReason} multiline />}
  </Card>;
}

function Cohorts({ trialId }: { trialId: string }) {
  const action = useAction();
  const [name, setName] = useState("");
  const [ids, setIds] = useState("");
  const [cohort, setCohort] = useState<{ id: string; name: string; state: string } | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [reason, setReason] = useState("");
  const [acceptLoss, setAcceptLoss] = useState(false);
  const [snapshot, setSnapshot] = useState("");
  const refresh = (id: string) => getCohortReadiness(trialId, id).then(setReadiness);
  return <Card>
    <Body weight="700">Pilot cohort cutover</Body>
    <Small>Move a small named group of reconciled patients to the new system. A second person promotes it. Rolling back refuses to lose changes made in the new system unless you accept that.</Small>
    <Status action={action} />
    {!cohort && <>
      <Input accessibilityLabel="Cohort name" placeholder="Cohort name" value={name} onChangeText={setName} />
      <Input accessibilityLabel="Legacy patient ids" placeholder="Legacy patient ids, separated by commas or lines" value={ids} onChangeText={setIds} multiline />
      <Button disabled={action.busy} onPress={() => void action.run(async () => {
        const made = await createCohort(trialId, cohortBody(name, ids)); setCohort(made); await refresh(made.id); return "Shadow cohort created. Nothing has changed for these patients yet.";
      }, cutoverMessage)}>Create shadow cohort</Button>
    </>}
    {!!cohort && <>
      <Text style={styles.name}>{cohort.name} · {cohort.state}</Text>
      {readiness && readinessLines(readiness).map((line) => <Small key={line}>{line}</Small>)}
      <Button variant="secondary" disabled={action.busy} onPress={() => void action.run(async () => { await refresh(cohort.id); }, cutoverMessage)}>Check readiness</Button>
      <Input accessibilityLabel="Reason" placeholder="Reason (required)" value={reason} onChangeText={setReason} multiline />
      {cohort.state === "SHADOW" && <Input accessibilityLabel="Snapshot report" placeholder="Paste the report printed by the encrypted snapshot command (required)" value={snapshot} onChangeText={setSnapshot} multiline />}
      {cohort.state === "SHADOW" && <Button disabled={action.busy || !readiness?.ready} onPress={() => void action.run(async () => {
        setCohort(await promoteCohort(trialId, cohort.id, { ...reasonBody(reason, "the promotion"), snapshot: snapshotBody(snapshot) })); return "Promoted. Legacy schedule writes for these patients are now refused.";
      }, cutoverMessage)}>Promote to pilot (second person)</Button>}
      {cohort.state === "PILOT" && <>
        <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: acceptLoss }} onPress={() => setAcceptLoss(!acceptLoss)}>
          <Text style={styles.link}>{acceptLoss ? "☑ " : "☐ "}A named owner accepts losing changes made in the new system</Text></Pressable>
        <Button disabled={action.busy} onPress={() => void action.run(async () => {
          setCohort(await rollbackCohort(trialId, cohort.id, rollbackBody(reason, acceptLoss))); return "Rolled back. Legacy writes are allowed again.";
        }, cutoverMessage)}>Roll back</Button>
      </>}
    </>}
  </Card>;
}

function Preferences({ trialId }: { trialId: string }) {
  const action = useAction();
  const [patient, setPatient] = useState("");
  const [form, setForm] = useState<PrefsForm>(emptyPrefs);
  const field = (key: keyof PrefsForm, label: string, placeholder?: string) =>
    <Input accessibilityLabel={label} placeholder={placeholder ?? label} value={String(form[key])} onChangeText={(value) => setForm({ ...form, [key]: value })} />;
  return <Card>
    <Body weight="700">Reminder preferences</Body>
    <Small>Record the patient's documented choice. Nothing is sent unless the patient granted consent.</Small>
    <Status action={action} />
    <Input accessibilityLabel="Patient id" placeholder="Legacy patient id" value={patient} onChangeText={setPatient} />
    <Button variant="secondary" disabled={action.busy || !patient.trim()} onPress={() => void action.run(async () => {
      const current = (await getNotificationPreferences(trialId, patient.trim())).preferences;
      if (current) setForm({ consent: current.consent, timezone: current.timezone, delivery_time: current.delivery_time,
        lead_days: String(current.lead_days), quiet_start: current.quiet_start ?? "", quiet_end: current.quiet_end ?? "" });
      return current ? "Loaded the saved preferences." : "No preferences recorded yet.";
    })}>Load</Button>
    {(["GRANTED", "DECLINED", "WITHDRAWN"] as const).map((choice) => <Pressable key={choice} accessibilityRole="radio"
      accessibilityState={{ checked: form.consent === choice }} onPress={() => setForm({ ...form, consent: choice })}>
      <Text style={styles.link}>{form.consent === choice ? "● " : "○ "}Consent {choice.toLowerCase()}</Text></Pressable>)}
    {field("timezone", "Time zone", "Asia/Kolkata")}{field("delivery_time", "Delivery time", "09:00")}
    {field("lead_days", "Days before the visit", "1")}{field("quiet_start", "Quiet hours start (optional)", "22:00")}{field("quiet_end", "Quiet hours end (optional)", "07:00")}
    <Button disabled={action.busy || !patient.trim()} onPress={() => void action.run(async () => {
      const saved = await putNotificationPreferences(trialId, patient.trim(), prefsBody(form)); return `Saved (revision ${saved.revision}).`;
    })}>Save preferences</Button>
  </Card>;
}

function Reports({ trialId }: { trialId: string }) {
  const action = useAction();
  const [report, setReport] = useState<ScheduleReport | null>(null);
  const totals = report ? reportTotals(report) : null;
  return <Card>
    <Body weight="700">Schedule report</Body>
    <Small>Targets, windows and actual visits from the same committed schedule the calendar shows. Sponsors see no operational detail.</Small>
    <Status action={action} />
    <Button variant="secondary" disabled={action.busy} onPress={() => void action.run(async () => { setReport(await getScheduleReport(trialId)); })}>Load report</Button>
    {totals && <Small accessibilityLiveRegion="polite">{totals.patients} patients, {totals.visits} visits, {totals.done} done, {totals.outside} outside their window{report?.next_cursor ? " (first page)" : ""}.</Small>}
    {report?.patients.flatMap((patient) => patient.rows).slice(0, 200).map((row, index) => <Small key={index}>{reportLine(row)}</Small>)}
  </Card>;
}

/** Operations for the Mongo plane. Each card needs its own grant; the server refuses what the signed-in user may not do. */
export default function OperationsScreen() {
  const params = useLocalSearchParams<{ trialId?: string }>();
  const [trialInput, setTrialInput] = useState(first(params.trialId) ?? "");
  const trialId = trialInput.trim();
  return <ScreenContainer>
    <ScreenHeader eyebrow="Operations" title="Plane operations" />
    <ScrollView contentContainerStyle={{ padding: spacing.md, gap: 12 }}>
      <DeadLetters />
      <Card>
        <Body weight="700">Trial</Body>
        <Input accessibilityLabel="Trial id" placeholder="Canonical trial id (UUID)" value={trialInput} onChangeText={setTrialInput} autoCapitalize="none" />
      </Card>
      {!!trialId && <><Cohorts key={`c-${trialId}`} trialId={trialId} /><Preferences key={`p-${trialId}`} trialId={trialId} /><Reports key={`r-${trialId}`} trialId={trialId} /></>}
    </ScrollView>
  </ScreenContainer>;
}
const styles = StyleSheet.create({
  row: { gap: 6, paddingVertical: 8, borderTopWidth: 1, borderTopColor: colors.border },
  name: { fontFamily: fonts.medium, color: colors.foreground },
  link: { color: colors.primary, fontFamily: fonts.medium, paddingVertical: 6 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground },
  error: { color: colors.destructive },
});
