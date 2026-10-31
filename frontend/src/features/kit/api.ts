import { Platform } from "react-native";
import { api } from "@/src/api/client";
import type { KitAnalysis, KitModel, KitParticipant } from "./index";

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
  body: {
    baseModel: KitModel; reviewAnswers: Record<string, string>; siteAnswers: Record<string, string>; fileName?: string;
    fileSha256?: string | null; warnings?: string[]; corrections?: string[] | null; analysisJobId?: string;
  },
) {
  return (await api.put(`/trials/${trialId}/kit-model`, body)).data;
}

export type SavedKitModel = {
  trial_id: string; base_model: KitModel; model: KitModel; review_answers: Record<string, string>;
  site_answers: Record<string, string>; file_name?: string | null; saved_at: string;
  version?: number; file_stored?: boolean;
};

/** The trial's current reviewed model, or an earlier `version` patients may still be on. */
export async function getKitModel(trialId: string, version?: number | null): Promise<SavedKitModel> {
  return (await api.get(`/trials/${trialId}/kit-model`, { params: version != null ? { version } : undefined })).data;
}

/** One patient on the kit schedule: their record plus everything needed to compute their dates. */
export type KitPatientRecord = {
  patient: { id: string; subject_id?: string; full_name?: string; avatar_initials?: string; trial_id: string };
  trial: { id?: string; title?: string; protocol_id?: string };
  model: KitModel; site_answers: Record<string, string>; participant: KitParticipant; updated_at?: string | null;
  /** The reviewed model version this patient is scheduled with. */
  model_version?: number;
  /** Staff only: a newer reviewed model the patient could move to, and why not if they can't. */
  latest?: { version: number; model: KitModel; site_answers: Record<string, string>; problem: string | null } | null;
};

export async function getPatientKit(patientId: string): Promise<KitPatientRecord> {
  return (await api.get(`/patients/${patientId}/kit`)).data;
}

export async function savePatientKit(patientId: string, participant: KitParticipant): Promise<KitPatientRecord> {
  return (await api.put(`/patients/${patientId}/kit`, participant)).data;
}

/** Move a patient to the trial's current reviewed model, keeping their recorded dates and answers. */
export async function movePatientToLatest(patientId: string): Promise<KitPatientRecord> {
  return (await api.post(`/patients/${patientId}/kit/move-to-latest`)).data;
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
