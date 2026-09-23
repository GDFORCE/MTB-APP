import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CalendarDays, CheckCircle2 } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  cancelScheduleImpact, confirmScheduleImpact, createUnscheduledVisit,
  getPatientAnchorStatuses, getPatientConditions, getPatientDeviations,
  getPatientSchedule, getPatientScheduleProjection, getScheduleProjection,
  getUniversalSchedule, previewConditionImpact,
} from "./api";
import type {
  AnchorStatusResponse, DeviationReportResponse, PatientProjection, ScheduleImpact,
} from "./api";
import {
  summariseImpact, toAnchorStatusRows, toConditionalRequirementRows,
  toConfinementRows, toImpactRows, toPatientScheduleRows, toProtocolScheduleRows,
  toRepeatRuleRows, toTimelineEntries, toVersionContext,
} from "./presentation";
import type { ConditionalRequirementRow } from "./presentation";
import {
  AnchorStatusTable, ConditionalRequirementsTable, ConfinementTable, DeviationTable,
  ImpactPreviewTable, PatientTimeline, RepeatRuleFooter, ScheduleTable,
  VersionContextHeader,
} from "./ScheduleTable";
import type {
  PatientConditionsResponse, PatientScheduleResponse, ScheduleProjection,
  UniversalSchedule,
} from "./types";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;

export default function PatientScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ patientId?: string; patient_id?: string; id?: string; patientName?: string }>();
  const [patientSchedule, setPatientSchedule] = useState<PatientScheduleResponse | null>(null);
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
  const [anchors, setAnchors] = useState<AnchorStatusResponse | null>(null);
  const [deviations, setDeviations] = useState<DeviationReportResponse | null>(null);
  const [projection, setProjection] = useState<PatientProjection | null>(null);
  const [newestVersion, setNewestVersion] = useState<number | undefined>();
  // Doc 2 / Case 3: a condition never changes a patient's schedule directly. The
  // PI or CRC records that it occurred, sees exactly what would move, and only
  // then confirms.
  const [conditions, setConditions] = useState<PatientConditionsResponse | null>(null);
  const [proposal, setProposal] = useState<ScheduleImpact | null>(null);
  const [pendingCondition, setPendingCondition] = useState<ConditionalRequirementRow | null>(null);
  const [conditionBusy, setConditionBusy] = useState(false);
  const [view, setView] = useState<"table" | "timeline">("table");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Doc 10 s13-15: MTB fully supports protocol-defined unscheduled visits on
  // the backend (POST /patients/{id}/unscheduled-visits already existed),
  // but no screen ever called it - a real, built feature with no way to
  // reach it. This adds that entry point here, on the screen a CRC is
  // already looking at when they need it.
  const [addingUnscheduled, setAddingUnscheduled] = useState(false);
  const [unscheduledEventCode, setUnscheduledEventCode] = useState("");
  const [unscheduledDate, setUnscheduledDate] = useState("");
  const [unscheduledReason, setUnscheduledReason] = useState("");
  const [unscheduledBusy, setUnscheduledBusy] = useState(false);
  const [unscheduledError, setUnscheduledError] = useState("");

  const load = async () => {
    setLoading(true); setError("");
    try {
      let patientId = first(params.patientId) || first(params.patient_id) || first(params.id);
      if (!patientId && __DEV__) patientId = await AsyncStorage.getItem("uctsm:last_patient_id") || undefined;
      if (!patientId) throw new Error("Select a patient to view their visit schedule.");
      const nextPatientSchedule = await getPatientSchedule(patientId);
      setPatientSchedule(nextPatientSchedule);
      if (nextPatientSchedule.schedule_version_id) {
        const [nextSchedule, nextProjections] = await Promise.all([
          getUniversalSchedule(nextPatientSchedule.schedule_version_id),
          getScheduleProjection(nextPatientSchedule.schedule_version_id),
        ]);
        setSchedule(nextSchedule); setProjections(nextProjections);
      }
      // Anchor status and deviations are derived views. A failure in either must
      // not hide the schedule itself, which is what the site actually needs.
      const [nextAnchors, nextDeviations, nextProjection, nextConditions] =
        await Promise.allSettled([
          getPatientAnchorStatuses(patientId),
          getPatientDeviations(patientId),
          getPatientScheduleProjection(patientId),
          getPatientConditions(patientId),
        ]);
      setAnchors(nextAnchors.status === "fulfilled" ? nextAnchors.value : null);
      setDeviations(nextDeviations.status === "fulfilled" ? nextDeviations.value : null);
      setProjection(nextProjection.status === "fulfilled" ? nextProjection.value : null);
      setConditions(nextConditions.status === "fulfilled" ? nextConditions.value : null);
    } catch (nextError: any) {
      setError(nextError?.response?.data?.detail || nextError?.message || "The patient schedule could not be loaded.");
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [params.patientId, params.patient_id, params.id]);

  const rows = useMemo(() => {
    if (!patientSchedule || !schedule) return [];
    return toPatientScheduleRows(patientSchedule, toProtocolScheduleRows(schedule, projections));
  }, [patientSchedule, schedule, projections]);

  const repeatRules = useMemo(
    () => toRepeatRuleRows(patientSchedule?.repeat_rules), [patientSchedule]);
  const anchorRows = useMemo(
    () => toAnchorStatusRows(anchors?.anchors), [anchors]);
  const reportedDeviations = useMemo(
    () => (deviations?.visits || []).filter(
      (item) => item.deviation_type !== "NONE" && item.deviation_type !== "NOT_ASSESSABLE"),
    [deviations],
  );
  const versionContext = useMemo(
    () => toVersionContext(
      schedule ? {
        name: schedule.schedule_metadata?.name,
        version_number: schedule.schedule_metadata?.version_number,
        status: schedule.schedule_metadata?.status,
        effective_from: (schedule.schedule_metadata as { effective_from?: string })
          ?.effective_from,
      } : undefined,
      { newestVersionNumber: newestVersion },
    ),
    [schedule, newestVersion],
  );
  const timeline = useMemo(() => toTimelineEntries(rows), [rows]);
  const confinements = useMemo(
    () => toConfinementRows(projection?.confinements), [projection]);
  const conditionRows = useMemo(
    () => toConditionalRequirementRows(conditions?.conditions || []), [conditions]);
  const impactRows = useMemo(
    () => toImpactRows(proposal?.impact?.events || []), [proposal]);
  // Doc 10 s13-14: an unscheduled visit must be one of the protocol's OWN
  // defined types, never a free-text visit name a CRC invents on the spot -
  // that is exactly the "no schedule element without protocol evidence"
  // rule this whole engine is built on.
  const unscheduledOptions = useMemo(
    () => (schedule?.events || [])
      .filter((item) => (item.event_type || "").toUpperCase() === "UNSCHEDULED")
      .map((item) => ({ code: item.code, label: item.display_name || item.protocol_label || item.code })),
    [schedule],
  );

  useEffect(() => {
    if (unscheduledOptions.length === 1) setUnscheduledEventCode(unscheduledOptions[0].code);
  }, [unscheduledOptions]);

  const submitUnscheduledVisit = async () => {
    const patientId = patientSchedule?.patient_id;
    if (!patientId || unscheduledBusy) return;
    setUnscheduledError("");
    if (!unscheduledEventCode) {
      setUnscheduledError("Choose which unscheduled visit type this is.");
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(unscheduledDate)) {
      setUnscheduledError("Enter the date as YYYY-MM-DD.");
      return;
    }
    if (!unscheduledReason.trim()) {
      setUnscheduledError("Say why this unscheduled visit occurred - it becomes part of the audit trail.");
      return;
    }
    setUnscheduledBusy(true);
    try {
      await createUnscheduledVisit(patientId, {
        event_code: unscheduledEventCode, occurred_on: unscheduledDate,
        reason: unscheduledReason.trim(),
      });
      setAddingUnscheduled(false);
      setUnscheduledDate(""); setUnscheduledReason("");
      if (unscheduledOptions.length !== 1) setUnscheduledEventCode("");
      setNotice("Unscheduled visit added to this patient's schedule.");
      await load();
    } catch (nextError: any) {
      setUnscheduledError(
        nextError?.response?.data?.detail || nextError?.message
        || "The unscheduled visit could not be saved.");
    } finally {
      setUnscheduledBusy(false);
    }
  };

  const horizon = () => {
    const next = new Date();
    next.setFullYear(next.getFullYear() + 1);
    return next.toISOString().slice(0, 10);
  };

  /** Ask what would change. Applies nothing - that needs a separate confirmation. */
  const previewCondition = async (row: ConditionalRequirementRow) => {
    const patientId = patientSchedule?.patient_id;
    if (!patientId || conditionBusy) return;
    setConditionBusy(true); setError("");
    try {
      setProposal(await previewConditionImpact(
        patientId,
        {
          condition_code: row.id,
          state: "ACTIVE",
          occurrence_date: new Date().toISOString().slice(0, 10),
        },
        horizon(),
        `${row.condition} was recorded as having occurred`,
      ));
      setPendingCondition(row);
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail || nextError?.message
        || "The schedule impact of this condition could not be calculated.");
    } finally { setConditionBusy(false); }
  };

  const applyProposal = async () => {
    if (!proposal || conditionBusy) return;
    setConditionBusy(true); setError("");
    try {
      await confirmScheduleImpact(
        proposal.id, `${pendingCondition?.condition || "Condition"} confirmed by reviewer`);
      setProposal(null); setPendingCondition(null);
      await load();
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail || nextError?.message
        || "The schedule change could not be applied.");
    } finally { setConditionBusy(false); }
  };

  const discardProposal = async () => {
    if (!proposal || conditionBusy) return;
    setConditionBusy(true);
    try {
      await cancelScheduleImpact(proposal.id, "Reviewer cancelled before applying");
    } catch {
      // The proposal expires on its own; failing to cancel must not leave the
      // reviewer stuck looking at a change they already decided against.
    } finally {
      setProposal(null); setPendingCondition(null); setConditionBusy(false);
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Clinical" title="Patient Visit Schedule" />
      {loading ? <View style={styles.center}><ActivityIndicator color={colors.primary} /><Small style={{ marginTop: 10 }}>Loading patient visits…</Small></View> : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {error ? (
            <Card style={styles.empty}>
              <AlertTriangle size={30} color={colors.warning} />
              <Body weight="700" style={{ marginTop: 10 }}>Schedule unavailable</Body>
              <Small style={styles.message}>{error}</Small>
              <Button onPress={() => router.push("/(app)/clinical/patients" as never)} variant="secondary" style={styles.button}>Choose patient</Button>
            </Card>
          ) : patientSchedule?.status === "NOT_EVALUATED" ? (
            <Card style={styles.empty}>
              <CalendarDays size={30} color={colors.primary} />
              <Body weight="700" style={{ marginTop: 10 }}>Schedule not evaluated yet</Body>
              <Small style={styles.message}>Required clinical information must be recorded before the approved schedule can produce dated visits.</Small>
            </Card>
          ) : (
            <>
              <Card>
                <Small>PATIENT</Small>
                <Body weight="700" style={styles.title}>{first(params.patientName) || "Patient visit plan"}</Body>
                <Small>{rows.length} scheduled, pending or non-applicable events · Dates are supplied by the UCTSM evaluator.</Small>
              </Card>

              {!!notice && (
                <Card style={styles.notice}>
                  <CheckCircle2 size={18} color={colors.success} />
                  <Small style={styles.noticeText}>{notice}</Small>
                </Card>
              )}

              {/* Doc 10 s13-15: an unscheduled visit is still ONE OF the
                  protocol's own defined types (never free text) - this is the
                  entry point to the backend endpoint that already existed with
                  no screen ever calling it. */}
              <Card>
                <View style={styles.sectionRow}>
                  <View style={{ flex: 1 }}>
                    <Body weight="700" style={styles.sectionTitle}>Unscheduled Visit</Body>
                    <Small>Record a protocol-defined unscheduled visit that occurred outside the plan</Small>
                  </View>
                  <Button
                    variant="secondary"
                    onPress={() => { setUnscheduledError(""); setAddingUnscheduled(!addingUnscheduled); }}>
                    {addingUnscheduled ? "Cancel" : "Add"}
                  </Button>
                </View>

                {addingUnscheduled && (
                  unscheduledOptions.length === 0 ? (
                    <Small style={styles.message}>
                      The protocol defines no unscheduled visit type, so none can be added here.
                    </Small>
                  ) : (
                    <View style={styles.unscheduledForm}>
                      {unscheduledOptions.length > 1 && (
                        <>
                          <Small style={styles.fieldLabel}>Visit type</Small>
                          <View style={styles.categoryRow}>
                            {unscheduledOptions.map((option) => (
                              <Pressable
                                key={option.code}
                                accessibilityRole="button"
                                accessibilityLabel={option.label}
                                onPress={() => setUnscheduledEventCode(option.code)}
                                style={[
                                  styles.category,
                                  unscheduledEventCode === option.code && styles.categoryActive,
                                ]}>
                                <Small style={styles.categoryLabel}>{option.label}</Small>
                              </Pressable>
                            ))}
                          </View>
                        </>
                      )}

                      <Small style={styles.fieldLabel}>Date (YYYY-MM-DD)</Small>
                      <TextInput
                        style={styles.input}
                        value={unscheduledDate}
                        onChangeText={setUnscheduledDate}
                        placeholder="2026-09-10"
                        placeholderTextColor={colors.mutedFg}
                      />

                      <Small style={styles.fieldLabel}>Reason</Small>
                      <TextInput
                        style={[styles.input, styles.reason]}
                        value={unscheduledReason}
                        onChangeText={setUnscheduledReason}
                        placeholder="Why this unscheduled visit occurred - becomes part of the audit trail"
                        placeholderTextColor={colors.mutedFg}
                        multiline
                      />

                      {!!unscheduledError && (
                        <Small style={styles.unscheduledError}>{unscheduledError}</Small>
                      )}

                      <Button
                        disabled={unscheduledBusy}
                        onPress={() => void submitUnscheduledVisit()}
                        style={{ marginTop: 12 }}>
                        {unscheduledBusy ? "Saving…" : "Save unscheduled visit"}
                      </Button>
                    </View>
                  )
                )}
              </Card>

              {/* Doc 1 s3 and s22: an undated visit is explained by the anchor it
                  waits on, so the schedule never just looks broken. */}
              {!!anchorRows.length && (
                <>
                  <View>
                    <Body weight="700" style={styles.sectionTitle}>Reference Dates</Body>
                    <Small>What each protocol anchor is waiting on for this patient</Small>
                  </View>
                  <AnchorStatusTable rows={anchorRows} />
                </>
              )}

              {/* Doc 9 s12-13: which version produced these dates. Two patients
                  on one trial showing different dates is the protocol working as
                  written, and this is what says so. */}
              <VersionContextHeader context={versionContext} />

              <View style={styles.sectionRow}>
                <View style={{ flex: 1 }}>
                  <Body weight="700" style={styles.sectionTitle}>
                    {view === "table" ? "Visit Schedule" : "Patient Timeline"}
                  </Body>
                  <Small>
                    {view === "table"
                      ? "Expected dates and allowed windows"
                      : "Visits in the order they happen"}
                  </Small>
                </View>
                <Button
                  variant="secondary"
                  onPress={() => setView(view === "table" ? "timeline" : "table")}>
                  {view === "table" ? "Timeline" : "Table"}
                </Button>
              </View>

              {view === "table" ? (
                <>
                  <ScheduleTable rows={rows} patient />
                  <RepeatRuleFooter rows={repeatRules} />
                </>
              ) : (
                <PatientTimeline entries={timeline} />
              )}

              {/* Doc 6 s17/s31: one stay, expandable to its study days. A row per
                  day would read as several separate visits. */}
              <ConfinementTable rows={confinements} />

              {/* Case 3. Inactive conditions carry no date, no reminder and no
                  overdue state - they are requirements, not visits. */}
              <ConditionalRequirementsTable
                rows={conditionRows}
                onConfirm={(row) => void previewCondition(row)}
              />
              {!!proposal && (
                <Card>
                  <Body weight="700" style={styles.sectionTitle}>Schedule Impact</Body>
                  <Small>
                    {pendingCondition
                      ? `If ${pendingCondition.condition} is confirmed: `
                      : ""}
                    {summariseImpact(impactRows)}
                  </Small>
                  <ImpactPreviewTable rows={impactRows} />
                  <View style={styles.proposalActions}>
                    <Button
                      variant="secondary"
                      disabled={conditionBusy}
                      onPress={() => void discardProposal()}>
                      Cancel
                    </Button>
                    <Button
                      testID="confirm-condition-impact"
                      disabled={conditionBusy}
                      onPress={() => void applyProposal()}>
                      {conditionBusy ? "Applying…" : "Confirm change"}
                    </Button>
                  </View>
                </Card>
              )}

              {/* Doc 1 s18 and s26: deviation is computed from the CURRENT expected
                  date, so a corrected anchor changes what is reported here. */}
              {!!reportedDeviations.length && (
                <>
                  <View>
                    <Body weight="700" style={styles.sectionTitle}>Protocol Deviations</Body>
                    <Small>Recalculated against this patient's pinned schedule version</Small>
                  </View>
                  <DeviationTable rows={reportedDeviations} />
                </>
              )}
            </>
          )}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 15 },
  empty: { alignItems: "center", paddingVertical: 30 },
  message: { textAlign: "center", marginTop: 6, maxWidth: 420 },
  button: { width: "100%", marginTop: 18 },
  title: { fontSize: 18, marginVertical: 4 },
  sectionTitle: { fontSize: 20 },
  sectionRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  proposalActions: { flexDirection: "row", gap: 10, marginTop: 12 },
  notice: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: colors.success + "14", borderColor: colors.success,
  },
  noticeText: { flex: 1, color: colors.foreground },
  unscheduledForm: { marginTop: 14 },
  fieldLabel: { marginTop: 12, marginBottom: 4, fontWeight: "700" },
  categoryRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  category: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm,
    paddingVertical: 5, paddingHorizontal: 9,
  },
  categoryActive: { borderColor: colors.primary, backgroundColor: colors.primary + "14" },
  categoryLabel: { fontSize: 12 },
  input: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    padding: 10, color: colors.foreground, fontFamily: fonts.regular,
  },
  reason: { minHeight: 70, textAlignVertical: "top" },
  unscheduledError: { color: colors.warning, marginTop: 8 },
});
