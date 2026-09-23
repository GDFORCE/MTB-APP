// Remote Audit — query/CAPA API client (Phase 5).
//
// Every mutation sends both an Idempotency-Key (so a retry on a flaky
// connection can't raise the same query twice) and the `expected_version` the
// screen last loaded (so two reviewers acting from two devices can't silently
// overwrite each other — the loser is told).
import { api } from "@/src/api/client";

import { normalizeCapa, normalizeQuery, normalizeResponse } from "./queries";
import type { AuditQuery, Capa, Criticality, QueryResponse } from "./queries";

const uuid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const idempotent = (key?: string) => ({ headers: { "Idempotency-Key": key ?? uuid() } });

// ── Queries ─────────────────────────────────────────────────────────────────

export async function createQuery(auditId: string, input: {
  title: string;
  detail?: string;
  criticality?: Criticality;
  linkedEntityType?: string;
  linkedEntityId?: string;
  dueDate?: string;
}): Promise<AuditQuery> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/queries`,
    {
      title: input.title,
      detail: input.detail ?? "",
      criticality: input.criticality ?? "minor",
      linked_entity_type: input.linkedEntityType ?? null,
      linked_entity_id: input.linkedEntityId ?? null,
      due_date: input.dueDate ?? null,
    },
    idempotent(),
  );
  return normalizeQuery(data);
}

export async function fetchQueries(auditId: string, status?: string): Promise<AuditQuery[]> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/queries`, {
    params: status ? { status } : undefined,
  });
  return Array.isArray(data) ? data.map(normalizeQuery) : [];
}

export async function fetchQuery(queryId: string): Promise<AuditQuery> {
  const { data } = await api.get(`/remote-audit/queries/${queryId}`);
  return normalizeQuery(data);
}

export async function respondToQuery(queryId: string, input: {
  body: string;
  attachmentIds?: string[];
  expectedVersion?: number;
}): Promise<QueryResponse> {
  const { data } = await api.post(
    `/remote-audit/queries/${queryId}/responses`,
    {
      body: input.body,
      attachment_ids: input.attachmentIds ?? [],
      expected_version: input.expectedVersion ?? null,
    },
    idempotent(),
  );
  return normalizeResponse(data);
}

export async function decideQuery(queryId: string, input: {
  action: "CLOSE" | "REJECT" | "REOPEN" | "CANCEL";
  reasonCode?: string;
  reason?: string;
  expectedVersion?: number;
}): Promise<AuditQuery> {
  const { data } = await api.post(
    `/remote-audit/queries/${queryId}/decision`,
    {
      action: input.action,
      reason_code: input.reasonCode ?? null,
      reason: input.reason ?? null,
      expected_version: input.expectedVersion ?? null,
    },
    idempotent(),
  );
  return normalizeQuery(data);
}

// ── CAPAs ───────────────────────────────────────────────────────────────────

export async function createCapa(auditId: string, input: {
  title: string;
  description?: string;
  ownerUserId: string;
  dueDate: string;
  criticality?: Criticality;
  queryId?: string;
}): Promise<Capa> {
  const { data } = await api.post(
    `/remote-audit/audits/${auditId}/capas`,
    {
      title: input.title,
      description: input.description ?? "",
      owner_user_id: input.ownerUserId,
      due_date: input.dueDate,
      criticality: input.criticality ?? "minor",
      query_id: input.queryId ?? null,
    },
    idempotent(),
  );
  return normalizeCapa(data);
}

export async function fetchCapas(auditId: string, status?: string): Promise<Capa[]> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/capas`, {
    params: status ? { status } : undefined,
  });
  return Array.isArray(data) ? data.map(normalizeCapa) : [];
}

export async function fetchCapa(capaId: string): Promise<Capa> {
  const { data } = await api.get(`/remote-audit/capas/${capaId}`);
  return normalizeCapa(data);
}

export async function progressCapa(capaId: string, input: {
  action: "START" | "COMPLETE" | "CANCEL";
  note?: string;
  expectedVersion?: number;
}): Promise<Capa> {
  const { data } = await api.post(
    `/remote-audit/capas/${capaId}/progress`,
    { action: input.action, note: input.note ?? null, expected_version: input.expectedVersion ?? null },
    idempotent(),
  );
  return normalizeCapa(data);
}

export async function reviewCapaEffectiveness(capaId: string, input: {
  outcome: "EFFECTIVE" | "INEFFECTIVE";
  note: string;
  expectedVersion?: number;
}): Promise<Capa> {
  const { data } = await api.post(
    `/remote-audit/capas/${capaId}/effectiveness-review`,
    { outcome: input.outcome, note: input.note, expected_version: input.expectedVersion ?? null },
    idempotent(),
  );
  return normalizeCapa(data);
}

export async function fetchOverdueCapas(auditId: string): Promise<Capa[]> {
  const { data } = await api.get(`/remote-audit/audits/${auditId}/capas/overdue`);
  return Array.isArray(data) ? data.map(normalizeCapa) : [];
}
