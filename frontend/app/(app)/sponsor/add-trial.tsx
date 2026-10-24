import React, { useMemo, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { ArrowRight, Check, FileText, Pencil, ShieldCheck, UploadCloud, X } from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import {
  KitAnalysisError, saveKitModel, startKitAnalysis, waitForKitJob, type KitStage,
} from "@/src/features/kit/api";
import { KitQuestionsModal, type KitConfirmed } from "@/src/features/kit/KitQuestionsModal";
import type { KitAnalysis, KitModel } from "@/src/features/kit";
import { FOCUS as G } from "@/src/features/kit/focus";
import { FlowButton, FlowScreen, FlowSheet, flowStyles as f } from "@/src/features/kit/FlowScreen";
import { ProtocolTimeline, useProtocolTimeline } from "@/src/features/kit/ProtocolTimeline";
import type { TimelineSection } from "@/src/features/kit/sections";
import { fonts } from "@/src/theme/tokens";

type Source = "registry" | "organization" | "upload";
type TrialStatus = "active" | "completed" | "terminated";
type Step = "id" | "analysing" | "details" | "schedule" | "saved";
type Details = {
  ctri_number: string;
  title: string;
  phase: string;
  indications: string[];
  drug: string;
  duration: string;
  target_enrollment: string;
  total_visits: string;
  status: TrialStatus;
};
const EMPTY: Details = {
  ctri_number: "",
  title: "",
  phase: "",
  indications: [],
  drug: "",
  duration: "",
  target_enrollment: "",
  total_visits: "",
  status: "active",
};
const PHASES = [
  "Phase 1",
  "Phase 1/Phase 2",
  "Phase 2",
  "Phase 2/Phase 3",
  "Phase 3",
  "Phase 3/Phase 4",
  "Phase 4",
  "Post Marketing Servilliance",
  "BA/BE",
  "Not applicable",
];

const PHASE_ALIASES: Record<string, string> = {
  phasei: "Phase 1",
  phase1: "Phase 1",
  "phasei/phaseii": "Phase 1/Phase 2",
  "phasei/ii": "Phase 1/Phase 2",
  "phase1/phase2": "Phase 1/Phase 2",
  "phase1/2": "Phase 1/Phase 2",
  phaseii: "Phase 2",
  phase2: "Phase 2",
  "phaseii/phaseiii": "Phase 2/Phase 3",
  "phaseii/iii": "Phase 2/Phase 3",
  "phase2/phase3": "Phase 2/Phase 3",
  "phase2/3": "Phase 2/Phase 3",
  phaseiii: "Phase 3",
  phase3: "Phase 3",
  "phaseiii/phaseiv": "Phase 3/Phase 4",
  "phaseiii/iv": "Phase 3/Phase 4",
  "phase3/phase4": "Phase 3/Phase 4",
  "phase3/4": "Phase 3/Phase 4",
  phaseiv: "Phase 4",
  phase4: "Phase 4",
  postmarketingsurveillance: "Post Marketing Servilliance",
  postmarketingservilliance: "Post Marketing Servilliance",
  "ba/be": "BA/BE",
  babe: "BA/BE",
  bioavailabilitybioequivalence: "BA/BE",
  notapplicable: "Not applicable",
  "n/a": "Not applicable",
  na: "Not applicable",
};

function canonicalPhase(value: unknown) {
  const raw = String(value || "").trim();
  const key = raw.toLowerCase().replace(/[^a-z0-9/]/g, "");
  return PHASE_ALIASES[key] || raw;
}
const STATUSES: { value: TrialStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "completed", label: "Completed" },
  { value: "terminated", label: "Terminated" },
];
const CREATE_TRIAL_RESPONSIBILITIES = [
  "You are authorized by your organization to create and manage this trial.",
  "You have completed the required training on applicable regulations, GCP, and platform usage.",
  "You are a delegated member of the study team or an authorized organizational representative.",
  "The information entered will be accurate and maintained in accordance with applicable regulatory requirements and your organization's SOPs.",
];

function normalized(raw: any): Details {
  return {
    ctri_number: raw?.ctri_number || "",
    title: raw?.title || "",
    phase: canonicalPhase(raw?.phase),
    indications: Array.isArray(raw?.indications) ? raw.indications.filter(Boolean) : [],
    drug: raw?.drug || "",
    duration: raw?.duration || "",
    target_enrollment: raw?.target_enrollment == null ? "" : String(raw.target_enrollment),
    total_visits: raw?.total_visits == null ? "" : String(raw.total_visits),
    status: ["active", "completed", "terminated"].includes(raw?.status)
      ? raw.status
      : "active",
  };
}

/** Section renames made on the schedule step, applied to a model by visit id. */
function withPhases<T extends KitModel>(model: T, renames: Record<string, string>): T {
  if (!Object.keys(renames).length) return model;
  return { ...model, visits: model.visits.map((v) => (renames[v.id] ? { ...v, phase: renames[v.id] } : v)) };
}

export default function AddTrial() {
  const router = useRouter();
  const { user } = useAuth();
  const scrollRef = useRef<ScrollView>(null);
  const analysisRun = useRef(0);
  const [step, setStep] = useState<Step>("id");
  const [protocolId, setProtocolId] = useState("");
  const [details, setDetails] = useState<Details>(EMPTY);
  const [source, setSource] = useState<Source | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [picked, setPicked] = useState<any>(null);
  // The protocol upload is analysed by the Protocol Visit Scheduler kit: its reviewed model is
  // what the schedule step shows and what Save stores.
  const [kit, setKit] = useState<KitAnalysis | null>(null);
  const [kitConfirmed, setKitConfirmed] = useState<KitConfirmed | null>(null);
  const [showKitQuestions, setShowKitQuestions] = useState(false);
  const [kitStages, setKitStages] = useState<KitStage[]>([]);
  const [uploadName, setUploadName] = useState("");
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [renaming, setRenaming] = useState<TimelineSection | null>(null);
  const [renameText, setRenameText] = useState("");
  const [showEdit, setShowEdit] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [createdTrialId, setCreatedTrialId] = useState<string | null>(null);
  const [showCreateConfirmation, setShowCreateConfirmation] = useState(true);

  const update = (patch: Partial<Details>) => setDetails((current) => ({ ...current, ...patch }));
  const go = (next: Step) => { setErr(""); setStep(next); scrollRef.current?.scrollTo({ y: 0, animated: false }); };

  const model = useMemo(() => {
    const current = kitConfirmed?.model ?? kit?.model;
    return current ? withPhases(current, renames) : null;
  }, [kit, kitConfirmed, renames]);
  const timeline = useProtocolTimeline(model, kitConfirmed?.siteAnswers);
  const questionCount = kit ? kit.model.review_questions.length + kit.model.questions.filter((q) => q.scope === "study").length : 0;

  const changeProtocolId = (value: string) => {
    setProtocolId(value);
    setSource(null);
    setDetails(EMPTY);
    setKit(null);
    setKitConfirmed(null);
    setRenames({});
    setErr("");
  };

  const lookup = async () => {
    const id = protocolId.trim();
    if (!id) return;
    setLookingUp(true);
    setErr("");
    try {
      const response = await api.get(`/protocols/lookup/${encodeURIComponent(id)}`);
      if (response.data?.found) {
        setProtocolId(response.data.protocol_id || id);
        setDetails(normalized(response.data.details));
        setSource(response.data.source === "organization" ? "organization" : "registry");
        go("details");
      } else {
        setPicked(null);
        setShowUpload(true);
      }
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "Could not look up this protocol. Try again.");
    } finally {
      setLookingUp(false);
    }
  };

  const pick = async () => {
    const result = await DocumentPicker.getDocumentAsync({ type: ["application/pdf", "text/plain"], copyToCacheDirectory: true });
    if (!result.canceled) { setPicked(result.assets[0]); setErr(""); }
  };

  const analyse = async () => {
    if (!picked) return;
    const run = ++analysisRun.current;
    setShowUpload(false);
    setKit(null);
    setKitConfirmed(null);
    setRenames({});
    setKitStages([]);
    setUploadName(picked.name || "protocol.pdf");
    go("analysing");
    try {
      // Only an identifier the sponsor actually entered is an expected identity; the file name never is.
      const started = await startKitAnalysis(picked, protocolId.trim());
      if (run !== analysisRun.current) return;
      setKitStages(started.stages);
      const analysis = await waitForKitJob(started.job_id, (job) => { if (run === analysisRun.current) setKitStages(job.stages); });
      if (run !== analysisRun.current) return;
      setKit(analysis);
      const found = normalized({
        title: analysis.details.title, phase: analysis.details.phase, indications: analysis.details.indications,
        total_visits: analysis.model.visits.length,
      });
      // A registry match keeps its details; the protocol only fills what is still empty.
      setDetails((current) => (source === "registry" || source === "organization")
        ? { ...current, title: current.title || found.title, phase: current.phase || found.phase,
            indications: current.indications.length ? current.indications : found.indications, total_visits: found.total_visits }
        : found);
      if (!protocolId.trim() && analysis.details.protocol_number) setProtocolId(analysis.details.protocol_number);
      if (!source) setSource("upload");
      const needsAnswers = analysis.model.review_questions.length > 0 || analysis.model.questions.some((q) => q.scope === "study");
      if (needsAnswers) setShowKitQuestions(true);
      else setKitConfirmed({ model: analysis.model, reviewAnswers: analysis.reviewAnswers, siteAnswers: {} });
      go(source === "registry" || source === "organization" ? "schedule" : "details");
    } catch (error: any) {
      if (run !== analysisRun.current) return;
      go("id");
      setShowUpload(true);
      setErr(error instanceof KitAnalysisError ? error.message
        : error?.response?.data?.detail || "We could not analyse this protocol. Check the file and try again.");
    }
  };

  const cancelAnalysis = () => { analysisRun.current += 1; go("id"); };

  const indications = details.indications.map((item) => item.trim()).filter(Boolean);
  const missingRequired = [
    !details.title.trim() ? "Title" : "", !details.phase ? "Phase" : "", !indications.length ? "Indication" : "",
  ].filter(Boolean);

  const save = async () => {
    if (missingRequired.length) { go("details"); setErr(`Add: ${missingRequired.join(", ")}.`); return; }
    if (kit && !kitConfirmed) { setShowKitQuestions(true); return; }
    setSaving(true);
    setErr("");
    try {
      const response = createdTrialId ? { data: { id: createdTrialId } } : await api.post("/trials", {
        title: details.title.trim(),
        protocol_id: protocolId.trim(),
        phase: details.phase,
        condition: indications.join(", "),
        indications,
        description: "",
        sponsor_name: "",
        drug: details.drug.trim(),
        duration: details.duration.trim(),
        target_enrollment: details.target_enrollment ? Number(details.target_enrollment) : null,
        total_visits: details.total_visits ? Number(details.total_visits) : null,
        ctri_number: details.ctri_number.trim(),
        status: details.status,
        recruitment_status: details.status === "active" ? "recruiting" : "closed",
      });
      const trialId = response.data.id;
      setCreatedTrialId(trialId);
      if (kit && kitConfirmed) {
        try {
          await saveKitModel(trialId, {
            baseModel: withPhases(kit.baseModel, renames), reviewAnswers: kitConfirmed.reviewAnswers,
            siteAnswers: kitConfirmed.siteAnswers, fileName: uploadName,
          });
        } catch (saveError: any) {
          const detail = saveError?.response?.data?.detail;
          setErr(`The trial was created, but its visit schedule could not be saved: ${
            typeof detail === "string" ? detail : detail?.message || saveError?.message || "unknown error"}. Press Save again to retry.`);
          return;
        }
      }
      go("saved");
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "Could not save this trial.");
    } finally {
      setSaving(false);
    }
  };

  const trialName = details.title.trim() || protocolId.trim();
  const openTrial = () => createdTrialId && router.replace({ pathname: "/(app)/clinical/trial-summary", params: { id: createdTrialId } });

  return <>
    {step === "id" && <FlowScreen title="Add trial" step={1} steps={5} onBack={() => router.back()} scrollRef={scrollRef}
      eyebrow="Protocol" heading="Enter the protocol ID"
      sub="If we have it on record, the details fill in. If not, you upload the protocol PDF.">
      <Text style={[f.label, { marginTop: 32 }]}>Protocol ID</Text>
      <View style={s.lookupRow}>
        <TextInput testID="trial-proto" value={protocolId} onChangeText={changeProtocolId} onSubmitEditing={lookup}
          placeholder="Enter protocol ID" placeholderTextColor={G.muted} autoCapitalize="characters"
          style={[f.input, s.lookupInput, !!protocolId && { borderColor: G.accent, borderWidth: 2 }]} />
        <Pressable testID="protocol-lookup" accessibilityRole="button" accessibilityLabel="Look up protocol"
          disabled={!protocolId.trim() || lookingUp} onPress={lookup}
          style={[s.lookupButton, (!protocolId.trim() || lookingUp) && { opacity: 0.4 }]}>
          {lookingUp ? <ActivityIndicator color="#FFFFFF" /> : <ArrowRight size={22} color="#FFFFFF" />}
        </Pressable>
      </View>
      <Text style={[f.hint, { marginTop: 10 }]}>For example an NCT number or your sponsor&apos;s study number.</Text>
      {!!err && !showUpload && <Text style={[f.error, { marginTop: 12 }]}>{err}</Text>}
    </FlowScreen>}

    {step === "analysing" && <FlowScreen title="Add trial" step={2} steps={5} scrollRef={scrollRef}
      eyebrow="Analysing" heading="Reading your protocol" sub={`${uploadName ? uploadName + " · " : ""}This usually takes 1 to 3 minutes.`}
      footer={<View style={s.analysingFooter}>
        <Text style={[f.hint, { flex: 1 }]}>Keep this screen open while we read it.</Text>
        <View style={{ width: 110 }}><FlowButton label="Cancel" variant="secondary" onPress={cancelAnalysis} /></View>
      </View>}>
      <View style={{ marginTop: 28 }}>
        {(kitStages.length ? kitStages : [{ id: "start", label: "Starting", status: "run", detail: "" } as KitStage]).map((stage, index, all) =>
          <View key={stage.id} style={s.stage}>
            <View style={s.stageRail}>
              {stage.status === "done"
                ? <View style={[s.stageDot, s.stageDone]}><Check size={14} color="#FFFFFF" strokeWidth={3} /></View>
                : stage.status === "run"
                  ? <View style={s.stageDot}><ActivityIndicator size="small" color={G.accent} /></View>
                  : <View style={[s.stageDot, s.stageWait]} />}
              {index < all.length - 1 && <View style={[s.stageLine, stage.status === "done" && { backgroundColor: G.accent }]} />}
            </View>
            <View style={s.stageText}>
              <Text style={[s.stageLabel, stage.status === "wait" && { color: G.muted, fontFamily: fonts.medium }]}>{stage.label}</Text>
              <Text style={f.hint}>{stage.status === "wait" ? "Waiting" : stage.detail || (stage.status === "run" ? "Working…" : "Done")}</Text>
            </View>
          </View>)}
      </View>
    </FlowScreen>}

    {step === "details" && <FlowScreen title="Add trial" step={4} steps={5} onBack={() => go("id")} scrollRef={scrollRef}
      eyebrow="Trial details" heading="Are these details right?"
      sub={source === "upload" ? "Read from the protocol. Fix anything that is wrong." : "Found on record. Fix anything that is wrong."}
      footer={<>
        {!!missingRequired.length && <Text style={[f.hint, { textAlign: "center" }]}>Add {missingRequired.join(", ").toLowerCase()} to continue</Text>}
        <FlowButton testID="details-continue" label="Looks right" arrow disabled={!!missingRequired.length} onPress={() => go("schedule")} />
      </>}>
      <View style={{ marginTop: 24 }}>
        {[
          ["Protocol ID", protocolId.trim(), false],
          ["Title", details.title.trim(), true],
          ["Phase", details.phase, true],
          ["Indication", indications.join(", "), true],
          ...(kit?.details.version ? [["Version", kit.details.version, false]] : []),
          ["Study drug", details.drug.trim(), false],
          ["Duration", details.duration.trim(), false],
          ["Sample size", details.target_enrollment, false],
          ["CTRI number", details.ctri_number.trim(), false],
          ["Status", STATUSES.find((x) => x.value === details.status)?.label || "", false],
        ].map(([label, value, required]) => <View key={String(label)} style={s.detailRow}>
          <Text style={s.detailLabel}>{label}</Text>
          <Text style={[s.detailValue, !value && { color: required ? G.error : G.amberText }]}>
            {value || (required ? "Required" : source === "upload" ? "Not in the protocol" : "Not given")}
          </Text>
        </View>)}
      </View>
      <Pressable testID="edit-details" accessibilityRole="button" onPress={() => setShowEdit(true)} style={s.editButton}>
        <Pencil size={16} color={G.accent} />
        <Text style={s.editText}>Edit details</Text>
      </Pressable>
      {!!err && <Text style={[f.error, { marginTop: 12 }]}>{err}</Text>}
    </FlowScreen>}

    {step === "schedule" && <FlowScreen title="Add trial" step={5} steps={5} onBack={() => go("details")} scrollRef={scrollRef}
      eyebrow="Visit schedule" heading={timeline ? timeline.title : "No visit schedule yet"}
      sub={timeline ? `Check the schedule, then save. Each patient's dates count from their own ${timeline.startLabel}.`
        : "Upload the protocol PDF to build the visit schedule, or save the trial without one."}
      footer={<>
        {!!err && <Text style={f.error}>{err}</Text>}
        <FlowButton testID="add-trial-submit" label="Save trial" loading={saving} onPress={save} />
      </>}>
      {timeline && kit ? <>
        <View style={s.checks}>
          <View style={[s.check, kit.corrections === null ? s.checkWarn : s.checkOk]}>
            {kit.corrections === null ? <X size={15} color={G.amberInk} /> : <Check size={15} color={G.accent} strokeWidth={2.5} />}
            <Text style={s.checkText}>
              {kit.corrections === null ? "Table check didn't run" : kit.corrections.length ? `${kit.corrections.length} fixed from the tables` : "Matches the tables"}
            </Text>
          </View>
          {!!questionCount && <Pressable accessibilityRole="button" onPress={() => setShowKitQuestions(true)} style={[s.check, s.checkLink]}>
            <Text style={s.checkText}>{questionCount} answer{questionCount === 1 ? "" : "s"}</Text>
            <Text style={s.linkText}>Review</Text>
          </Pressable>}
        </View>
        <ProtocolTimeline data={timeline} onRename={(section) => { setRenaming(section); setRenameText(section.label); }} />
        {(!!model?.notes.length || !!kit.warnings.length) && <Pressable accessibilityRole="button" onPress={() => setShowNotes(true)} style={s.notesRow}>
          <Text style={s.notesText}>{(model?.notes.length || 0) + kit.warnings.length} notes from the protocol</Text>
          <Text style={s.linkText}>Read</Text>
        </Pressable>}
      </> : <View style={{ marginTop: 24 }}>
        <FlowButton label="Upload protocol PDF" variant="secondary" onPress={() => { setPicked(null); setShowUpload(true); }} />
      </View>}
    </FlowScreen>}

    {step === "saved" && <FlowScreen title="Add trial" eyebrow="Saved"
      heading={timeline ? `${trialName} is ready for patients` : `${trialName} is saved`}
      sub={timeline ? `${timeline.title}. Each patient gets their own dates when they are added.`
        : "It has no visit schedule yet."}
      footer={<>
        {(user?.role === "pi" || user?.role === "crc") && !!timeline
          ? <FlowButton label="Add first patient" onPress={() => createdTrialId && router.replace({
              pathname: "/(app)/clinical/add-patient", params: { trialId: createdTrialId } })} />
          : <FlowButton label="Open trial" onPress={openTrial} />}
        {!!timeline && <FlowButton label="View visit schedule" variant="secondary" onPress={() => createdTrialId && router.replace({
          pathname: "/(app)/sponsor/kit-schedule", params: { id: createdTrialId, trialName } })} />}
      </>}>
      <View style={s.savedCard}>
        <View style={s.savedRow}><Text style={s.detailLabel}>Protocol</Text><Text style={s.savedValue}>{[protocolId.trim(), details.phase].filter(Boolean).join(" · ")}</Text></View>
        {!!timeline && <View style={s.savedRow}><Text style={s.detailLabel}>Schedule starts at</Text><Text style={s.savedValue}>{timeline.startLabel}</Text></View>}
        {!!model && <View style={[s.savedRow, { borderBottomWidth: 0 }]}>
          <Text style={s.detailLabel}>Asked per patient</Text>
          <Text style={s.savedValue}>{(() => {
            const labels = model.questions.filter((q) => q.scope === "participant").map((q) => q.label);
            return labels.length ? labels.slice(0, 2).join(", ") + (labels.length > 2 ? `, ${labels.length - 2} more` : "") : "Start date only";
          })()}</Text>
        </View>}
      </View>
    </FlowScreen>}

    {/* Before anything is entered: the person confirms they may create a trial. */}
    <Modal visible={showCreateConfirmation} transparent animationType="fade" onRequestClose={() => router.back()}>
      <View style={s.backdrop}>
        <View style={s.dialog}>
          <View style={s.dialogIcon}><ShieldCheck size={24} color={G.accent} /></View>
          <Text style={s.dialogTitle}>Before you add a trial</Text>
          <ScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: 12, paddingTop: 12 }}>
            {CREATE_TRIAL_RESPONSIBILITIES.map((item, index) => <View key={item} style={s.point}>
              <Text style={s.pointNumber}>{index + 1}</Text>
              <Text style={[f.hint, { flex: 1, color: G.ink }]}>{item}</Text>
            </View>)}
          </ScrollView>
          <View style={s.dialogActions}>
            <View style={{ flex: 1 }}><FlowButton testID="cancel-create-trial" label="Cancel" variant="secondary" onPress={() => router.back()} /></View>
            <View style={{ flex: 1.6 }}><FlowButton testID="confirm-create-trial" label="I confirm" onPress={() => setShowCreateConfirmation(false)} /></View>
          </View>
        </View>
      </View>
    </Modal>

    <Modal visible={showUpload} transparent animationType="fade" onRequestClose={() => setShowUpload(false)}>
      <View style={s.backdrop}>
        <View style={s.dialog}>
          <View style={s.dialogTop}>
            <View style={s.dialogIcon}><FileText size={24} color={G.accent} /></View>
            <Pressable testID="close-protocol-upload" accessibilityRole="button" accessibilityLabel="Close" hitSlop={12} onPress={() => setShowUpload(false)}>
              <X size={20} color={G.muted} />
            </Pressable>
          </View>
          <Text style={s.dialogTitle}>{protocolId.trim() ? `${protocolId.trim()} is not on record` : "Upload the protocol"}</Text>
          <Text style={[f.hint, { fontSize: 15, lineHeight: 22, marginTop: 8 }]}>Upload the protocol PDF. We read it and build the visit schedule for you to check.</Text>
          {!!err && <Text style={[f.error, { marginTop: 12 }]}>{err}</Text>}
          <Pressable testID="upload-protocol" accessibilityRole="button" onPress={pick} style={s.dropZone}>
            <UploadCloud size={24} color={G.accent} />
            <Text style={s.dropTitle}>{picked ? "Choose a different file" : "Choose protocol PDF"}</Text>
            <Text style={f.hint}>PDF or text · up to 25 MB</Text>
          </Pressable>
          {!!picked && <View style={s.fileChip}>
            <Check size={18} color={G.accent} strokeWidth={2.5} />
            <Text style={s.fileName} numberOfLines={1}>{picked.name}</Text>
          </View>}
          <View style={{ marginTop: 20 }}>
            <FlowButton testID="analyse-protocol" label="Analyse protocol" disabled={!picked} onPress={analyse} />
          </View>
        </View>
      </View>
    </Modal>

    <FlowSheet visible={showEdit} title="Trial details" onClose={() => setShowEdit(false)}>
      <View style={{ gap: 6 }}>
        <Text style={f.label}>Title</Text>
        <TextInput testID="trial-title" value={details.title} onChangeText={(value) => update({ title: value })} multiline
          placeholder="Official study title" placeholderTextColor={G.muted} style={[f.input, { minHeight: 76, textAlignVertical: "top" }]} />
      </View>
      <View style={{ gap: 8 }}>
        <Text style={f.label}>Phase</Text>
        <View style={f.chips}>
          {PHASES.map((phase) => {
            const on = details.phase === phase;
            return <Pressable key={phase} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => update({ phase })} style={[f.chip, on && f.chipOn]}>
              <Text style={[f.chipText, on && f.chipTextOn]}>{phase}</Text>
            </Pressable>;
          })}
        </View>
      </View>
      <SheetField label="Indication" testID="trial-condition" value={details.indications.join(", ")} placeholder="e.g. Type 1 diabetes"
        onChange={(value) => update({ indications: value.split(",").map((item) => item.trimStart()) })} />
      <SheetField label="Study drug" testID="trial-drug" value={details.drug} placeholder="Optional" onChange={(value) => update({ drug: value })} />
      <SheetField label="Duration" testID="trial-duration" value={details.duration} placeholder="e.g. 18 months (optional)" onChange={(value) => update({ duration: value })} />
      <View style={{ flexDirection: "row", gap: 12 }}>
        <View style={{ flex: 1 }}><SheetField label="Sample size" testID="trial-sample-size" value={details.target_enrollment} placeholder="Optional"
          numeric onChange={(value) => update({ target_enrollment: value.replace(/\D/g, "") })} /></View>
        <View style={{ flex: 1 }}><SheetField label="CTRI number" testID="trial-ctri" value={details.ctri_number} placeholder="Optional"
          onChange={(value) => update({ ctri_number: value })} /></View>
      </View>
      <View style={{ gap: 8 }}>
        <Text style={f.label}>Status</Text>
        <View style={f.chips}>
          {STATUSES.map((status) => {
            const on = details.status === status.value;
            return <Pressable key={status.value} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => update({ status: status.value })} style={[f.chip, on && f.chipOn]}>
              <Text style={[f.chipText, on && f.chipTextOn]}>{status.label}</Text>
            </Pressable>;
          })}
        </View>
      </View>
      <FlowButton label="Done" onPress={() => setShowEdit(false)} />
    </FlowSheet>

    <FlowSheet visible={!!renaming} title="Rename section" onClose={() => setRenaming(null)}>
      <Text style={f.hint}>Give it the same name as the section before or after it to join them.</Text>
      <TextInput value={renameText} onChangeText={setRenameText} autoFocus style={f.input} placeholder="Section name" placeholderTextColor={G.muted} />
      <FlowButton label="Save name" disabled={!renameText.trim()} onPress={() => {
        if (!renaming) return;
        const label = renameText.trim();
        setRenames((current) => ({ ...current, ...Object.fromEntries(renaming.visitIds.map((id) => [id, label])) }));
        setRenaming(null);
      }} />
    </FlowSheet>

    <FlowSheet visible={showNotes} title="Notes from the protocol" onClose={() => setShowNotes(false)}>
      {kit?.warnings.map((w) => <Text key={w} style={[f.hint, { color: G.amberText }]}>{w}</Text>)}
      {model?.notes.map((note) => <Text key={note} style={[f.hint, { color: G.ink, fontSize: 14, lineHeight: 20 }]}>• {note}</Text>)}
    </FlowSheet>

    {kit && <KitQuestionsModal
      visible={showKitQuestions}
      baseModel={kit.baseModel}
      model={kitConfirmed?.model ?? kit.model}
      reviewAnswers={kitConfirmed?.reviewAnswers ?? kit.reviewAnswers}
      siteAnswers={kitConfirmed?.siteAnswers ?? {}}
      onConfirm={(confirmed) => { setKitConfirmed(confirmed); setShowKitQuestions(false); }}
      onDismiss={kitConfirmed ? () => setShowKitQuestions(false) : undefined}
    />}
  </>;
}

function SheetField({ label, value, onChange, placeholder, testID, numeric }: {
  label: string; value: string; onChange: (value: string) => void; placeholder: string; testID: string; numeric?: boolean;
}) {
  return <View style={{ gap: 6 }}>
    <Text style={f.label}>{label}</Text>
    <TextInput testID={testID} value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={G.muted}
      keyboardType={numeric ? "number-pad" : "default"} style={f.input} />
  </View>;
}

const s = StyleSheet.create({
  lookupRow: { flexDirection: "row", gap: 10, marginTop: 8 },
  lookupInput: { flex: 1, minHeight: 54, fontSize: 18, fontFamily: fonts.medium, letterSpacing: 1 },
  lookupButton: { width: 54, height: 54, borderRadius: 14, backgroundColor: G.accent, alignItems: "center", justifyContent: "center" },
  analysingFooter: { flexDirection: "row", alignItems: "center", gap: 12 },
  stage: { flexDirection: "row", gap: 14 },
  stageRail: { width: 24, alignItems: "center" },
  stageDot: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  stageDone: { backgroundColor: G.accent },
  stageWait: { borderWidth: 2, borderColor: G.line },
  stageLine: { flex: 1, width: 2, backgroundColor: G.track, marginVertical: 4 },
  stageText: { flex: 1, paddingTop: 1, paddingBottom: 20, gap: 2 },
  stageLabel: { color: G.ink, fontFamily: fonts.semibold, fontSize: 16 },
  detailRow: { flexDirection: "row", gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: G.softLine },
  detailLabel: { width: 104, color: G.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 20 },
  detailValue: { flex: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 15, lineHeight: 21 },
  editButton: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 18, minHeight: 48, borderWidth: 1, borderColor: G.line, borderRadius: 14 },
  editText: { color: G.accent, fontFamily: fonts.semibold, fontSize: 15 },
  checks: { flexDirection: "row", gap: 8, marginTop: 18 },
  check: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12 },
  checkOk: { backgroundColor: G.selected },
  checkWarn: { backgroundColor: G.amberBg },
  checkLink: { borderWidth: 1, borderColor: G.line, justifyContent: "space-between" },
  checkText: { flexShrink: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 13 },
  linkText: { color: G.accent, fontFamily: fonts.semibold, fontSize: 14 },
  notesRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 14 },
  notesText: { color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
  savedCard: { marginTop: 28, borderWidth: 1, borderColor: G.line, borderRadius: 14, paddingHorizontal: 16 },
  savedRow: { flexDirection: "row", gap: 12, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: G.softLine },
  savedValue: { flex: 1, color: G.ink, fontFamily: fonts.semibold, fontSize: 14, textAlign: "right" },
  backdrop: { flex: 1, backgroundColor: "rgba(22,53,42,0.45)", justifyContent: "center", padding: 20 },
  dialog: { width: "100%", maxWidth: 440, maxHeight: "92%", alignSelf: "center", backgroundColor: "#FFFFFF", borderRadius: 22, padding: 24 },
  dialogTop: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between" },
  dialogIcon: { width: 48, height: 48, borderRadius: 14, backgroundColor: G.tag, alignItems: "center", justifyContent: "center" },
  dialogTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 22, lineHeight: 28, marginTop: 16 },
  dialogActions: { flexDirection: "row", gap: 10, marginTop: 20 },
  point: { flexDirection: "row", gap: 10 },
  pointNumber: { width: 22, height: 22, borderRadius: 11, backgroundColor: G.tag, color: G.accent, textAlign: "center", lineHeight: 22, fontFamily: fonts.bold, fontSize: 11, overflow: "hidden" },
  dropZone: { marginTop: 20, alignItems: "center", gap: 6, paddingVertical: 22, paddingHorizontal: 16, borderWidth: 1.5, borderStyle: "dashed", borderColor: "#9DB8A6", borderRadius: 14, backgroundColor: "#F7FAF5" },
  dropTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 15 },
  fileChip: { marginTop: 12, flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderRadius: 12, backgroundColor: G.selected },
  fileName: { flex: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
});
