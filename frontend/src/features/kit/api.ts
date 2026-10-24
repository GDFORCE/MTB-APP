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
  body: { baseModel: KitModel; reviewAnswers: Record<string, string>; siteAnswers: Record<string, string>; fileName?: string },
) {
  return (await api.put(`/trials/${trialId}/kit-model`, body)).data;
}

export type SavedKitModel = {
  trial_id: string; base_model: KitModel; model: KitModel; review_answers: Record<string, string>;
  site_answers: Record<string, string>; file_name?: string | null; saved_at: string;
};

export async function getKitModel(trialId: string): Promise<SavedKitModel> {
  return (await api.get(`/trials/${trialId}/kit-model`)).data;
}

/** One patient on the kit schedule: their record plus everything needed to compute their dates. */
export type KitPatientRecord = {
  patient: { id: string; subject_id?: string; full_name?: string; avatar_initials?: string; trial_id: string };
  trial: { id?: string; title?: string; protocol_id?: string };
  model: KitModel; site_answers: Record<string, string>; participant: KitParticipant; updated_at?: string | null;
};

export async function getPatientKit(patientId: string): Promise<KitPatientRecord> {
  return (await api.get(`/patients/${patientId}/kit`)).data;
}

export async function savePatientKit(patientId: string, participant: KitParticipant): Promise<KitPatientRecord> {
  return (await api.put(`/patients/${patientId}/kit`, participant)).data;
}

export async function getMyKit(): Promise<KitPatientRecord> {
  return (await api.get("/me/kit")).data;
}
