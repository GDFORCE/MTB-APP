// Audit detail — one screen serving both sides, like the query and CAPA
// screens. What each side can DO differs; what they see of the record does not,
// because two renderings of one audit would eventually disagree about it.
//
// Sections are ordered by what needs attention: open queries and overdue CAPAs
// first, documents after. The score lives on its own screen because a decision
// deserves the room, and because mixing "here are the open items" with "here is
// the verdict" invites deciding before reading.
import React, { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, View,
} from "react-native";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import {
  Camera, ClipboardCheck, Clock, FileText, MessageSquare, ShieldCheck, Upload, Video,
} from "lucide-react-native";

import { Body, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { useAuth } from "@/src/auth/AuthContext";
import { fetchCapas, fetchQueries } from "@/src/features/remote-audit/queriesApi";
import { computeOverdue } from "@/src/features/remote-audit/queries";
import type { AuditQuery, Capa } from "@/src/features/remote-audit/queries";
import { fetchDocuments } from "@/src/features/remote-audit/documentsApi";
import type { DocumentItem } from "@/src/features/remote-audit/types";
import { consentPermitsJoin, fetchSessions } from "@/src/features/remote-audit/scoringApi";
import type { VirtualSession } from "@/src/features/remote-audit/scoringApi";

const QUERY_TONE: Record<string, string> = {
  OPEN: colors.warning, RESPONDED: colors.info, REOPENED: colors.warning,
  CLOSED: colors.success, CANCELLED: colors.mutedFg,
};
const CAPA_TONE: Record<string, string> = {
  OPEN: colors.warning, IN_PROGRESS: colors.info, COMPLETED: colors.accent,
  CLOSED: colors.success, REOPENED: colors.destructive, CANCELLED: colors.mutedFg,
};
const DOC_TONE: Record<string, string> = {
  QUARANTINED: colors.mutedFg, PROCESSING: colors.mutedFg,
  PROCESSING_BLOCKED: colors.destructive, PROCESSING_FAILED: colors.destructive,
  TECHNICALLY_VALID: colors.success, REVIEW_REQUIRED: colors.warning,
  ACCEPTED: colors.success, REJECTED: colors.destructive,
  SUPERSEDED: colors.mutedFg, EXPIRED: colors.warning,
};

function Pill({ label, tone }: { label: string; tone: string }) {
  return (
    <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
      <Small color={tone}>{label.replace(/_/g, " ")}</Small>
    </View>
  );
}

export default function AuditDetailScreen() {
  const router = useRouter();
  const { auditId: rawAuditId } = useLocalSearchParams<{ auditId?: string }>();
  const auditId = String(rawAuditId ?? "");
  const { user } = useAuth();
  const isSiteSide = user?.role === "site" || user?.role === "pi" || user?.role === "crc";

  const [queries, setQueries] = useState<AuditQuery[]>([]);
  const [capas, setCapas] = useState<Capa[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [sessions, setSessions] = useState<VirtualSession[]>([]);
  const [livekitConfigured, setLivekitConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!auditId) return;
    setError(null);
    try {
      // Settled, not all: one failing section shouldn't blank the other two.
      const [q, c, d, v] = await Promise.allSettled([
        fetchQueries(auditId), fetchCapas(auditId), fetchDocuments(auditId),
        fetchSessions(auditId),
      ]);
      if (q.status === "fulfilled") setQueries(q.value);
      if (c.status === "fulfilled") setCapas(c.value);
      if (d.status === "fulfilled") setDocuments(d.value);
      if (v.status === "fulfilled") {
        setSessions(v.value.sessions);
        setLivekitConfigured(v.value.livekitConfigured);
      }
      if (q.status === "rejected" && c.status === "rejected" && d.status === "rejected") {
        setError("This audit isn't available to you.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [auditId]);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const openQueries = useMemo(
    () => queries.filter((q) => q.status !== "CLOSED" && q.status !== "CANCELLED"), [queries]);
  const overdueCapas = useMemo(
    () => capas.filter((c) => c.isOverdue || computeOverdue(c.status, c.dueDate)), [capas]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Audit" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Audit" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        {error ? <Card><Body>{error}</Body></Card> : null}

        {/* What needs attention, before anything else. */}
        {(openQueries.length > 0 || overdueCapas.length > 0) ? (
          <Card style={{ gap: spacing.xs, borderColor: colors.warning }}>
            <Body weight="700">Needs attention</Body>
            {openQueries.length > 0 ? (
              <Small>{openQueries.length} open quer{openQueries.length === 1 ? "y" : "ies"}</Small>
            ) : null}
            {overdueCapas.length > 0 ? (
              <Small color={colors.destructive}>
                {overdueCapas.length} overdue CAPA{overdueCapas.length === 1 ? "" : "s"}
              </Small>
            ) : null}
          </Card>
        ) : null}

        {/* Site-side actions */}
        {isSiteSide ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Capture</Body>
            <View style={s.actions}>
              <Pressable
                style={s.action}
                onPress={() => router.push(
                  `/(app)/site/remote-audit/capture?auditId=${auditId}&linkedEntityType=facility&linkedEntityId=${auditId}` as never)}
              >
                <Camera size={15} color={colors.primary} />
                <Small color={colors.primary}>Take evidence photo</Small>
              </Pressable>
              <Pressable
                style={s.action}
                onPress={() => router.push(
                  `/(app)/site/remote-audit/documents/upload?auditId=${auditId}` as never)}
              >
                <Upload size={15} color={colors.primary} />
                <Small color={colors.primary}>Upload a document</Small>
              </Pressable>
            </View>
          </Card>
        ) : (
          <Pressable
            onPress={() => router.push(`/(app)/sponsor/remote-audit/${auditId}/decision` as never)}
          >
            <Card style={{ gap: spacing.xs }}>
              <View style={s.row}>
                <ShieldCheck size={18} color={colors.primary} />
                <Body weight="700">Scoring and decision</Body>
              </View>
              <Small color={colors.mutedFg}>
                Both axes, the blockers behind them, and the step-up-gated sign-off.
              </Small>
            </Card>
          </Pressable>
        )}

        {/* Queries */}
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <MessageSquare size={18} color={colors.primary} />
            <Body weight="700">Queries</Body>
          </View>
          {queries.length === 0 ? (
            <Small color={colors.mutedFg}>None raised yet.</Small>
          ) : (
            queries.map((query) => (
              <Pressable
                key={query.id}
                style={s.listRow}
                onPress={() => router.push(`/(app)/remote-audit/queries/${query.id}` as never)}
              >
                <View style={{ flex: 1, gap: 2 }}>
                  <Small color={colors.foreground}>{query.title}</Small>
                  <Small color={colors.mutedFg}>{query.criticality}</Small>
                </View>
                <Pill label={query.status} tone={QUERY_TONE[query.status] ?? colors.mutedFg} />
              </Pressable>
            ))
          )}
        </Card>

        {/* CAPAs */}
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <ClipboardCheck size={18} color={colors.primary} />
            <Body weight="700">Corrective actions</Body>
          </View>
          {capas.length === 0 ? (
            <Small color={colors.mutedFg}>None raised yet.</Small>
          ) : (
            capas.map((capa) => {
              const overdue = capa.isOverdue || computeOverdue(capa.status, capa.dueDate);
              return (
                <Pressable
                  key={capa.id}
                  style={s.listRow}
                  onPress={() => router.push(`/(app)/remote-audit/capas/${capa.id}` as never)}
                >
                  <View style={{ flex: 1, gap: 2 }}>
                    <Small color={colors.foreground}>{capa.title}</Small>
                    <View style={s.row}>
                      <Clock size={12} color={overdue ? colors.destructive : colors.mutedFg} />
                      <Small color={overdue ? colors.destructive : colors.mutedFg}>
                        due {capa.dueDate}{overdue ? " · overdue" : ""}
                      </Small>
                    </View>
                  </View>
                  <Pill label={capa.status} tone={CAPA_TONE[capa.status] ?? colors.mutedFg} />
                </Pressable>
              );
            })
          )}
        </Card>

        {/* Documents */}
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <FileText size={18} color={colors.primary} />
            <Body weight="700">Documents</Body>
          </View>
          {documents.length === 0 ? (
            <Small color={colors.mutedFg}>
              Nothing uploaded yet. Documents stay hidden until they have been scanned.
            </Small>
          ) : (
            documents.map((doc) => (
              <Pressable
                key={doc.id}
                style={s.listRow}
                onPress={() => router.push(`/(app)/sponsor/remote-audit/documents/${doc.id}` as never)}
              >
                <View style={{ flex: 1, gap: 2 }}>
                  <Small color={colors.foreground}>{doc.displayName}</Small>
                  <Small color={colors.mutedFg}>{doc.documentType.replace(/_/g, " ")}</Small>
                </View>
                <Pill label={doc.status} tone={DOC_TONE[doc.status] ?? colors.mutedFg} />
              </Pressable>
            ))
          )}
          {documents.some((d) => d.status === "PROCESSING_BLOCKED") ? (
            <Small color={colors.destructive}>
              Something is stuck at PROCESSING_BLOCKED — the malware scanner is unreachable, so
              those files stay isolated rather than being shown unscanned.
            </Small>
          ) : null}
        </Card>

        {/* Virtual visits */}
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <Video size={18} color={colors.primary} />
            <Body weight="700">Virtual visits</Body>
          </View>
          {sessions.length === 0 ? (
            <Small color={colors.mutedFg}>None scheduled.</Small>
          ) : (
            sessions.map((session) => (
              <Pressable
                key={session.id}
                style={s.listRow}
                onPress={() => router.push(
                  `/(app)/remote-audit/virtual-sessions/${session.id}?auditId=${auditId}` as never)}
              >
                <View style={{ flex: 1, gap: 2 }}>
                  <Small color={colors.foreground}>
                    {session.scheduledStartUtc
                      ? new Date(session.scheduledStartUtc).toLocaleString()
                      : "Unscheduled"}
                  </Small>
                  {/* The recording posture is stated in the list, not just
                      behind a tap. Someone deciding whether to join should not
                      have to open the screen to learn it. */}
                  <Small color={session.recordingEnabled ? colors.destructive : colors.mutedFg}>
                    {session.recordingEnabled ? "will be recorded" : "not recorded"}
                  </Small>
                </View>
                <Pill
                  label={session.consentStatus}
                  tone={consentPermitsJoin(session) ? colors.success : colors.warning} />
              </Pressable>
            ))
          )}
          {!livekitConfigured && sessions.length > 0 ? (
            <Small color={colors.mutedFg}>
              Video isn&apos;t configured on this deployment, so these can be scheduled but not
              joined. Everything else on the audit is unaffected.
            </Small>
          ) : null}
        </Card>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md },
  body: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  listRow: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm,
    paddingVertical: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border,
  },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  action: {
    flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 9,
    borderRadius: radii.pill, borderWidth: 1, borderColor: colors.primary + "44",
    backgroundColor: colors.secondary,
  },
});
