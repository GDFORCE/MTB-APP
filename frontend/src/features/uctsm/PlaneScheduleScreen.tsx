import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, spacing } from "@/src/theme/tokens";
import { getPlaneCalendar, listPlanePatients, seedPilot, seedPilotAmendment } from "./api";
import { PatientAmendmentPanel } from "./PatientAmendmentPanel";
import { PatientCommandsPanel } from "./PatientCommandsPanel";
import { mergePage, toPlaneRows, type PlaneEntry } from "./planeSchedule.ts";
import { classifyProbeError, PLANE_MESSAGES } from "./patientCommands.ts";

const first = (value?: string | string[]) => (Array.isArray(value) ? value[0] : value);

/**
 * A patient whose clinical schedule is AUTHORITATIVE in the command plane: the
 * calendar below is read from the same plane the commands write to, so a
 * confirmed change is exactly what this screen shows after the reload. Existing
 * SQL/legacy patients keep their own screen and never appear here.
 */
export default function PlaneScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ trialId?: string; patient?: string }>();
  const trialId = first(params.trialId);
  const patient = first(params.patient);
  const [rows, setRows] = useState<ReturnType<typeof toPlaneRows>>([]);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [authority, setAuthority] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);

  const load = useCallback(async () => {
    if (!trialId || !patient) return;
    generation.current += 1;
    const gen = generation.current;
    setLoading(true); setError("");
    try {
      const plane = await getPlaneCalendar(trialId, patient);
      if (gen !== generation.current) return;
      setRows(toPlaneRows(plane.calendar as PlaneEntry[]));
      setVersionId(plane.schedule_version_id);
      setAuthority(plane.authority_plane ?? null);
    } catch (failure: any) {
      if (gen !== generation.current) return;
      const kind = classifyProbeError(failure?.response?.status);
      setRows([]); setVersionId(null);
      setError(PLANE_MESSAGES[kind === "checking" || kind === "ready" || kind === "mixed" || kind === "mismatch" ? "unavailable" : kind]);
    } finally {
      if (gen === generation.current) setLoading(false);
    }
  }, [trialId, patient]);
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, [load]);

  if (!trialId || !patient) return <PilotChooser />;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Command plane" title={`Patient ${patient}`} onBack={() => router.back()} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, gap: 12 }}>
        {loading && <ActivityIndicator />}
        {!!error && <Card><Small color={colors.destructive}>{error}</Small>
          <Pressable accessibilityRole="button" onPress={() => void load()}><Text style={styles.link}>Try again</Text></Pressable>
        </Card>}
        {rows.map((row) => (
          <Card key={row.key}>
            <Body weight="700">{row.visit} · {row.track}</Body>
            <Small>Status: {row.status}</Small>
            <Small>Protocol target: {row.protocolTarget} · Window: {row.protocolWindow}</Small>
            <Small>Appointment: {row.appointment}</Small>
            <Small>Exception: {row.exception}</Small>
            <Small>Actual visit: {row.actualVisit} · Actual treatment: {row.actualTreatment}</Small>
            {row.flags.map((flag) => <Text key={flag} style={styles.flag}>{flag}</Text>)}
          </Card>
        ))}
        {rows.length > 0 && <Small>Evaluation {rows[0].evaluationId}</Small>}
        {rows.length > 0 && authority !== "MONGO" && <Small color={colors.destructive}>
          This patient is not authoritative in the command plane (authority: {authority ?? "unclassified"}),
          so this view is read-only and commands are switched off.</Small>}
        <PatientCommandsPanel
          trialId={trialId} externalPatientId={patient} scheduleVersionId={versionId}
          displayPlane={authority === "MONGO" ? "MONGO" : "SQL"} horizon={new Date(Date.now() + 365 * 86400000).toISOString().slice(0, 10)}
          onChanged={() => void load()} />
        <PatientAmendmentPanel trialId={trialId} externalPatientId={patient} onChanged={() => void load()} />
        <Pressable accessibilityRole="button" onPress={() => router.push({
          pathname: "/(app)/clinical/amendment-review", params: { trialId } } as never)}>
          <Text style={styles.link}>Compare protocol versions and match their visits</Text></Pressable>
      </ScrollView>
    </ScreenContainer>
  );
}

/** No patient chosen: create/open the synthetic pilot and page through its patients. */
function PilotChooser() {
  const router = useRouter();
  const [trialId, setTrialId] = useState<string | null>(null);
  const [items, setItems] = useState<{ external_patient_id: string; patient_code: string }[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (failure: any) {
      setError(failure?.response?.status === 404
        ? "The synthetic pilot is not enabled on this server."
        : "The pilot could not be loaded. Nothing was changed.");
    } finally { setBusy(false); }
  };
  const loadPage = (trial: string, after: string | null, reset = false) => run(async () => {
    const page = await listPlanePatients(trial, after);
    setItems((current) => (reset ? page.items : mergePage(current, page)));
    setCursor(page.next_cursor);
  });
  const addAmendment = () => run(async () => {
    await seedPilotAmendment();
    if (trialId) { const page = await listPlanePatients(trialId, null); setItems(page.items); setCursor(page.next_cursor); }
  });
  const open = () => run(async () => {
    const seeded = await seedPilot();
    setTrialId(seeded.trial_id);
    const page = await listPlanePatients(seeded.trial_id, null);
    setItems(page.items); setCursor(page.next_cursor);
  });

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Command plane" title="Synthetic pilot" onBack={() => router.back()} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, gap: 12 }}>
        <Small>MTB-UCTSM-PILOT-001 is an isolated local trial with synthetic patients only.</Small>
        {!trialId && <Button onPress={open} loading={busy}>Open the synthetic pilot</Button>}
        {!!trialId && <Button variant="secondary" onPress={addAmendment} loading={busy}>
          Add the synthetic protocol amendment (moves nobody)</Button>}
        {!!trialId && <Pressable accessibilityRole="button" onPress={() => router.push({
          pathname: "/(app)/clinical/amendment-review", params: { trialId } } as never)}>
          <Text style={styles.link}>Compare protocol versions and match their visits</Text></Pressable>}
        {!!error && <Small color={colors.destructive}>{error}</Small>}
        {items.map((item) => (
          <Pressable key={item.external_patient_id} accessibilityRole="button"
            onPress={() => router.push({ pathname: "/(app)/clinical/plane-schedule",
              params: { trialId: trialId!, patient: item.external_patient_id } } as never)}>
            <Card><Body weight="700">{item.patient_code}</Body></Card>
          </Pressable>))}
        {!!cursor && !!trialId && <Button variant="secondary" loading={busy}
          onPress={() => void loadPage(trialId, cursor)}>Load more</Button>}
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  link: { color: colors.primary, fontFamily: fonts.medium },
  flag: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12 },
});
