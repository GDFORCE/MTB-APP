// Remote Audit — document API client (Phase 4).
//
// Mirrors the backend's corrected upload sequencing exactly: create (claims
// only) → send bytes → submit. There is deliberately no call that returns a
// verdict synchronously, because producing one would mean parsing untrusted
// bytes in the API process — which this system never does.
import { api } from "@/src/api/client";

import {
  normalizeCheckRun,
  normalizeDocument,
  normalizeUploadTarget,
} from "./documentNormalize";
import type { DocumentCheckRun, DocumentItem, DocumentUploadTarget } from "./types";

const uuid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const idempotent = (key?: string) => ({ headers: { "Idempotency-Key": key ?? uuid() } });

export async function createDocument(auditId: string, input: {
  documentType: string;
  fileName: string;
  mimeClaimed: string;
  sizeClaimed: number;
  linkedEntityType?: string;
  linkedEntityId?: string;
  supersedesDocumentId?: string;
}): Promise<DocumentUploadTarget> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/documents`,
    {
      document_type: input.documentType,
      file_name: input.fileName,
      mime_claimed: input.mimeClaimed,
      size_claimed: input.sizeClaimed,
      linked_entity_type: input.linkedEntityType ?? null,
      linked_entity_id: input.linkedEntityId ?? null,
      supersedes_document_id: input.supersedesDocumentId ?? null,
    },
    idempotent(),
  );
  return normalizeUploadTarget(data);
}

export async function uploadDocumentBytes(documentId: string, file: {
  uri: string;
  name: string;
  type: string;
}): Promise<void> {
  const form = new FormData();
  form.append("file", file as unknown as Blob);
  await api.post(`/remote-audit/documents/${documentId}/upload-bytes`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/** Presigned-POST path (S3). A bare fetch, not `api`: the object store is a
 *  different origin and must never receive this app's Authorization header. */
export async function uploadDocumentToPresignedPost(
  upload: { url: string; fields: Record<string, string> },
  file: { uri: string; name: string; type: string },
): Promise<void> {
  const form = new FormData();
  for (const [k, v] of Object.entries(upload.fields || {})) form.append(k, v);
  form.append("file", file as unknown as Blob);
  const res = await fetch(upload.url, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Upload rejected by storage (${res.status})`);
}

export async function sendDocumentBytes(
  target: DocumentUploadTarget,
  file: { uri: string; name: string; type: string },
): Promise<void> {
  if (target.upload?.url) {
    await uploadDocumentToPresignedPost(target.upload, file);
    return;
  }
  await uploadDocumentBytes(target.document.id, file);
}

/** Hands the document to the worker. Returns while it is still QUARANTINED —
 *  the verdict arrives later, and the screen polls for it. */
export async function submitDocument(documentId: string): Promise<void> {
  await api.post(`/remote-audit/documents/${documentId}/submit`, {}, idempotent());
}

export async function fetchDocuments(auditId: string): Promise<DocumentItem[]> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/documents`);
  return Array.isArray(data) ? data.map(normalizeDocument) : [];
}

export async function fetchDocument(documentId: string): Promise<DocumentItem> {
  const { data } = await api.get(`/remote-audit/documents/${documentId}`);
  return normalizeDocument(data);
}

export async function fetchDocumentChecks(documentId: string): Promise<{
  latest: DocumentCheckRun | null;
  history: DocumentCheckRun[];
}> {
  const { data } = await api.get(`/remote-audit/documents/${documentId}/checks`);
  return {
    latest: normalizeCheckRun(data?.latest),
    history: Array.isArray(data?.history)
      ? (data.history.map(normalizeCheckRun).filter(Boolean) as DocumentCheckRun[])
      : [],
  };
}

export async function confirmDocumentFields(
  documentId: string,
  fields: Record<string, unknown>,
): Promise<DocumentItem> {
  const { data } = await api.post(`/remote-audit/documents/${documentId}/confirm`, { fields });
  return normalizeDocument(data);
}

export async function decideDocument(documentId: string, input: {
  decision: "ACCEPTED" | "REJECTED";
  reasonCode?: string;
  note?: string;
}): Promise<DocumentItem> {
  const { data } = await api.post(
    `/remote-audit/documents/${documentId}/decide`,
    { decision: input.decision, reason_code: input.reasonCode ?? null, note: input.note ?? null },
    idempotent(),
  );
  return normalizeDocument(data);
}
