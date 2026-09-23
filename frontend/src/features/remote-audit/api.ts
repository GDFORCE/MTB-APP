// Remote Audit API client — plain axios + hand-written normalizers, matching
// this codebase's existing convention (see src/features/sponsor/api.ts).
//
// The capture flow deliberately mirrors the backend's corrected sequencing
// (backend/app/services/remote_audit/evidence.py): create session -> declare
// the upload's CLAIMS -> send the bytes -> finalize. Nothing here decides
// whether evidence is valid; the server does that after the bytes arrive.
import { api } from "@/src/api/client";

import {
  normalizeAudit,
  normalizeEvidence,
  normalizeSession,
  normalizeUploadIntent,
  normalizeVerification,
} from "./normalize";
import type {
  AuditSummary,
  CaptureMode,
  CaptureSession,
  EvidenceItem,
  UploadIntent,
  VerificationResult,
} from "./types";

export {
  normalizeEvidence,
  normalizeSession,
  normalizeUploadIntent,
  normalizeVerification,
} from "./normalize";

const uuid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/** Every remote-audit mutation requires an Idempotency-Key (the backend
 *  rejects requests without one), so retries can never double-create. */
const idempotent = (key?: string) => ({ headers: { "Idempotency-Key": key ?? uuid() } });

// ── Capture flow (site side) ────────────────────────────────────────────────

export async function createCaptureSession(auditId: string, input: {
  linkedEntityType: string;
  linkedEntityId: string;
  requestedMediaType: string;
  captureMode: CaptureMode;
}): Promise<CaptureSession> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/capture-sessions`,
    {
      linked_entity_type: input.linkedEntityType,
      linked_entity_id: input.linkedEntityId,
      requested_media_type: input.requestedMediaType,
      capture_mode: input.captureMode,
    },
    idempotent(),
  );
  return normalizeSession(data);
}

export async function createUploadIntent(sessionId: string, input: {
  fileName: string;
  mimeClaimed: string;
  sizeClaimed: number;
}): Promise<UploadIntent> {
  const { data } = await api.post(`/remote-audit/capture-sessions/${sessionId}/upload-intent`, {
    file_name: input.fileName,
    mime_claimed: input.mimeClaimed,
    size_claimed: input.sizeClaimed,
  });
  return normalizeUploadIntent(data);
}

/** Server-mediated upload path (local/dev storage). With S3 configured the
 *  backend returns a presigned POST instead and the bytes never touch the API. */
export async function uploadBytes(sessionId: string, file: {
  uri: string;
  name: string;
  type: string;
}): Promise<void> {
  const form = new FormData();
  // React Native's FormData takes this {uri,name,type} shape for file parts.
  form.append("file", file as unknown as Blob);
  await api.post(`/remote-audit/capture-sessions/${sessionId}/upload-bytes`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/** Presigned-POST path (S3). Sent with a bare fetch, not the `api` instance:
 *  the object store is a different origin and must never receive this app's
 *  Authorization header. Field order matters — S3 requires every policy field
 *  to precede the file part. */
export async function uploadToPresignedPost(
  upload: { url: string; fields: Record<string, string> },
  file: { uri: string; name: string; type: string },
): Promise<void> {
  const form = new FormData();
  for (const [key, value] of Object.entries(upload.fields || {})) {
    form.append(key, value);
  }
  form.append("file", file as unknown as Blob);
  const res = await fetch(upload.url, { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(`Upload rejected by storage (${res.status})`);
  }
}

/** Routes the bytes down whichever path upload-intent selected. */
export async function sendBytes(
  sessionId: string,
  intent: UploadIntent,
  file: { uri: string; name: string; type: string },
): Promise<void> {
  if (intent.upload?.url) {
    await uploadToPresignedPost(intent.upload, file);
    return;
  }
  await uploadBytes(sessionId, file);
}

export async function finalizeCapture(sessionId: string, input: {
  objectKey: string;
  sha256Client: string;
  captureManifest: Record<string, unknown>;
}): Promise<EvidenceItem> {
  const { data } = await api.post(
    `/remote-audit/capture-sessions/${sessionId}/finalize`,
    {
      object_key: input.objectKey,
      sha256_client: input.sha256Client,
      capture_manifest: input.captureManifest,
    },
    idempotent(),
  );
  return normalizeEvidence(data);
}

// ── Review (sponsor side) ───────────────────────────────────────────────────

export async function fetchEvidence(evidenceId: string): Promise<EvidenceItem> {
  const { data } = await api.get(`/remote-audit/evidence/${evidenceId}`);
  return normalizeEvidence(data);
}

export async function fetchVerification(evidenceId: string): Promise<{
  latest: VerificationResult | null;
  history: VerificationResult[];
}> {
  const { data } = await api.get(`/remote-audit/evidence/${evidenceId}/verification`);
  return {
    latest: normalizeVerification(data?.latest),
    history: Array.isArray(data?.history)
      ? (data.history.map(normalizeVerification).filter(Boolean) as VerificationResult[])
      : [],
  };
}

export async function fetchAudits(): Promise<AuditSummary[]> {
  const { data } = await api.get(`/remote-audit/audits`);
  return Array.isArray(data) ? data.map(normalizeAudit) : [];
}
