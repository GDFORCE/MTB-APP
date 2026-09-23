import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CheckCircle2, ClipboardCheck, FlaskConical, RefreshCw, ShieldCheck } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  decideSchedule, getApprovedSchedules, getScheduleProjection,
  getUniversalSchedule, submitScheduleReview, validateSchedule, resolveExtractionIssue,
  approveReviewedTable,
} from "./api";
import { toProtocolScheduleRows, UNSTATED_VISIT_MODE, type ProtocolScheduleRow } from "./presentation";
import { SimpleScheduleTable } from "./SimpleScheduleTable";
import type { Evidence, ScheduleProjection, UniversalSchedule } from "./types";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
const errorText = (error: any) => error?.response?.data?.detail || error?.message || "The schedule could not be loaded.";

export default function ProtocolScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    id?: string; scheduleVersionId?: string; schedule_version_id?: string;
    trialName?: string; protocolVersion?: string;
  }>();
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
  const [busy, setBusy] = useState("load");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [bulkReason, setBulkReason] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [sourceText, setSourceText] = useState<string | null>(null);
  const [issueReasons, setIssueReasons] = useState<Record<string, string>>({});
  const [showChecks, setShowChecks] = useState(false);
  const [showApproval, setShowApproval] = useState(false);
  const [showReject, setShowReject] = useState(false);

  const loadVersion = async (versionId: string) => {
    const [nextSchedule, nextProjections] = await Promise.all([
      getUniversalSchedule(versionId), getScheduleProjection(versionId),
    ]);
    setSchedule(nextSchedule);
    setProjections(nextProjections);
  };

  const resolveVersion = async () => {
    setBusy("load");
    setError("");
    try {
      let versionId = first(params.scheduleVersionId) || first(params.schedule_version_id);
      const trialId = first(params.id);
      if (!versionId && trialId) {
        try {
          const approved = await getApprovedSchedules(trialId);
          versionId = approved[0]?.schedule_version_id;
        } catch {
          // A legacy trial identifier has no UCTSM schedule mapping yet.
        }
      }
      if (!versionId && __DEV__) versionId = await AsyncStorage.getItem("uctsm:last_schedule_version_id") || undefined;
      if (!versionId) throw new Error("No UCTSM schedule version is linked to this trial yet.");
      await loadVersion(versionId);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy("");
    }
  };

  useEffect(() => { void resolveVersion(); }, [params.id, params.scheduleVersionId, params.schedule_version_id]);

  const rows = useMemo(
    () => schedule ? toProtocolScheduleRows(schedule, projections) : [],
    [schedule, projections],
  );
  const openIssues = schedule?.validation_issues.filter((issue) => issue.status === "OPEN") || [];
  const blocking = openIssues.filter((issue) => issue.blocking).length;
  const canReview = !!schedule && ["DRAFT", "EXTRACTED", "VALIDATION_REQUIRED", "REJECTED", "IN_REVIEW"]
    .includes(schedule.schedule_metadata.status);
  const statusLabel = schedule?.schedule_metadata.status.toLowerCase().replace(/_/g, " ");
  // Doc 10 s18/s33: never let an unstated visit mode reach a patient. It is
  // not a validation issue (extraction is not wrong for leaving it unstated -
  // the protocol just did not say), so it needs its own reviewer-visible
  // count rather than hiding inside "openIssues", matching the existing
  // pendingCount/AlertTriangle pattern the legacy visit-schedule screen uses.
  const unstatedModeCount = rows.filter((row) => row.visitMode === UNSTATED_VISIT_MODE).length;

  const run = async (label: string, action: () => Promise<void>) => {
    if (!schedule || busy) return;
    setBusy(label); setError(""); setMessage("");
    try { await action(); await loadVersion(schedule.schedule_version_id); }
    catch (nextError) { setError(errorText(nextError)); }
    finally { setBusy(""); }
  };

  const validate = () => run("validate", async () => {
    const result = await validateSchedule(schedule!.schedule_version_id);
    setMessage(result.blocking_issues ? `${result.blocking_issues} item(s) require correction.` : "Schedule structure is valid.");
  });
  const submit = () => run("submit", async () => {
    await submitScheduleReview(schedule!.schedule_version_id);
    setMessage("Schedule submitted for clinical review.");
  });
  const approve = () => run("approve", async () => {
    await approveReviewedTable(schedule!.schedule_version_id, bulkReason.trim());
    setShowApproval(false);
    setMessage("Schedule approved. It can now be used to calculate patient visits.");
  });
  // The PI-facing sibling of `approve`: the reviewer who now reaches this
  // screen from the pending-review inbox needs a way to say no, not just yes.
  const reject = () => run("reject", async () => {
    const reason = rejectReason.trim();
    if (!reason) {
      throw new Error("Say why this schedule is being rejected before saving it.");
    }
    await decideSchedule(schedule!.schedule_version_id, "REJECT", reason);
    setRejectReason("");
    setMessage("Schedule rejected. It stays editable for a new extraction or correction.");
  });

  const openEvidence = (row: ProtocolScheduleRow) => {
    const evidence = schedule?.evidence.filter((item) => row.evidenceRefs.includes(item.id)) || [];
    if (!evidence.length) return setSourceText("No linked evidence is available for this event.");
    const text = evidence.map(formatEvidence).join("\n\n──────────\n\n");
    setSourceText(`${row.visit} · ${row.visitName}\n\n${text}`);
  };

  if (busy === "load") return <ScreenContainer><ScreenHeader eyebrow="Protocol" title="Schedule" /><View style={styles.center}><ActivityIndicator color={colors.primary} /><Small style={{ marginTop: 10 }}>Loading approved schedule…</Small></View></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Protocol schedule" title="Visit Schedule" />
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {error && !schedule ? (
          <Card style={styles.emptyCard}>
            <AlertTriangle size={30} color={colors.warning} />
            <Body weight="700" style={styles.emptyTitle}>Schedule not available</Body>
            <Small style={styles.centerText}>{error}</Small>
            <Button onPress={resolveVersion} variant="secondary" style={styles.wideButton}>Try again</Button>
            {__DEV__ && <Pressable onPress={() => router.push("/(app)/dev/uctsm-workbench" as never)} style={styles.devLink}><FlaskConical size={14} color={colors.primary} /><Text style={styles.devText}>Open development workbench</Text></Pressable>}
          </Card>
        ) : schedule ? (
          <>
            <View style={styles.summaryRow}>
              <View style={{ flex: 1 }}>
                <Body weight="700" style={styles.summaryTitle}>{first(params.trialName) || schedule.schedule_metadata.name}</Body>
                <Small>{rows.length} visits · {first(params.protocolVersion) || `Version ${schedule.schedule_metadata.version_number}`}</Small>
              </View>
              <View>
                <View style={[styles.statusPill, schedule.schedule_metadata.status === "APPROVED" && styles.approvedPill]}>
                  {schedule.schedule_metadata.status === "APPROVED" ? <ShieldCheck size={14} color={colors.success} /> : <RefreshCw size={14} color={colors.warning} />}
                  <Text style={[styles.statusText, schedule.schedule_metadata.status === "APPROVED" && styles.approvedText]}>{statusLabel ? statusLabel[0].toUpperCase() + statusLabel.slice(1) : ""}</Text>
                </View>
              </View>
            </View>

            {!!error && <Notice error text={error} />}
            {!!message && <Notice text={message} />}

            <Small>Tap a visit for activities, notes and source details.</Small>
            <SimpleScheduleTable rows={rows} onEvidence={openEvidence} />
            <Pressable accessibilityRole="button" accessibilityState={{ expanded: showChecks }}
              onPress={() => setShowChecks((value) => !value)} style={{ paddingVertical: 12 }}>
              <Small color={blocking ? "#87591F" : colors.primary}>
                {showChecks ? "Hide" : "Show"} schedule checks{openIssues.length ? ` (${openIssues.length})` : ""}
              </Small>
            </Pressable>
            {showChecks && <>
            <Card style={styles.validationCard}>
              <View style={styles.validationHead}>
                {blocking ? <AlertTriangle size={21} color={colors.warning} /> : <CheckCircle2 size={21} color={colors.success} />}
                <View style={{ flex: 1 }}>
                  <Body weight="700">Validation</Body>
                  <Small>{openIssues.length ? `${openIssues.length} item(s) require attention; ${blocking} block approval.` : "No open validation issues."}</Small>
                </View>
              </View>
              {openIssues.map((issue) => <View key={issue.id} style={styles.issue}>
                <Text style={styles.issueText}>{issue.blocking ? "Blocks approval: " : "Review: "}{issue.message}</Text>
                {issue.details.origin === "EXTRACTION" && !["SCHEDULE_OPTION_FAILED", "SCHEDULE_DISCOVERY_FAILED"].includes(issue.issue_code) && canReview && <>
                  <TextInput value={issueReasons[issue.id] || ""}
                    onChangeText={(value) => setIssueReasons((current) => ({ ...current, [issue.id]: value }))}
                    placeholder="Describe the source review or correction that resolves this finding"
                    placeholderTextColor={colors.mutedFg} style={styles.bulkConfirmInput} />
                  <Button variant="secondary" disabled={!issueReasons[issue.id]?.trim() || !!busy}
                    onPress={() => run("resolve", async () => {
                      await resolveExtractionIssue(schedule.schedule_version_id, issue.id, issueReasons[issue.id].trim());
                      setMessage("Source review recorded. Structural errors still need correction before approval.");
                    })}>Record source resolution</Button>
                </>}
              </View>)}
            </Card>

            {!!unstatedModeCount && (
              <Card style={styles.validationCard}>
                <View style={styles.validationHead}>
                  <AlertTriangle size={21} color={colors.warning} />
                  <View style={{ flex: 1 }}>
                    <Body weight="700">Visit mode needs review</Body>
                    <Small>
                      {unstatedModeCount} visit{unstatedModeCount === 1 ? "" : "s"} do
                      not state how the patient attends - resolve these before
                      approval so a reminder never guesses.
                    </Small>
                  </View>
                </View>
              </Card>
            )}

            <View style={styles.sectionHead}>
              <View><Body weight="700" style={styles.sectionTitle}>Protocol Schedule</Body><Small>{rows.length} visits and protocol events</Small></View>
              {canReview && <Button onPress={validate} loading={busy === "validate"} variant="secondary" style={styles.smallButton}>Validate</Button>}
              {/* Doc 4 s7 and doc 8 s33. The decisions that block approval live
                  on their own screen, because they need explanation rather than
                  a row in a table. */}
              <Button
                variant="secondary"
                style={styles.smallButton}
                onPress={() => router.push({
                  pathname: "/(app)/clinical/protocol-review",
                  params: { scheduleVersionId: schedule.schedule_version_id },
                } as never)}>
                Review rules
              </Button>
            </View>
            </>}

            {canReview && <Button
              onPress={() => { setShowApproval((value) => !value); if (blocking) setShowChecks(true); }}>
              {showApproval ? "Close approval" : blocking ? "Resolve items before approval" : "Review and approve"}
            </Button>}

            {showApproval && canReview && (
              <Card style={styles.reviewCard}>
                <View style={styles.reviewTitle}><ClipboardCheck size={20} color={colors.primary} /><View style={{ flex: 1 }}><Body weight="700">Review and approval</Body><Small>Review the table and evidence before approving this version.</Small></View></View>
                <View style={styles.actions}>
                <View style={styles.bulkConfirmBlock}>
                  <Small style={styles.bulkConfirmLabel}>
                    This confirms the whole table in one action. Check the visits, activities and source notes first.
                  </Small>
                  <TextInput
                    value={bulkReason}
                    onChangeText={setBulkReason}
                    placeholder="Add a short note about your review"
                    placeholderTextColor={colors.mutedFg}
                    style={styles.bulkConfirmInput}
                  />
                  <Button
                    onPress={approve}
                    loading={busy === "approve"}
                    disabled={blocking > 0 || !bulkReason.trim() || !!busy}
                    style={styles.actionButton}>
                    Confirm table and approve
                  </Button>
                </View>
                </View>
                <Pressable accessibilityRole="button" onPress={() => setShowReject((value) => !value)} style={{ paddingVertical: 10 }}>
                  <Small color={colors.primary}>{showReject ? "Hide" : "Show"} other review options</Small>
                </Pressable>
                {showReject && <>
                  {["DRAFT", "EXTRACTED", "VALIDATION_REQUIRED", "REJECTED"].includes(schedule.schedule_metadata.status) && <Button onPress={submit} loading={busy === "submit"} variant="secondary">Send for review</Button>}
                {schedule.schedule_metadata.status === "IN_REVIEW" && (
                  <View style={styles.bulkConfirmBlock}>
                    <Small style={styles.bulkConfirmLabel}>Reject instead</Small>
                    <TextInput
                      value={rejectReason}
                      onChangeText={setRejectReason}
                      placeholder="Why is this schedule being rejected? (recorded in the audit trail)"
                      placeholderTextColor={colors.mutedFg}
                      style={styles.bulkConfirmInput}
                    />
                    <Button
                      onPress={reject}
                      loading={busy === "reject"}
                      variant="secondary"
                      style={styles.actionButton}>
                      Reject schedule
                    </Button>
                  </View>
                )}
                </>}
              </Card>
            )}
          </>
        ) : null}
      </ScrollView>
      <Modal visible={sourceText !== null} transparent onRequestClose={() => setSourceText(null)}>
        <View style={{ flex: 1, padding: 24, justifyContent: "center", backgroundColor: "rgba(0,0,0,0.4)" }}>
          <Card style={{ maxHeight: "80%" }}>
            <Body weight="700">Protocol evidence</Body>
            <ScrollView><Text style={{ color: colors.foreground, paddingVertical: 16 }}>{sourceText}</Text></ScrollView>
            <Button onPress={() => setSourceText(null)}>Close</Button>
          </Card>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

function formatEvidence(item: Evidence) {
  return [
    item.page_number ? `Page ${item.page_number}` : null,
    item.section_title ? `Section: ${item.section_title}` : null,
    item.table_title ? `Table: ${item.table_title}` : null,
    item.source_text || null,
  ].filter(Boolean).join("\n");
}
function Notice({ text, error = false }: { text: string; error?: boolean }) { return <View style={[styles.notice, error && styles.errorNotice]}>{error ? <AlertTriangle size={16} color={colors.destructive} /> : <CheckCircle2 size={16} color={colors.success} />}<Small color={error ? colors.destructive : colors.success} style={{ flex: 1 }}>{text}</Small></View>; }

const styles = StyleSheet.create({
  content: { padding: spacing.md, paddingBottom: 50, gap: 14 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  centerText: { textAlign: "center", marginTop: 6 },
  emptyCard: { alignItems: "center", paddingVertical: 30 },
  emptyTitle: { marginTop: 10 },
  wideButton: { width: "100%", marginTop: 18 },
  devLink: { flexDirection: "row", gap: 6, alignItems: "center", marginTop: 18 },
  devText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  summaryRow: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  summaryMain: { flex: 1, minWidth: 230 },
  summaryTitle: { marginVertical: 4, fontSize: 18 },
  statusCard: { minWidth: 175 },
  statusPill: { marginTop: 8, paddingHorizontal: 10, paddingVertical: 7, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "#FFF0D6", alignSelf: "flex-start" },
  approvedPill: { backgroundColor: "#E7F4EA" },
  statusText: { color: "#855416", fontFamily: fonts.semibold, fontSize: 11 },
  approvedText: { color: "#326A43" },
  validationCard: { gap: 9 },
  validationHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  issue: { padding: 9, borderRadius: radii.sm, backgroundColor: "#FFF7E8" },
  issueText: { color: "#754A16", fontFamily: fonts.regular, fontSize: 12 },
  sectionHead: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 10, marginTop: 5 },
  sectionTitle: { fontSize: 20 },
  smallButton: { minWidth: 110, paddingHorizontal: 12 },
  reviewCard: { gap: 13 },
  reviewTitle: { flexDirection: "row", alignItems: "center", gap: 10 },
  actions: { gap: 10 },
  actionButton: { minWidth: 180, flexGrow: 1 },
  bulkConfirmBlock: { gap: 8, marginTop: 4, minWidth: 0, width: "100%" },
  bulkConfirmLabel: { fontStyle: "italic" },
  bulkConfirmInput: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    padding: 10, color: colors.foreground,
  },
  notice: { flexDirection: "row", alignItems: "center", gap: 8, padding: 11, borderWidth: 1, borderColor: "#BFDCC6", backgroundColor: "#EFF8F1", borderRadius: radii.md },
  errorNotice: { borderColor: "#EBC0BB", backgroundColor: "#FFF1EF" },
});
