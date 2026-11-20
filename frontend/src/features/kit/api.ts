import { Platform } from "react-native";
import { api } from "@/src/api/client";
import type { KitAnalysis, KitBlocker, KitModel, KitParticipant } from "./index";

export type KitStage = { id: string; label: string; status: "wait" | "run" | "done"; detail: string };
export type KitJob = {
  job_id: string; status: "running" | "done" | "failed"; stages: KitStage[]; model_name?: string | null;
  result?: KitAnalysis; error?: { code: string; message: string }; http_status?: number;
};

export class KitAnalysisError extends Error {
  constructor(message: string, readonly code: string) { super(message); }
}

const ERROR_TEXT: Record<string, string> = {
  no_credits: "The model account has no credits left, so the protocol could not be analysed.",
  rate_limited: "The model is busy. Wait a minute and try again.",
  model_busy: "The AI model is very busy right now. It was retried several times. Try again in a few minutes.",
  prompt_too_large: "This protocol is too large to analyse in one request.",
  no_api_key: "Protocol analysis is not configured on the server yet.",
  auth: "Protocol analysis is not configured on the server yet.",
  bad_pdf: "This file could not be read as a PDF.",
  no_offline_model: "The server is in offline mode and has no prepared analysis for this file.",
};

/** Upload a protocol and return the background job's id. */
export async function startKitAnalysis(asset: any, expectedProtocolId: string): Promise<{ job_id: string; stages: KitStage[] }> {
  const form = new FormData();
  if (expectedProtocolId) form.append("expected_protocol_id", expectedProtocolId);
  if (Platform.OS === "web") {
    const file = asset.file || await (await fetch(asset.uri)).blob();
    form.append("file", file, asset.name || "protocol.pdf");
  } else {
    form.append("file", { uri: asset.uri, name: asset.name || "protocol.pdf", type: asset.mimeType || "application/pdf" } as any);
  }
  return (await api.post("/kit/analyze", form, { timeout: 120000 })).data;
}

export async function getKitJob(jobId: string): Promise<KitJob> {
  return (await api.get(`/kit/jobs/${jobId}`)).data;
}

/**
 * Poll a job until it finishes, reporting every stage change. Rejects with a
 * KitAnalysisError whose message is safe to show.
 */
export async function waitForKitJob(
  jobId: string, onUpdate: (job: KitJob) => void, intervalMs = 1500,
): Promise<KitAnalysis> {
  for (;;) {
    const job = await getKitJob(jobId);
    onUpdate(job);
    if (job.status === "done" && job.result) return job.result;
    if (job.status === "failed") {
      const code = job.error?.code || "internal";
      throw new KitAnalysisError(ERROR_TEXT[code] || job.error?.message || "Protocol analysis failed.", code);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export async function patchKitModel(baseModel: KitModel, reviewAnswers: Record<string, string>): Promise<KitModel> {
  return (await api.post("/kit/models/patch", { baseModel, reviewAnswers })).data.model;
}

export async function saveKitModel(
  trialId: string,
  body: KitWorkflowInput & { workflowAction?: "draft" | "approve"; workflowDigest?: string },
) {
  return (await api.put(`/trials/${trialId}/kit-model`, body)).data;
}

export type KitWorkflowInput = {
  baseModel: KitModel; reviewAnswers: Record<string, string>; siteAnswers: Record<string, string>; fileName?: string;
  fileSha256?: string | null; warnings?: string[]; corrections?: string[] | null; analysisJobId?: string;
  expectedVersion?: number; blockEdits?: Record<string, Record<string, unknown>>; resolutions?: Record<string, string>;
};
type WorkflowEdge = { block: string; relation: string; basis?: string };
export type WorkflowBlock = {
  id: string; kind: string; label: string; scope: string; trigger: string;
  inputs: WorkflowEdge[]; paths: WorkflowEdge[];
  prompts: { type: string; text: string; roles: string[]; options: { value: string; label: string }[] }[];
  outputs?: { kind: string; key?: string; label?: string; source?: string }[];
  /** For history-based intervals and study events: condition, inputs, timing, branches and outputs in words. */
  summary?: string[];
  source: { reference?: unknown; page?: number | null; quote?: string | null };
  operation: Record<string, unknown>;
};
export type KitWorkflow = {
  version: number; blocks: WorkflowBlock[]; errors: string[];
  issues: { id: string; kind: string; text: string; why: string; quote: string; page: number | null;
    blocks: string[]; resolution?: { text: string; by: string } | null }[];
};
export type WorkflowPreview = { model: KitModel; workflow: KitWorkflow; digest: string; problems: string[] };

export async function previewKitWorkflow(body: KitWorkflowInput): Promise<WorkflowPreview> {
  return (await api.post("/kit/workflow-preview", body)).data;
}

export type KitWorkflowDraft = SavedKitModel & {
  expected_version: number; block_edits: Record<string, Record<string, unknown>>;
  resolutions: Record<string, string>; digest: string; problems: string[];
};

export async function getKitDraft(trialId: string): Promise<KitWorkflowDraft> {
  return (await api.get(`/trials/${trialId}/kit-draft`)).data;
}

export type SavedKitModel = {
  trial_id: string; base_model: KitModel; model: KitModel; review_answers: Record<string, string>;
  site_answers: Record<string, string>; file_name?: string | null; saved_at: string;
  version?: number; file_stored?: boolean;
  file_sha256?: string | null; workflow?: KitWorkflow; analysis_warnings?: string[]; corrections?: string[] | null;
  study_dates?: Record<string, string>; study_revision?: number;
  /** Pattern rules nothing calculates yet: while any is listed, no patient can be enrolled. */
  blockers?: KitBlocker[];
};

/** The trial's current reviewed model, or an earlier `version` patients may still be on. */
export async function getKitModel(trialId: string, version?: number | null): Promise<SavedKitModel> {
  return (await api.get(`/trials/${trialId}/kit-model`, { params: version != null ? { version } : undefined })).data;
}

export async function setKitStudyDate(trialId: string, version: number, anchorId: string, date: string | null, revision: number) {
  return (await api.put(`/trials/${trialId}/kit-model/${version}/study-dates/${encodeURIComponent(anchorId)}`, { date, revision })).data;
}

/** One patient on the kit schedule: their record plus everything needed to compute their dates. */
export type KitPatientRecord = {
  patient: { id: string; subject_id?: string; full_name?: string; avatar_initials?: string; trial_id: string };
  trial: { id?: string; title?: string; protocol_id?: string };
  model: KitModel; site_answers: Record<string, string>; participant: KitParticipant; updated_at?: string | null;
  /** The reviewed model version this patient is scheduled with. */
  model_version?: number;
  workflow_version?: number | null;
  revision?: number;
  actual_versions?: Record<string, number>;
  /** Staff only: a newer reviewed model the patient could move to, and why not if they can't. */
  latest?: { version: number; model: KitModel; site_answers: Record<string, string>; problem: string | null } | null;
};

export async function getPatientKit(patientId: string): Promise<KitPatientRecord> {
  return (await api.get(`/patients/${patientId}/kit`)).data;
}

export async function savePatientKit(patientId: string, participant: KitParticipant, revision?: number): Promise<KitPatientRecord> {
  return (await api.put(`/patients/${patientId}/kit`, participant, { params: { revision } })).data;
}

/** Move a patient to the trial's current reviewed model, keeping their recorded dates and answers. */
export async function movePatientToLatest(patientId: string, version?: number, revision?: number): Promise<KitPatientRecord> {
  return (await api.post(`/patients/${patientId}/kit/move-to-latest`, null, { params: { version, revision } })).data;
}

const toBase64 = (buffer: ArrayBuffer) => {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return (globalThis as any).btoa(binary);
};

/** Save or share the protocol file a trial's model (current or `version`) was read from. */
export async function downloadProtocolFile(trialId: string, fileName: string, version?: number | null): Promise<void> {
  const params = version != null ? { version } : undefined;
  const name = fileName || "protocol.pdf";
  const mime = name.toLowerCase().endsWith(".txt") ? "text/plain" : "application/pdf";
  if (Platform.OS === "web") {
    const res = await api.get(`/trials/${trialId}/kit-model/file`, { params, responseType: "blob" });
    const g: any = globalThis;
    const url = g.URL.createObjectURL(res.data instanceof g.Blob ? res.data : new g.Blob([res.data], { type: mime }));
    const a = g.document.createElement("a");
    a.href = url; a.download = name; a.click();
    g.URL.revokeObjectURL(url);
    return;
  }
  const res = await api.get(`/trials/${trialId}/kit-model/file`, { params, responseType: "arraybuffer" });
  const FileSystem = await import("expo-file-system/legacy");
  const Sharing = await import("expo-sharing");
  const uri = `${FileSystem.cacheDirectory}${name.replace(/[^\w.-]+/g, "_")}`;
  await FileSystem.writeAsStringAsync(uri, toBase64(res.data as ArrayBuffer), { encoding: FileSystem.EncodingType.Base64 });
  if (!await Sharing.isAvailableAsync()) throw new Error("Sharing is not available on this device.");
  await Sharing.shareAsync(uri, { mimeType: mime, dialogTitle: `Share ${name}` });
}

export async function getMyKit(): Promise<KitPatientRecord> {
  return (await api.get("/me/kit")).data;
}

// ── Blocked rules across trials (admin) ─────────────────────────────────────
export type KitBlockerGroup = {
  pattern: string; has_handler: boolean; trials: number; rules: number;
  evidence: {
    trial_id: string; trial_title: string | null; protocol_id: string | null; version: number | null; label: string;
    quote: string; page: number | null; quote_found: boolean | null; reason: "no_handler" | "params"; problem: string | null;
  }[];
};
export type KitBlockerOverview = { handlers: { pattern: string; title: string }[]; blocked_trials: number; groups: KitBlockerGroup[] };

export async function fetchKitBlockers(): Promise<KitBlockerOverview> {
  return (await api.get("/admin/kit/blockers")).data;
}

/** Re-evaluate saved models against the handlers the server now has (all, or one pattern). */
export async function recheckKitBlockers(pattern?: string): Promise<{ checked: number; unblocked: string[]; still_blocked: number; handlers: string[] }> {
  return (await api.post("/admin/kit/blockers/recheck", pattern ? { pattern } : {})).data;
}

// ── Inputs confirmed at a visit, and study-wide events ──────────────────────────────

/** Record a finding or recommendation with its own id and confirmation time: a replay changes nothing
 *  and an older confirmation never replaces a newer one. */
export async function recordKitObservation(patientId: string, body: {
  id: string; key: string; input: string; value: string | number; confirmed_at: string; source?: string;
}): Promise<{ status: "applied" | "replayed" | "ignored_older"; revision: number }> {
  return (await api.post(`/patients/${patientId}/kit/observations`, body)).data;
}

export type KitStudyEffectView = {
  id: string; event_id: string; subject_id: string; site: string; version: number; label: string;
  state: "applied" | "excluded" | "unresolved" | "to_review" | "review" | "unmatched" | "already_completed" | "ended" | "withdrawn";
  action: string | null; reason: string | null; effective: string; task_key: string | null; booked: string | null;
  changes: { visit?: string; key?: string; change: string }[];
};
export type KitStudyEventView = {
  id: string; trial_id: string; type: string; label: string; effective_date: string; fields: Record<string, string>;
  confirmed_at: string; confirmed_by: string; revision: number; status: "processing" | "partial" | "applied";
  counts?: Record<string, number>; failed?: { subject_id: string; error: string }[]; effects: KitStudyEffectView[];
};
export type KitCorrectionPreview = Record<"added" | "removed" | "kept" | "booked_affected" | "completed_kept",
  { effect_id: string; subject_id: string; from: string | null; to: string; reason: string | null; booked: string | null }[]>;

export async function listStudyEvents(trialId: string): Promise<{ definitions: NonNullable<KitModel["study_events"]>; events: KitStudyEventView[] }> {
  return (await api.get(`/trials/${trialId}/kit-study-events`)).data;
}

/** `id` is generated once per confirmation, so retrying the request never applies the event twice. */
export async function confirmStudyEvent(trialId: string, body: { id: string; type: string; effective_date: string; fields: Record<string, string> }): Promise<KitStudyEventView> {
  return (await api.post(`/trials/${trialId}/kit-study-events`, body)).data;
}

export async function retryStudyEvent(trialId: string, eventId: string): Promise<KitStudyEventView> {
  return (await api.post(`/trials/${trialId}/kit-study-events/${eventId}/process`)).data;
}

export async function correctStudyEvent(trialId: string, eventId: string, body: {
  effective_date: string; fields: Record<string, string>; revision: number; preview?: boolean; acknowledge_booked?: string[];
}): Promise<{ preview: KitCorrectionPreview } & Partial<KitStudyEventView>> {
  return (await api.put(`/trials/${trialId}/kit-study-events/${eventId}`, body)).data;
}

/** Site staff book (or clear) the next available date for a visit a study event added. */
export async function bookStudyEffect(patientId: string, effectId: string, date: string | null) {
  return (await api.put(`/patients/${patientId}/kit/study-effects/${encodeURIComponent(effectId)}/booking`, { date })).data;
}
