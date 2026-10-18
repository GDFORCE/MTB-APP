import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle, CheckCircle2, FlaskConical, ShieldCheck,
} from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  addScheduleVisit, approveScheduleVersion, buildCanonicalSchedule, decideSchedule,
  editScheduleVisit, exportScheduleVersion, getApprovedSchedules, getMyCapabilities, getReviewView,
  resolveReviewQuestion, reviewScheduleTracks, submitScheduleReview,
} from "./api";
import {
  DocumentIssues, FilterChips, SharedQuestionsPanel, SourceCoveragePanel, VisitScheduleSummary, VisitTable,
  type QuestionDraft,
} from "./VisitScheduleReview";
import { TrackReviewSheet } from "./TrackReviewSheet";
import { AddVisitSheet, EditVisitSheet } from "./VisitScheduleSheets";
import {
  approvalState, buildQuestionRequest, exportFileName, isStaleRevision, recoveryNotice,
  reviewErrorMessage, type ReviewRow, type ReviewView, type RowFilter, type SharedQuestion,
} from "./visitSchedule";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
const EDITABLE = ["DRAFT", "EXTRACTED", "VALIDATION_REQUIRED", "REJECTED", "IN_REVIEW"];

/**
 * The draft Visit Schedule (design doc "Required user-facing output").
 *
 * Reached after Add Trial and from the trial's Protocol & Schedule Versions.
 * Everything shown comes from the backend review view: row status, timing and
 * window text, shared questions and - crucially - whether approval is allowed.
 * Every write carries the revision the reviewer saw, so a concurrent change is
 * refused and the screen reloads instead of approving something unseen.
 */
export default function ProtocolScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    id?: string; scheduleVersionId?: string; schedule_version_id?: string; trialName?: string;
  }>();
  const trialId = first(params.id);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [view, setView] = useState<ReviewView | null>(null);
  const [busy, setBusy] = useState("load");
  // Informational only (the server rechecks): hides the rule-correction control
  // from an account that lacks schedule.correct. Unknown (null) = show it.
  const [canCorrect, setCanCorrect] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    getMyCapabilities().then(
      (mine) => { if (live) setCanCorrect("schedule.correct" in mine.capabilities); },
      () => { if (live) setCanCorrect(null); });
    return () => { live = false; };
  }, []);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState<RowFilter>("ALL");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<ReviewRow | null>(null);
  const [adding, setAdding] = useState(false);
  const [approvalNote, setApprovalNote] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [showOther, setShowOther] = useState(false);

  const load = useCallback(async (id: string) => {
    setView(await getReviewView(id));
  }, []);

  const resolveVersion = useCallback(async () => {
    setBusy("load"); setError("");
    try {
      let id = first(params.scheduleVersionId) || first(params.schedule_version_id);
      if (!id && trialId) {
        try { id = (await getApprovedSchedules(trialId))[0]?.schedule_version_id; }
        catch { /* A legacy trial has no canonical schedule mapping yet. */ }
      }
      if (!id && __DEV__) id = await AsyncStorage.getItem("uctsm:last_schedule_version_id") || undefined;
      if (!id) throw new Error("No schedule version is linked to this trial yet.");
      setVersionId(id);
      await load(id);
    } catch (nextError) {
      setError(reviewErrorMessage(nextError, "The Visit Schedule could not be loaded."));
    } finally { setBusy(""); }
  }, [params.scheduleVersionId, params.schedule_version_id, trialId, load]);

  useEffect(() => { void resolveVersion(); }, [resolveVersion]);

  /** Runs one write; a stale revision reloads the draft and says so. */
  const write = async (label: string, action: () => Promise<string | void>): Promise<boolean> => {
    if (!versionId || busy) return false;
    setBusy(label); setError(""); setMessage("");
    try {
      const done = await action();
      if (done) setMessage(done);
      await load(versionId);
      return true;
    } catch (nextError) {
      setError(reviewErrorMessage(nextError));
      if (isStaleRevision(nextError)) await load(versionId).catch(() => undefined);
      return false;
    } finally { setBusy(""); }
  };

  const onEdit = (row: ReviewRow, body: Record<string, unknown>) => write("edit", async () => {
    const result = await editScheduleVisit(versionId!, row.id, body);
    return `${row.visit_name} saved and revalidated · ${result.changed_row_ids.length} row(s) changed.`;
  });
  const [tracking, setTracking] = useState(false);
  const onTracks = (body: Record<string, unknown>) => write("tracks", async () => {
    await reviewScheduleTracks(versionId!, body);
    return "Track rules saved and revalidated.";
  });
  const onAdd = (body: Record<string, unknown>) => write("add", async () => {
    await addScheduleVisit(versionId!, body);
    return "Visit added as a manual entry and revalidated.";
  });
  const onResolve = (question: SharedQuestion, draft: QuestionDraft) => {
    const built = buildQuestionRequest(question, draft, view!.revision);
    if (built.error) { setError(built.error); return; }
    void write("resolve", async () => {
      await resolveReviewQuestion(versionId!, question.id, built.body!);
      return `Answered once for ${question.affected_rows.length} visit(s).`;
    });
  };
  const onApprove = () => write("approve", async () => {
    await approveScheduleVersion(versionId!, view!.revision, approvalNote.trim());
    setApprovalNote("");
    return "Schedule approved. Patients can now be enrolled onto this version.";
  });
  const onSubmitForReview = () => write("submit", async () => {
    await submitScheduleReview(versionId!);
    return "Schedule sent for review.";
  });
  const onReject = () => write("reject", async () => {
    if (!rejectReason.trim()) throw new Error("Say why this schedule is being rejected.");
    await decideSchedule(versionId!, "REJECT", rejectReason.trim());
    setRejectReason("");
    return "Schedule rejected. It stays editable for correction or re-extraction.";
  });
  const onRetryBuild = () => write("build", async () => {
    if (!trialId) throw new Error("Open this schedule from its trial to retry the build.");
    const built = await buildCanonicalSchedule(trialId);
    const next = built.schedules[0]?.schedule_version_id;
    if (next && next !== versionId) { setVersionId(next); await load(next); }
    return "Schedule rebuilt from the extracted protocol.";
  });

  const onExport = async (format: "json" | "csv") => {
    if (!versionId || !view || busy) return;
    setBusy(`export-${format}`); setError(""); setMessage("");
    try {
      const text = await exportScheduleVersion(versionId, format);
      const filename = exportFileName(view, format);
      const mime = format === "json" ? "application/json" : "text/csv";
      if (Platform.OS === "web") {
        const g: any = globalThis;
        const url = g.URL.createObjectURL(new g.Blob([text], { type: mime }));
        const anchor = g.document.createElement("a");
        anchor.href = url; anchor.download = filename; anchor.click();
        g.URL.revokeObjectURL(url);
      } else {
        const uri = `${FileSystem.cacheDirectory}${filename}`;
        await FileSystem.writeAsStringAsync(uri, text, { encoding: FileSystem.EncodingType.UTF8 });
        if (!await Sharing.isAvailableAsync()) throw new Error("Sharing is not available on this device.");
        await Sharing.shareAsync(uri, { mimeType: mime, dialogTitle: `Share ${filename}` });
      }
      setMessage(`Exported ${filename}.`);
    } catch (nextError) {
      setError(reviewErrorMessage(nextError, "The export could not be created."));
    } finally { setBusy(""); }
  };

  const showRow = (rowId: string) => {
    setFilter("ALL");
    setExpanded((current) => ({ ...current, [rowId]: true }));
  };

  if (busy === "load") {
    return <ScreenContainer><ScreenHeader eyebrow="Protocol" title="Visit Schedule" />
      <View style={styles.center}><ActivityIndicator color={colors.primary} /><Small style={{ marginTop: 10 }}>Loading the Visit Schedule…</Small></View>
    </ScreenContainer>;
  }

  const editable = !!view && EDITABLE.includes(view.status);
  const approval = view ? approvalState(view) : { enabled: false, reason: "" };
  const recovery = view ? recoveryNotice(view) : null;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={first(params.trialName) || "Protocol schedule"} title="Visit Schedule" />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {!view ? (
          <Card style={styles.emptyCard}>
            <AlertTriangle size={30} color={colors.warning} />
            <Body weight="700" style={{ marginTop: 10 }}>Visit Schedule not available</Body>
            <Small style={styles.centerText}>{error}</Small>
            <Button onPress={resolveVersion} variant="secondary" style={styles.wide}>Try again</Button>
            {!!trialId && <Button variant="secondary" style={styles.wide} onPress={() => router.push({
              pathname: "/(app)/sponsor/schedule-versions", params: { id: trialId },
            } as never)}>Open Protocol & Schedule Versions</Button>}
            {__DEV__ && <Pressable onPress={() => router.push("/(app)/dev/uctsm-workbench" as never)} style={styles.devLink}>
              <FlaskConical size={14} color={colors.primary} /><Text style={styles.devText}>Open development workbench</Text>
            </Pressable>}
          </Card>
        ) : <>
          <VisitScheduleSummary view={view} />
          {!!error && <Notice error text={error} />}
          {!!message && <Notice text={message} />}
          {!!recovery && <Card style={styles.recovery}>
            <View style={styles.recoveryHead}>
              <AlertTriangle size={20} color={colors.warning} />
              <Body weight="700" style={{ flex: 1 }}>Extraction needs attention</Body>
            </View>
            <Small>{recovery}</Small>
            {view.extraction_status.retry_available && <View style={styles.buttonRow}>
              {!!trialId && <Button variant="secondary" onPress={onRetryBuild} loading={busy === "build"} style={styles.grow}>
                Retry schedule build
              </Button>}
              {!!trialId && <Button variant="secondary" style={styles.grow} onPress={() => router.push({
                pathname: "/(app)/sponsor/schedule-versions", params: { id: trialId },
              } as never)}>Upload the protocol again</Button>}
            </View>}
          </Card>}

          <SourceCoveragePanel coverage={view.source_coverage} />
          <DocumentIssues issues={view.document_level_issues} />
          <SharedQuestionsPanel questions={view.shared_review_questions} onResolve={onResolve}
            onShowRow={showRow} busy={!!busy} editable={editable} />

          <FilterChips rows={view.rows} value={filter} onChange={setFilter} />
          <VisitTable view={view} filter={filter} expanded={expanded}
            onToggle={(rowId) => setExpanded((current) => ({ ...current, [rowId]: !current[rowId] }))}
            onEdit={(row) => setEditing(row)} editable={editable} />

          <View style={styles.buttonRow}>
            {editable && <Button variant="secondary" onPress={() => setAdding(true)} style={styles.grow}>
              + Add Visit
            </Button>}
            {editable && canCorrect !== false && <Button variant="secondary" onPress={() => setTracking(true)} style={styles.grow}>
              Tracks and rules
            </Button>}
            <Button variant="secondary" onPress={() => onExport("json")} loading={busy === "export-json"} style={styles.grow}>
              Export JSON
            </Button>
            <Button variant="secondary" onPress={() => onExport("csv")} loading={busy === "export-csv"} style={styles.grow}>
              Export CSV
            </Button>
          </View>
          <Pressable accessibilityRole="link" style={styles.linkRow} onPress={() => router.push({
            pathname: "/(app)/clinical/protocol-review", params: { scheduleVersionId: versionId! },
          } as never)}>
            <Text style={styles.link}>Review footnotes and dependency rules</Text>
          </Pressable>

          {view.status === "APPROVED" ? (
            <View style={styles.approved}><ShieldCheck size={18} color={colors.success} />
              <Small color={colors.success} style={{ flex: 1 }}>This version is approved and can no longer be edited.</Small>
            </View>
          ) : editable && <Card style={styles.approval}>
            <Body weight="700">Approve this version</Body>
            <Small>Approval covers the whole schedule at the revision shown here. MTB rechecks every gate before approving.</Small>
            <TextInput value={approvalNote} onChangeText={setApprovalNote} accessibilityLabel="Approval note"
              placeholder="Add a short note about your review (recorded)" placeholderTextColor={colors.mutedFg}
              style={styles.input} multiline />
            <Button onPress={onApprove} loading={busy === "approve"}
              disabled={!approval.enabled || !approvalNote.trim() || !!busy}>Approve schedule</Button>
            {!approval.enabled && <View style={styles.blocked} accessibilityLiveRegion="polite">
              <AlertTriangle size={15} color={colors.warning} />
              <Small style={{ flex: 1 }}>Approval unavailable: {approval.reason}</Small>
            </View>}
            <Pressable accessibilityRole="button" accessibilityState={{ expanded: showOther }}
              onPress={() => setShowOther((value) => !value)} style={styles.linkRow}>
              <Text style={styles.link}>{showOther ? "Hide" : "Show"} other review options</Text>
            </Pressable>
            {showOther && <View style={{ gap: 8 }}>
              {view.status !== "IN_REVIEW" && <Button variant="secondary" onPress={onSubmitForReview} loading={busy === "submit"}>Send for review</Button>}
              {view.status === "IN_REVIEW" && <>
                <TextInput value={rejectReason} onChangeText={setRejectReason} accessibilityLabel="Rejection reason"
                  placeholder="Why is this schedule being rejected? (recorded)" placeholderTextColor={colors.mutedFg} style={styles.input} />
                <Button variant="secondary" onPress={onReject} loading={busy === "reject"}>Reject schedule</Button>
              </>}
            </View>}
          </Card>}
        </>}
      </ScrollView>
      {!!view && <EditVisitSheet row={editing} view={view} busy={busy === "edit"} onClose={() => setEditing(null)} onSubmit={onEdit} canCorrect={canCorrect !== false} />}
      {!!view && <TrackReviewSheet visible={tracking} view={view} busy={busy === "tracks"}
        onClose={() => setTracking(false)} onSubmit={onTracks} />}
      {!!view && <AddVisitSheet visible={adding} view={view} busy={busy === "add"} onClose={() => setAdding(false)} onSubmit={onAdd} />}
    </ScreenContainer>
  );
}

function Notice({ text, error = false }: { text: string; error?: boolean }) {
  return <View style={[styles.notice, error && styles.errorNotice]} accessibilityLiveRegion="polite">
    {error ? <AlertTriangle size={16} color={colors.destructive} /> : <CheckCircle2 size={16} color={colors.success} />}
    <Small color={error ? colors.destructive : colors.success} style={{ flex: 1 }}>{text}</Small>
  </View>;
}

const styles = StyleSheet.create({
  content: { padding: spacing.md, paddingBottom: 60, gap: 14 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  centerText: { textAlign: "center", marginTop: 6 },
  emptyCard: { alignItems: "center", paddingVertical: 30, gap: 4 },
  wide: { width: "100%", marginTop: 12 },
  devLink: { flexDirection: "row", gap: 6, alignItems: "center", marginTop: 18 },
  devText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  recovery: { gap: 8, borderColor: "#EBCB95" },
  recoveryHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  buttonRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  grow: { flexGrow: 1, minWidth: 140 },
  linkRow: { minHeight: 44, justifyContent: "center" },
  link: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 13 },
  approval: { gap: 10 },
  approved: { flexDirection: "row", gap: 8, alignItems: "center", padding: 12, borderRadius: radii.md, backgroundColor: "#E7F4EA" },
  blocked: { flexDirection: "row", gap: 8, alignItems: "flex-start", padding: 10, borderRadius: radii.md, backgroundColor: "#FDF3E1" },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground, minHeight: 44 },
  notice: { flexDirection: "row", alignItems: "center", gap: 8, padding: 11, borderWidth: 1, borderColor: "#BFDCC6", backgroundColor: "#EFF8F1", borderRadius: radii.md },
  errorNotice: { borderColor: "#F2C9C9", backgroundColor: "#FDECEC" },
});
