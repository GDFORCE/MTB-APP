import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, ChevronRight, RefreshCw, ShieldCheck } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  acceptParityRun, buildCanonicalSchedule, getCanonicalSchedules, getReadMode, setReadMode,
} from "./api";
import type { CanonicalScheduleIndex, ReadModeStatus } from "./api";
import { toEnrolmentReadiness, toScheduleVersionRows } from "./presentation";
import { ProtocolUploadReview } from "./ProtocolUploadReview";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
const errorText = (error: any) =>
  error?.response?.data?.detail || error?.message
  || "The schedule versions could not be loaded.";

/**
 * Case 10 / doc 9 - the trial-level protocol and schedule version table.
 *
 * Every version is listed, superseded ones included, because a patient enrolled
 * under v1 stays on v1: hiding it would make the patients still being scheduled
 * from it unexplainable. The row that matters most - the one new enrollments
 * receive - is marked rather than merely being first.
 */
export default function ScheduleVersionsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string; trialId?: string; trialName?: string }>();
  const trialId = first(params.id) || first(params.trialId) || "";
  const [index, setIndex] = useState<CanonicalScheduleIndex | null>(null);
  const [busy, setBusy] = useState("load");
  const [error, setError] = useState("");
  // Doc 9 / AD-1a: the parity gate this trial's reads are governed by. Built
  // and tested on the backend (ParityService.set_read_mode,
  // POST .../read-mode) with no screen ever calling it - this is that screen.
  const [readMode, setReadModeStatus] = useState<ReadModeStatus | null>(null);
  const [readModeError, setReadModeError] = useState("");
  const [cutoverReason, setCutoverReason] = useState("");
  const [cutoverBusy, setCutoverBusy] = useState(false);
  // AD-1a: signing off one DIFFERENCES_FOUND run so it counts toward
  // readiness - a per-row reason, never a bulk "accept everything failing".
  const [acceptReasons, setAcceptReasons] = useState<Record<string, string>>({});
  const [acceptBusyId, setAcceptBusyId] = useState<string | null>(null);
  const [acceptError, setAcceptError] = useState("");

  const loadReadMode = useCallback(async () => {
    if (!trialId) return;
    try {
      setReadModeStatus(await getReadMode(trialId));
    } catch (nextError) {
      // A trial with no canonical link yet has nothing to report here - the
      // schedule-version table above already explains that state.
      setReadModeStatus(null);
    }
  }, [trialId]);

  const load = useCallback(async () => {
    if (!trialId) { setError("No trial was selected."); setBusy(""); return; }
    setBusy("load"); setError("");
    try {
      setIndex(await getCanonicalSchedules(trialId));
    } catch (nextError) {
      setError(errorText(nextError));
    } finally { setBusy(""); }
  }, [trialId]);

  useEffect(() => { void load(); void loadReadMode(); }, [load, loadReadMode]);

  const switchReadMode = async (mode: "LEGACY" | "ENGINE") => {
    if (cutoverBusy) return;
    const reason = cutoverReason.trim();
    if (!reason) {
      setReadModeError("Say why this trial's reads are changing mode - it becomes part of the audit trail.");
      return;
    }
    setCutoverBusy(true); setReadModeError("");
    try {
      await setReadMode(trialId, mode, reason);
      setCutoverReason("");
      await loadReadMode();
    } catch (nextError) {
      setReadModeError(errorText(nextError));
    } finally { setCutoverBusy(false); }
  };

  const acceptRun = async (runId: string) => {
    if (acceptBusyId) return;
    const reason = (acceptReasons[runId] || "").trim();
    if (!reason) {
      setAcceptError("Say why this difference is accepted - it becomes part of the audit trail.");
      return;
    }
    setAcceptBusyId(runId); setAcceptError("");
    try {
      await acceptParityRun(trialId, runId, reason);
      setAcceptReasons((prev) => ({ ...prev, [runId]: "" }));
      await loadReadMode();
    } catch (nextError) {
      setAcceptError(errorText(nextError));
    } finally { setAcceptBusyId(null); }
  };

  const rows = useMemo(
    () => toScheduleVersionRows(index?.schedules, index?.approved_schedule_version_id),
    [index],
  );
  const readiness = useMemo(() => toEnrolmentReadiness(index?.schedules), [index]);

  const build = async () => {
    setBusy("build"); setError("");
    try {
      await buildCanonicalSchedule(trialId);
      await load();
    } catch (nextError) {
      setError(errorText(nextError));
    } finally { setBusy(""); }
  };

  const open = (versionId: string) => router.push({
    pathname: "/(app)/sponsor/protocol-schedule",
    params: { scheduleVersionId: versionId, id: trialId },
  });

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow={first(params.trialName) || "Trial"}
        title="Protocol & Schedule Versions"
      />
      {busy === "load" ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
          <Small style={{ marginTop: 10 }}>Loading schedule versions…</Small>
        </View>
      ) : (
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {!!trialId && <ProtocolUploadReview trialId={trialId} onSaved={() => { void load(); }} />}
          {!!error && (
            <Card style={styles.notice}>
              <AlertTriangle size={20} color={colors.warning} />
              <Small style={styles.noticeText}>{error}</Small>
            </Card>
          )}

          <Card>
            <Small color={colors.mutedFg} weight="700">ENROLMENT</Small>
            <Body style={{ marginTop: 4 }}>{readiness.message}</Body>
          </Card>

          {!!readMode && (
            <Card style={styles.cutoverCard}>
              <View style={styles.cutoverHead}>
                {readMode.read_mode === "ENGINE"
                  ? <ShieldCheck size={20} color={colors.success} />
                  : <RefreshCw size={20} color={colors.mutedFg} />}
                <View style={{ flex: 1 }}>
                  <Small color={colors.mutedFg} weight="700">SCHEDULE READS</Small>
                  <Body weight="700" style={{ marginTop: 2 }}>
                    {readMode.read_mode === "ENGINE"
                      ? "Served from the canonical engine"
                      : "Served from the operational store"}
                  </Body>
                </View>
              </View>

              <Small style={styles.cutoverDetail}>
                {readMode.checked} of {readMode.patients} patient(s) parity-checked
                {readMode.with_differences.length
                  ? ` – ${readMode.with_differences.length} with an outstanding difference`
                  : ""}.
              </Small>
              {readMode.read_mode === "LEGACY" && !readMode.ready && !!readMode.reasons.length && (
                <View style={styles.cutoverReasons}>
                  {readMode.reasons.map((item) => (
                    <Small key={item} color={colors.warning} style={styles.cutoverReasonLine}>
                      • {item}
                    </Small>
                  ))}
                </View>
              )}

              {/* AD-1a: a DIFFERENCES_FOUND run that's correct and permanent
                  (the engine won't date a visit the operational store did)
                  would otherwise block this gate forever. Sign it off here -
                  one patient, one reason - rather than fix-and-recheck. */}
              {readMode.read_mode === "LEGACY" && !!readMode.with_differences.length && (
                <View style={styles.acceptList}>
                  <Small color={colors.mutedFg} weight="700">NEEDS A DECISION</Small>
                  {readMode.with_differences.map((item) => (
                    <View key={item.parity_run_id} style={styles.acceptRow}>
                      <Small weight="600">
                        {item.patient_code}
                        {typeof item.differences === "number" && item.differences > 0
                          ? ` – ${item.differences} difference${item.differences === 1 ? "" : "s"}`
                          : ""}
                      </Small>
                      <TextInput
                        value={acceptReasons[item.parity_run_id] || ""}
                        onChangeText={(text) =>
                          setAcceptReasons((prev) => ({ ...prev, [item.parity_run_id]: text }))}
                        placeholder="Why is this difference OK to accept? (required)"
                        placeholderTextColor={colors.mutedFg}
                        style={styles.acceptInput}
                      />
                      <Button
                        variant="secondary"
                        onPress={() => void acceptRun(item.parity_run_id)}
                        disabled={acceptBusyId === item.parity_run_id}
                      >
                        {acceptBusyId === item.parity_run_id ? "Accepting…" : "Accept this difference"}
                      </Button>
                    </View>
                  ))}
                </View>
              )}

              {!!readMode.accepted.length && (
                <View style={styles.acceptList}>
                  <Small color={colors.mutedFg} weight="700">ACCEPTED DIFFERENCES</Small>
                  {readMode.accepted.map((item) => (
                    <Small key={item.parity_run_id} style={styles.cutoverReasonLine}>
                      • {item.patient_code}: {item.accepted_reason}
                    </Small>
                  ))}
                </View>
              )}

              {!!acceptError && <Small style={styles.cutoverError}>{acceptError}</Small>}
              {!!readModeError && <Small style={styles.cutoverError}>{readModeError}</Small>}
              <TextInput
                value={cutoverReason}
                onChangeText={setCutoverReason}
                placeholder="Why is this trial's read mode changing? (required)"
                placeholderTextColor={colors.mutedFg}
                style={styles.cutoverInput}
                multiline
              />
              {/* Doc 9/AD-1a: moving TO the engine requires a matching parity run
                  for every patient; moving back needs no gate at all - reversing
                  a cutover must never be harder than making one. */}
              {readMode.read_mode === "LEGACY" ? (
                <Button
                  testID="switch-to-engine"
                  onPress={() => void switchReadMode("ENGINE")}
                  disabled={cutoverBusy || !readMode.ready}
                >
                  {cutoverBusy ? "Switching…" : "Switch reads to the engine"}
                </Button>
              ) : (
                <Button
                  testID="switch-to-legacy"
                  variant="secondary"
                  onPress={() => void switchReadMode("LEGACY")}
                  disabled={cutoverBusy}
                >
                  {cutoverBusy ? "Switching…" : "Move reads back to the operational store"}
                </Button>
              )}
            </Card>
          )}

          {!rows.length ? (
            <Card style={styles.empty}>
              <Body weight="700">No canonical schedule yet</Body>
              <Small style={styles.emptyText}>
                Build the schedule from this trial's extracted protocol. It is
                created as a draft for review — nothing reaches a patient until
                it is approved.
              </Small>
              <Button
                testID="build-canonical-schedule"
                onPress={build}
                disabled={busy === "build"}
              >
                {busy === "build" ? "Building…" : "Build schedule from protocol"}
              </Button>
            </Card>
          ) : (
            <View style={styles.table}>
              <View style={styles.headerRow}>
                <Text style={[styles.headerText, styles.colName]}>Schedule</Text>
                <Text style={[styles.headerText, styles.colVersion]}>Protocol</Text>
                <Text style={[styles.headerText, styles.colVersion]}>Version</Text>
                <Text style={[styles.headerText, styles.colEffective]}>Effective For</Text>
                <Text style={[styles.headerText, styles.colStatus]}>Status</Text>
                <Text style={[styles.headerText, styles.colPatients]}>Patients</Text>
              </View>
              {rows.map((row, position) => (
                <Pressable
                  key={row.id}
                  testID={`schedule-version-${row.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Open ${row.name} ${row.version}`}
                  onPress={() => open(row.id)}
                  style={[
                    styles.dataRow,
                    position % 2 === 1 && styles.altRow,
                    row.current && styles.currentRow,
                  ]}
                >
                  <Text style={[styles.value, styles.name, styles.colName]}>{row.name}</Text>
                  <Text style={[styles.value, styles.colVersion]}>{row.protocolVersion}</Text>
                  <Text style={[styles.value, styles.colVersion]}>{row.version}</Text>
                  <Text style={[styles.value, styles.colEffective]}>{row.effectiveFor}</Text>
                  <Text style={[styles.value, styles.colStatus]}>{row.status}</Text>
                  <View style={[styles.patientsCell, styles.colPatients]}>
                    <Text style={styles.value}>{row.patients}</Text>
                    <ChevronRight size={15} color={colors.mutedFg} />
                  </View>
                </Pressable>
              ))}
            </View>
          )}

          {!!rows.length && (
            <Button
              testID="rebuild-canonical-schedule"
              variant="secondary"
              onPress={build}
              disabled={busy === "build"}
            >
              {busy === "build" ? "Checking…" : "Import newly extracted schedules"}
            </Button>
          )}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 14 },
  notice: { flexDirection: "row", alignItems: "center", gap: 10 },
  noticeText: { flex: 1 },
  cutoverCard: { gap: 10 },
  cutoverHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  cutoverDetail: { lineHeight: 17 },
  cutoverReasons: { gap: 3 },
  cutoverReasonLine: { lineHeight: 16 },
  cutoverError: { color: colors.destructive },
  cutoverInput: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    padding: 10, minHeight: 44, textAlignVertical: "top",
    color: colors.foreground, fontFamily: fonts.regular, fontSize: 13,
  },
  acceptList: { gap: 8, paddingTop: 4, borderTopWidth: 1, borderTopColor: colors.border },
  acceptRow: {
    gap: 6, backgroundColor: colors.background, borderRadius: radii.md, padding: 10,
  },
  acceptInput: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    padding: 8, color: colors.foreground, fontFamily: fonts.regular, fontSize: 12,
  },
  empty: { alignItems: "flex-start", gap: 10 },
  emptyText: { marginBottom: 4 },
  table: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg,
    overflow: "hidden", backgroundColor: colors.card,
  },
  headerRow: {
    flexDirection: "row", backgroundColor: colors.primaryDeep,
    paddingHorizontal: 10, paddingVertical: 11, gap: 8,
  },
  headerText: {
    color: colors.white, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.2,
  },
  dataRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 10, paddingVertical: 13,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  altRow: { backgroundColor: "#FBF7F1" },
  currentRow: { borderLeftWidth: 3, borderLeftColor: colors.primary },
  value: { fontFamily: fonts.regular, fontSize: 12, color: colors.foreground },
  name: { fontFamily: fonts.semibold },
  patientsCell: { flexDirection: "row", alignItems: "center", gap: 4 },
  colName: { flex: 1.6 },
  colVersion: { flex: 1 },
  colEffective: { flex: 1.7 },
  colStatus: { flex: 1.1 },
  colPatients: { flex: 0.9 },
});
