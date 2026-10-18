import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, ChevronRight, ShieldCheck } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  actionLabel, classificationLabel, identityBlockFrom, resolutionBody, resumeNote,
  reviewStatusLabel, validateResolution,
  type ProtocolIdentity, type ReviewAction,
} from "./protocolIdentity";
import {
  getProtocolIdentity, listProtocolIdentities, resolveProtocolIdentity, resumeProtocolIdentity,
} from "./protocolIdentityApi";

const errorText = (error: any) => {
  const block = identityBlockFrom(error);
  if (block) return block.message;
  const detail = error?.response?.data?.detail;
  if (error?.response?.status === 403) return "You don't have permission to review protocol identities.";
  return (typeof detail === "string" ? detail : detail?.message) || error?.message
    || "The protocol identity review could not be loaded.";
};

/**
 * Quarantine review (P2-04): every uploaded protocol whose identity could not be
 * verified waits here. A reviewer sees what was submitted, what the document
 * says about itself (page + line), the hashes and registry status, and decides.
 * The server enforces the permission, the reason and the revision; this screen
 * only makes the allowed choices reachable and explains the outcome.
 */
export default function ProtocolIdentityReviewScreen() {
  const params = useLocalSearchParams<{ id?: string }>();
  const [items, setItems] = useState<ProtocolIdentity[]>([]);
  const [canReview, setCanReview] = useState(false);
  const [selected, setSelected] = useState<ProtocolIdentity | null>(null);
  const [busy, setBusy] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState("");
  const [action, setAction] = useState<ReviewAction | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [reason, setReason] = useState("");

  const open = useCallback(async (id: string) => {
    setError(""); setOutcome("");
    try {
      const detail = await getProtocolIdentity(id);
      setSelected(detail);
      setAction(detail.allowed_actions[0] ?? null);
      setIdentifier(detail.suggested_correction?.expected_identifier ?? "");
      setReason("");
    } catch (cause) { setError(errorText(cause)); }
  }, []);

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const response = await listProtocolIdentities("PENDING_REVIEW");
      setItems(response.items); setCanReview(response.can_review);
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (params.id) void open(String(params.id)); }, [params.id, open]);

  const submit = async () => {
    if (!selected || !action) return;
    const problem = validateResolution(action, reason, identifier);
    if (problem) { setError(problem); return; }
    setSaving(true); setError("");
    try {
      const result = await resolveProtocolIdentity(
        selected.id, resolutionBody(selected, action, reason, identifier));
      setOutcome(action === "REJECT" ? "Rejected. The document will not be extracted."
        : action === "KEEP_QUARANTINED" ? "Kept in quarantine."
        : resumeNote(result.resume));
      setSelected(null); await load();
    } catch (cause) { setError(errorText(cause)); } finally { setSaving(false); }
  };

  const retryResume = async () => {
    if (!selected) return;
    setSaving(true); setError("");
    try {
      const result = await resumeProtocolIdentity(selected.id);
      setOutcome(resumeNote(result.resume));
    } catch (cause) { setError(errorText(cause)); } finally { setSaving(false); }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Protocols" title="Protocol identity review" />
      {busy ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {!!error && (
            <Card style={styles.notice}>
              <AlertTriangle size={20} color={colors.warning} />
              <Small style={styles.flex}>{error}</Small>
            </Card>
          )}
          {!!outcome && (
            <View testID="identity-review-outcome">
            <Card style={styles.notice}>
              <ShieldCheck size={20} color={colors.success} />
              <Small style={styles.flex}>{outcome}</Small>
            </Card>
            </View>
          )}

          {selected ? (
            <View testID="identity-review-detail">
            <Card style={{ gap: 8 }}>
              <Body weight="700">{selected.filename}</Body>
              <Small>
                {classificationLabel(selected.classification)} · {reviewStatusLabel(selected.review_status)}
              </Small>
              {(selected.issues ?? []).filter((item) => item.severity === "BLOCKING").map((item) => (
                <Small key={item.code + item.message} color={colors.destructive}>
                  • {item.message}{item.page ? ` (page ${item.page})` : ""}
                </Small>
              ))}

              <Text style={styles.heading}>Submitted as</Text>
              {(selected.expected ?? []).length
                ? (selected.expected ?? []).map((item) => (
                  <Small key={item.normalized + item.source}>{item.value} ({item.source.toLowerCase().replace("_", " ")})</Small>))
                : <Small>No expected identifier was supplied.</Small>}

              <Text style={styles.heading}>What the document says</Text>
              {(selected.document_identifiers ?? []).length
                ? (selected.document_identifiers ?? []).map((item) => (
                  <View key={`${item.normalized}-${item.page}-${item.snippet}`} style={styles.evidence}>
                    <Small weight="700">
                      {item.value} · page {item.page} · {item.authoritative ? "states its own identity" : "mention only"}
                    </Small>
                    <Small>{item.snippet}</Small>
                  </View>))
                : <Small>No protocol identifiers could be read.</Small>}
              {selected.title ? <Small>Title (page {selected.title.page}): {selected.title.value}</Small> : null}
              {selected.version_label ? <Small>Version: {selected.version_label.value}{selected.version_date ? ` · ${selected.version_date}` : ""}</Small> : null}

              <Text style={styles.heading}>Checks</Text>
              <Small>Registry: {selected.registry?.status?.toLowerCase().replace("_", " ") ?? "not checked"}</Small>
              <Small>
                Text read: {selected.text_coverage
                  ? `${selected.text_coverage.pages_with_text} of ${selected.text_coverage.pages_total} pages`
                  : "unknown"}
              </Small>
              <Small>File fingerprint: {selected.binary_sha256.slice(0, 16)}…</Small>
              {selected.text_sha256 ? <Small>Text fingerprint: {selected.text_sha256.slice(0, 16)}…</Small> : null}

              {!!selected.reviews?.length && <>
                <Text style={styles.heading}>History</Text>
                {selected.reviews.map((item) => (
                  <Small key={item.id}>{actionLabel(item.action)} — {item.reason}</Small>))}
              </>}

              {selected.review_status === "PENDING_REVIEW" && canReview ? <>
                <Text style={styles.heading}>Decision</Text>
                {selected.allowed_actions.map((item) => (
                  <Pressable
                    key={item} testID={`identity-action-${item}`} accessibilityRole="radio"
                    accessibilityState={{ selected: action === item }}
                    onPress={() => setAction(item)}
                    style={[styles.choice, action === item && styles.choiceActive]}>
                    <Small weight={action === item ? "700" : "400"}>{actionLabel(item)}</Small>
                  </Pressable>))}
                {(action === "CORRECT_EXPECTED_IDENTITY" || action === "CONFIRM_DOCUMENT_IDENTITY") && (
                  <TextInput
                    testID="identity-identifier" accessibilityLabel="Protocol identifier"
                    value={identifier} onChangeText={setIdentifier} autoCapitalize="characters"
                    placeholder="Protocol identifier, e.g. NCT02088554" style={styles.input} />
                )}
                <TextInput
                  testID="identity-reason" accessibilityLabel="Reason for this decision"
                  value={reason} onChangeText={setReason} multiline
                  placeholder="Reason for this decision (required)" style={[styles.input, { minHeight: 70 }]} />
                <Button onPress={submit} loading={saving} disabled={saving || !action}>Record decision</Button>
              </> : null}
              {selected.review_status === "APPROVED" && canReview ? (
                <Button variant="secondary" onPress={retryResume} loading={saving} disabled={saving}>
                  Retry extraction
                </Button>
              ) : null}
              {selected.review_status === "PENDING_REVIEW" && !canReview ? (
                <Small>Only an authorized reviewer can resolve this document.</Small>
              ) : null}
              <Button variant="secondary" onPress={() => setSelected(null)}>Back to list</Button>
            </Card>
            </View>
          ) : !items.length && !error ? (
            <Card style={styles.empty}>
              <ShieldCheck size={28} color={colors.mutedFg} />
              <Body weight="700" style={{ marginTop: 10 }}>Nothing is waiting for review</Body>
              <Small style={styles.emptyText}>
                An upload appears here when its protocol identity could not be verified.
              </Small>
            </Card>
          ) : items.map((item) => (
            <Pressable
              key={item.id} testID={`identity-item-${item.id}`} accessibilityRole="button"
              accessibilityLabel={`Review ${item.filename}`} onPress={() => void open(item.id)}
              style={styles.row}>
              <View style={styles.flex}>
                <Text style={styles.title}>{item.filename}</Text>
                <Text style={styles.meta}>
                  {classificationLabel(item.classification)} · {item.issue_codes.join(", ").toLowerCase().replace(/_/g, " ")}
                </Text>
              </View>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Pressable>
          ))}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 10 },
  flex: { flex: 1 },
  notice: { flexDirection: "row", alignItems: "center", gap: 10 },
  empty: { alignItems: "center", paddingVertical: 30 },
  emptyText: { marginTop: 4, textAlign: "center", maxWidth: 300 },
  heading: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginTop: 10 },
  evidence: {
    borderLeftWidth: 3, borderLeftColor: colors.border, paddingLeft: 8, marginTop: 4, gap: 2,
  },
  choice: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, marginTop: 4,
  },
  choiceActive: { borderColor: colors.primary, backgroundColor: colors.card },
  input: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, marginTop: 8,
    fontFamily: fonts.regular, color: colors.foreground,
  },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, padding: 14,
  },
  title: { fontFamily: fonts.semibold, fontSize: 15, color: colors.foreground },
  meta: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg, marginTop: 3 },
});
