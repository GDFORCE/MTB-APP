import React, { useEffect, useState } from "react";
import { ActivityIndicator, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { useAuth } from "@/src/auth/AuthContext";
import { downloadProtocolFile, getKitDraft, getKitModel, saveKitModel, setKitStudyDate, startKitAnalysis, waitForKitJob,
  type KitWorkflowInput, type SavedKitModel } from "@/src/features/kit/api";
import { KitQuestionsModal } from "@/src/features/kit/KitQuestionsModal";
import { isIsoDate, type KitAnalysis } from "@/src/features/kit";
import { WorkflowReview, useWorkflowPreview, workflowError } from "@/src/features/kit/WorkflowReview";
import { FlowButton, FlowScreen, flowStyles as f } from "@/src/features/kit/FlowScreen";
import { ProtocolTimeline, useProtocolTimeline } from "@/src/features/kit/ProtocolTimeline";
import { FOCUS as G } from "@/src/features/kit/focus";
import { OpenPointsList } from "@/src/features/kit/OpenPointsList";
import { openPointViews } from "@/src/features/kit/openPoints";
import { blockedPatternRules } from "@/src/features/kit/patterns";
import { StudyEventsPanel } from "@/src/features/kit/StudyEventsPanel";

// The reviewed visit schedule saved for a trial by the protocol upload in Add Trial.
export default function KitScheduleScreen() {
  const router = useRouter();
  const { id, trialName } = useLocalSearchParams<{ id: string; trialName?: string }>();
  const [saved, setSaved] = useState<SavedKitModel | null>(null);
  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const { user } = useAuth();
  const canReview = ["sponsor", "cro", "pi"].includes(user?.role || "");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [analysis, setAnalysis] = useState<{ value: KitAnalysis; fileName: string; jobId: string } | null>(null);
  const [draft, setDraft] = useState<KitWorkflowInput | null>(null);
  const [dateEdits, setDateEdits] = useState<Record<string, string>>({});
  const preview = useWorkflowPreview(draft);
  const timeline = useProtocolTimeline(saved?.model, saved?.site_answers);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSaved(null);
    setDraft(null);
    setError("");
    Promise.allSettled([getKitModel(String(id)), canReview ? getKitDraft(String(id)) : Promise.resolve(null)])
      .then(([approved, pending]) => {
        if (cancelled) return;
        if (approved.status === "fulfilled") setSaved(approved.value);
        else if (approved.reason?.response?.status !== 404) setError(workflowError(approved.reason));
        if (pending.status === "fulfilled" && pending.value) {
          const d = pending.value;
          setDraft({ baseModel: d.base_model, siteAnswers: d.site_answers, reviewAnswers: d.review_answers,
            fileName: d.file_name || undefined, fileSha256: d.file_sha256, expectedVersion: d.expected_version,
            warnings: d.analysis_warnings, corrections: d.corrections, blockEdits: d.block_edits, resolutions: d.resolutions });
        } else if (pending.status === "rejected" && pending.reason?.response?.status !== 404) setError(workflowError(pending.reason));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [id, canReview]);

  const upload = async () => {
    setError("");
    try {
      const picked = await DocumentPicker.getDocumentAsync({ type: ["application/pdf", "text/plain"], copyToCacheDirectory: true });
      if (picked.canceled) return;
      setBusy("Analyzing the revised protocol…");
      const file = picked.assets[0];
      const job = await startKitAnalysis(file, saved?.model.protocol.number || "");
      const value = await waitForKitJob(job.job_id, () => {});
      const next = { value, fileName: file.name, jobId: job.job_id };
      if (value.baseModel.review_questions.length || value.baseModel.questions.some((q) => q.scope === "study")) setAnalysis(next);
      else prepareDraft(next, {}, {});
    } catch (e) {
      setError(workflowError(e));
    } finally {
      setBusy("");
    }
  };

  const prepareDraft = (next: NonNullable<typeof analysis>, reviewAnswers: Record<string, string>, siteAnswers: Record<string, string>) => {
    const { value, fileName, jobId } = next;
    setDraft({ baseModel: value.baseModel, siteAnswers, reviewAnswers, fileName, fileSha256: value.fileSha256,
      expectedVersion: saved?.version || (saved ? 1 : 0), warnings: value.warnings, corrections: value.corrections, analysisJobId: jobId });
    setAnalysis(null);
  };

  const save = async (approve: boolean) => {
    if (!draft || (approve && (!preview?.current || !preview.preview || preview.preview.problems.length))) return;
    setError("");
    setBusy(approve ? "Approving workflow…" : "Saving draft…");
    try {
      await saveKitModel(String(id), { ...draft, workflowAction: approve ? "approve" : "draft", workflowDigest: preview?.preview?.digest });
      if (approve) {
        setSaved(await getKitModel(String(id)));
        setDraft(null);
      }
    } catch (e) {
      setError(workflowError(e));
    } finally {
      setBusy("");
    }
  };

  const saveStudyDate = async (anchorId: string, value: string | null) => {
    if (!saved?.version) return;
    setError("");
    setBusy("Saving study date…");
    try {
      await setKitStudyDate(String(id), saved.version, anchorId, value, saved.study_revision || 0);
      setSaved(await getKitModel(String(id)));
      setDateEdits((prior) => {
        const next = { ...prior };
        delete next[anchorId];
        return next;
      });
    } catch (e) {
      setError(workflowError(e));
    } finally {
      setBusy("");
    }
  };

  return <FlowScreen title="Visit schedule" onBack={() => router.back()} eyebrow={trialName || "Trial"}
    heading={draft ? `Draft workflow version ${(draft.expectedVersion ?? 0) + 1}` : saved?.workflow ? `Approved workflow version ${saved.version}` : timeline?.title}
    sub={timeline ? `Each patient's dates count from their own ${timeline.startLabel}.${saved?.file_name ? ` From ${saved.file_name}.` : ""}` : undefined}
    footer={saved ? <FlowButton label="Open trial" variant="secondary" onPress={() => router.replace({
      pathname: "/(app)/clinical/trial-summary", params: { id: String(id) } })} /> : undefined}>
    {loading && <ActivityIndicator color={G.accent} style={{ marginTop: 32 }} />}
    {!!busy && <Text accessibilityLiveRegion="polite" style={f.hint}>{busy}</Text>}
    {!!error && <Text style={[f.error, { marginTop: 16 }]}>{error}</Text>}
    {!loading && canReview && <FlowButton label={saved ? "Upload an amendment" : "Upload protocol"}
      variant="secondary" disabled={!!busy} onPress={upload} />}
    {!!analysis && <KitQuestionsModal visible model={analysis.value.model} baseModel={analysis.value.baseModel}
      reviewAnswers={{}} siteAnswers={{}} onDismiss={() => setAnalysis(null)}
      onConfirm={(confirmed) => prepareDraft(analysis, confirmed.reviewAnswers, confirmed.siteAnswers)} />}
    {saved && !timeline && <Text style={[f.error, { marginTop: 16 }]}>This schedule could not be laid out.</Text>}
    {saved?.file_stored && <Text accessibilityRole="button" style={[f.hint, { color: G.accent, marginTop: 8 }]}
      onPress={downloading ? undefined : async () => {
        setDownloading(true); setFileError("");
        try { await downloadProtocolFile(String(id), saved.file_name || "protocol.pdf"); }
        catch (e: any) { setFileError(e?.response?.status === 404 ? "The protocol file was not stored." : "Couldn't download the protocol file."); }
        finally { setDownloading(false); }
      }}>{downloading ? "Downloading…" : `Download the protocol (${saved.file_name || "PDF"})`}{saved.version ? ` · model version ${saved.version}` : ""}</Text>}
    {!!fileError && <Text style={f.error}>{fileError}</Text>}
    {!draft && saved?.workflow && saved.model.anchors.filter((a) => a.scope === "study").map((a) => {
      const savedDate = saved.study_dates?.[a.id] || "";
      const typed = dateEdits[a.id] ?? savedDate;
      return <View key={a.id} style={{ gap: 5, marginVertical: 8 }}>
        <Text style={f.label}>{a.label}</Text>
        {user?.role === "pi" ? <>
          <TextInput value={typed} accessibilityLabel={a.label} style={f.input} placeholder="YYYY-MM-DD"
            onChangeText={(value) => setDateEdits((prior) => ({ ...prior, [a.id]: value }))} />
          <FlowButton label="Save study date" variant="secondary"
            disabled={!!busy || typed === savedDate || (typed !== "" && !isIsoDate(typed))}
            onPress={() => saveStudyDate(a.id, typed || null)} />
        </> : <Text style={f.hint}>{savedDate || "Pending input from the PI"}</Text>}
      </View>;
    })}
    {!!saved && !!blockedPatternRules(saved.model).length && <Text testID="kit-blocked" style={[f.error, { marginTop: 12 }]}>
      {`${blockedPatternRules(saved.model).length} protocol rule(s) the app cannot calculate yet: no patient can be enrolled until ${blockedPatternRules(saved.model).length === 1 ? "it is" : "they are"} supported. They are listed below with the protocol's words.`}
    </Text>}
    {draft && <>
      {!!preview?.error && <Text style={f.error}>{preview.error}</Text>}
      {preview?.preview && <WorkflowReview workflow={preview.preview.workflow} model={preview.preview.model}
        previous={saved?.workflow} site={draft.siteAnswers} resolutions={draft.resolutions}
        onEdit={(key, op) => setDraft((prior) => prior && { ...prior, blockEdits: { ...prior.blockEdits, [key]: op } })}
        onResolve={(key, value) => setDraft((prior) => prior && { ...prior, resolutions: { ...prior.resolutions, [key]: value } })} />}
      <FlowButton label="Approve workflow" disabled={!!busy || !preview?.current || !preview.preview || !!preview.preview.problems.length}
        onPress={() => save(true)} />
      <FlowButton label="Save workflow draft" variant="secondary" disabled={!!busy} onPress={() => save(false)} />
      <FlowButton label="Reset block edits" variant="secondary" disabled={!!busy || !Object.keys(draft.blockEdits || {}).length}
        onPress={() => setDraft((prior) => prior && { ...prior, blockEdits: {} })} />
    </>}
    {!draft && saved?.workflow && !!saved.model.study_events?.length && <StudyEventsPanel trialId={String(id)} role={user?.role || ""} />}
    {!draft && saved?.workflow && <WorkflowReview workflow={saved.workflow} model={saved.model} site={saved.site_answers} />}
    {!draft && !saved?.workflow && timeline && <ProtocolTimeline data={timeline} />}
    {!draft && !!saved && !saved.workflow && !!openPointViews(saved.model).length && <>
      <Text style={[f.label, { marginTop: 24 }]}>To check against the protocol</Text>
      <OpenPointsList points={openPointViews(saved.model)} />
    </>}
  </FlowScreen>;
}
