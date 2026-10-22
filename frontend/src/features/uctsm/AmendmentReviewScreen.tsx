import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { RuleHandlingPanel } from "./RuleHandlingPanel";
import {
  getAmendmentPair, getTrialAmendments, prepareAmendmentPair, reviewAmendmentMapping,
} from "./api";
import {
  describeBlocker, describeMapping, describeSource, failureOf, mappingActions, parseRules, pendingMappings,
  type MappingRow, type PairView,
} from "./amendments.ts";

const first = (value?: string | string[]) => (Array.isArray(value) ? value[0] : value);

/**
 * Compare two approved schedule versions and decide how their visits correspond (P6).
 *
 * A shared visit code proves nothing: an exact code, name and type is matched
 * automatically, everything else is a PROPOSAL a reviewer confirms, rejects or replaces.
 * Each decision is recorded under the signed-in user with the reason and the revision the
 * reviewer saw; the server recomputes the automatic matches and keeps rejected proposals on
 * record. Nothing here moves a patient.
 */
export default function AmendmentReviewScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ trialId?: string }>();
  const trialId = first(params.trialId);
  const [overview, setOverview] = useState<Awaited<ReturnType<typeof getTrialAmendments>> | null>(null);
  const [view, setView] = useState<PairView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [rulesText, setRulesText] = useState<Record<string, string>>({});
  const generation = useRef(0);

  const load = useCallback(async () => {
    if (!trialId) return;
    generation.current += 1;
    const gen = generation.current;
    setBusy(true); setError("");
    try {
      const data = await getTrialAmendments(trialId);
      if (gen === generation.current) setOverview(data);
    } catch (failure) {
      if (gen === generation.current) setError(failureOf(failure).message);
    } finally {
      if (gen === generation.current) setBusy(false);
    }
  }, [trialId]);
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, [load]);

  if (!trialId) {
    return <ScreenContainer><ScreenHeader eyebrow="Amendments" title="Compare versions" onBack={() => router.back()} />
      <ScrollView contentContainerStyle={{ padding: spacing.md }}>
        <Small>Open this from a trial so we know which one to compare.</Small></ScrollView></ScreenContainer>;
  }

  const run = async (action: () => Promise<PairView>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { setView(await action()); await load(); } catch (failure) {
      const info = failureOf(failure);
      setError(info.message);
      if (info.kind === "conflict" && view) {
        try { setView(await getAmendmentPair(trialId, view.pair_id)); } catch { /* the message stands */ }
      }
    } finally { setBusy(false); }
  };

  const decide = (row: MappingRow, action: string) => {
    if (!view) return;
    if (reason.trim().length < 3) return setError("Say why before you decide.");
    const body: Record<string, unknown> = {
      expected_revision: view.mapping_set_revision, action, mapping_id: row.id, reason: reason.trim() };
    if (action === "SET_RULES") {
      const parsed = parseRules(rulesText[row.id] ?? "");
      if ("error" in parsed) return setError(parsed.error);
      body.rules = parsed.rules;
    }
    void run(() => reviewAmendmentMapping(trialId, view.pair_id, body));
  };

  const candidates = (overview?.versions ?? []).filter((item) =>
    item.status === "APPROVED" && item.based_on_schedule_version_id);
  const pending = view ? pendingMappings(view).length : 0;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Amendments" title="Compare versions" onBack={() => router.back()} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, gap: 12 }}>
        {busy && <ActivityIndicator />}
        {!!error && <Card><Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text></Card>}
        <Button variant="secondary" onPress={() => router.push({
          pathname: "/(app)/clinical/plane-operations", params: { trialId } } as never)}>
          Operations: failed messages, pilot cutover, reminders, reports</Button>
        <RuleHandlingPanel key={trialId} trialId={trialId} versions={overview?.versions ?? []} onApproved={() => void load()} />

        {!view && <Card>
          <Body weight="700">Approved amendments</Body>
          {candidates.length === 0 && !busy && <Small>
            This trial has no approved version that declares it amends another.</Small>}
          {candidates.map((item) => (
            <View key={item.schedule_version_id} style={styles.row}>
              <Text style={styles.rowTitle}>Version {item.version_number} amends an earlier version</Text>
              <Small>Approved {item.approved_at?.slice(0, 10) ?? "—"}
                {item.effective_from ? ` · in force from ${item.effective_from}` : ""}</Small>
              <Button variant="secondary" disabled={busy} onPress={() => void run(() => prepareAmendmentPair(
                trialId, item.based_on_schedule_version_id!, item.schedule_version_id))}>
                Compare with the version it amends</Button>
            </View>))}
        </Card>}

        {!!view && <>
          <Card>
            <Body weight="700">Version {view.from.version_number} → version {view.to.version_number}</Body>
            {[view.from, view.to].map((side, index) => (
              <Small key={side.schedule_version_id}>
                {index === 0 ? "Current" : "New"}: version {side.version_number} · approved {side.approved_at?.slice(0, 10) ?? "—"}
                {side.effective_from ? ` · in force from ${side.effective_from}` : ""}
                {" · "}{side.verification === "VERIFIED" ? "content verified" : "not verified"}
                {side.content_hash ? ` · ${side.content_hash.slice(0, 10)}` : ""}</Small>))}
            <Small>{view.change_summary.total} change(s): {view.change_summary.clinical} clinical,{" "}
              {view.change_summary.administrative} administrative.</Small>
            <Text style={view.ready_for_adoption ? styles.ok : styles.error}>
              {view.ready_for_adoption
                ? "Ready: every visit has a decided match. Patients can now be previewed for a move."
                : `Not ready: ${view.problems.length} thing(s) to resolve before any patient can be moved.`}
            </Text>
            {view.problems.map((problem, index) => {
              const text = describeBlocker(problem);
              return <View key={index} style={styles.row}>
                <Text style={styles.errorSmall}>{text.title}</Text>
                {!!text.detail && <Small>{text.detail}</Small>}<Small>{text.action}</Small></View>;
            })}
            <Pressable accessibilityRole="button" onPress={() => { setView(null); void load(); }}>
              <Text style={styles.link}>Back to the list</Text></Pressable>
          </Card>

          <Card>
            <Body weight="700">What changed</Body>
            {view.changes.length === 0 && <Small>No differences were found.</Small>}
            {[...view.changes].sort((a, b) => (a.significance === b.significance ? 0 : a.significance === "CLINICAL" ? -1 : 1))
              .map((change, index) => (
                <View key={index} style={styles.row}>
                  <Text style={styles.rowTitle}>{change.entity_code} · {change.change_type.replace(/_/g, " ").toLowerCase()}
                    {change.significance === "CLINICAL" ? " · clinical" : " · administrative"}</Text>
                  <Small>{change.summary}</Small>
                  {!!change.detail && <Small>{change.detail}</Small>}
                  {(change.sources?.from ?? []).map((source, at) => (
                    <Small key={`f${at}`}>Current version cites {describeSource(source)}</Small>))}
                  {(change.sources?.to ?? []).map((source, at) => (
                    <Small key={`t${at}`}>New version cites {describeSource(source)}</Small>))}
                </View>))}
          </Card>

          <Card>
            <Body weight="700">How the visits correspond</Body>
            <Small>{pending > 0 ? `${pending} match(es) are proposals that need your decision.`
              : "Every match is decided."} A shared visit code is not proof of the same visit.</Small>
            <TextInput accessibilityLabel="Reason" placeholder="Why? (recorded under your name)"
              placeholderTextColor={colors.mutedFg} style={styles.input} value={reason}
              onChangeText={setReason} multiline />
            {view.mappings.map((row) => (
              <View key={row.id} style={styles.row}>
                <Text style={styles.rowTitle}>{describeMapping(row)}</Text>
                <Small>{row.review_status === "AUTO_CONFIRMED" ? "Matched automatically (same code, name and type)"
                  : row.review_status === "PROPOSED" ? `Proposed (${Math.round(row.confidence * 100)}% sure) - ${row.rationale}`
                  : row.review_status === "CONFIRMED" ? "Confirmed by a reviewer"
                  : "Rejected by a reviewer (kept on record)"}</Small>
                {row.occurrence_rules.map((rule, index) => (
                  <Small key={index}>{rule.source_code}:{rule.source_index} → {rule.target_code}:{rule.target_index}</Small>))}
                {(row.mapping_type === "SPLIT" || row.mapping_type === "MERGED") && row.review_status !== "REJECTED" &&
                  <TextInput accessibilityLabel="Occurrence rules" placeholder="CODE:INDEX>CODE:INDEX, ..."
                    placeholderTextColor={colors.mutedFg} style={styles.input}
                    value={rulesText[row.id] ?? ""} onChangeText={(value) => setRulesText({ ...rulesText, [row.id]: value })} />}
                <View style={styles.actions}>
                  {mappingActions(row).map((action) => (
                    <Button key={action} variant={action === "REJECT" ? "secondary" : "primary"} disabled={busy}
                      onPress={() => decide(row, action)}>
                      {action === "CONFIRM" ? "Confirm" : action === "REJECT" ? "Reject" : "Save the rules and confirm"}
                    </Button>))}
                </View>
              </View>))}
          </Card>
        </>}
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10,
    fontFamily: fonts.regular, color: colors.foreground },
  error: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 13 },
  errorSmall: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12 },
  ok: { color: colors.foreground, fontFamily: fonts.bold, fontSize: 13 },
  row: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 8, gap: 2 },
  rowTitle: { fontFamily: fonts.bold, fontSize: 13, color: colors.foreground },
  actions: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  link: { color: colors.primary, fontFamily: fonts.medium, fontSize: 12 },
});
