import { api } from "@/src/api/client";
import type { ProtocolIdentity, ReviewAction } from "./protocolIdentity";

export type ResumeOutcome = {
  status: "COMPLETED" | "ALREADY_HANDLED" | "PROVIDER_UNAVAILABLE" | "FAILED";
  retryable?: boolean;
  message?: string;
  reused?: boolean;
  schedule_count?: number;
};

export async function listProtocolIdentities(reviewStatus: string = "PENDING_REVIEW") {
  const response = await api.get<{ items: ProtocolIdentity[]; can_review: boolean }>(
    "/protocol-identities", { params: { review_status: reviewStatus } });
  return response.data;
}

export async function getProtocolIdentity(id: string) {
  const response = await api.get<ProtocolIdentity>(`/protocol-identities/${id}`);
  return response.data;
}

export async function resolveProtocolIdentity(
  id: string,
  body: { action: ReviewAction; reason: string; expected_revision: number; corrected_identifier: string | null },
) {
  const response = await api.post<{
    identity: ProtocolIdentity; replayed: boolean; resume: ResumeOutcome | null;
  }>(`/protocol-identities/${id}/resolve`, body);
  return response.data;
}

export async function resumeProtocolIdentity(id: string) {
  const response = await api.post<{ identity: ProtocolIdentity; resume: ResumeOutcome }>(
    `/protocol-identities/${id}/resume`);
  return response.data;
}

export async function getIdentityExtraction(id: string) {
  const response = await api.get<{
    details: Record<string, unknown>;
    extractions: { extraction_id: string; option_label?: string; schedule_visit_count: number }[];
    identity: ProtocolIdentity;
  }>(`/protocol-identities/${id}/extraction`);
  return response.data;
}
