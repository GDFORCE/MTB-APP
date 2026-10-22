import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import { ConflictPanel } from "./ConflictPanel";
import { PlaneReviewPanel } from "./PlaneReviewPanel";
import { RequiredInputsPanel } from "./RequiredInputsPanel";
import { approveRuleHandling, declareRuleHandling, getMyCapabilities, getRuleHandling } from "./api";
import { approveRuleBody, canHandleRules, declareRuleBody, editableRules, emptyManualForm,
  reviewFailureMessage, type RuleView } from "./manualRules.ts";

/** Explicit review of gaps. The signed-in requester owns the request; another approver decides. */
export function RuleHandlingPanel({ trialId, versions, onApproved }: { trialId: string;
  versions: { schedule_version_id: string; version_number: number; status: string }[]; onApproved?: () => void }) {
  const [version, setVersion] = useState<string | null>(null);
  const [view, setView] = useState<RuleView | null>(null);
  const [mine, setMine] = useState<Awaited<ReturnType<typeof getMyCapabilities>> | null>(null);
  const [eventCode, setEvent] = useState("");
  const [form, setForm] = useState(emptyManualForm);
  const [reviewReason, setReviewReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  useEffect(() => { setVersion(null); }, [trialId]);
  useEffect(() => {
    const gen = ++generation.current;
    setView(null); setMine(null); setEvent(""); setForm(emptyManualForm()); setReviewReason("");
    setError(""); setNotice("");
    if (!version) { setBusy(false); return () => { generation.current++; }; }
    setBusy(true);
    Promise.all([getRuleHandling(trialId, version), getMyCapabilities()]).then(([data, rights]) => {
      if (generation.current === gen) { setView(data); setMine(rights); }
    }).catch((failure) => { if (generation.current === gen) setError(reviewFailureMessage(failure)); })
      .finally(() => { if (generation.current === gen) setBusy(false); });
    return () => { generation.current++; };
  }, [trialId, version, retry]);

  const chooseEvent = (code: string) => { setEvent(code); setForm(emptyManualForm()); setReviewReason(""); setNotice(""); };
  const save = async (approve: boolean) => {
    if (!view || !version || busy) return;
    const gen = generation.current;
    let body;
    try { body = approve ? approveRuleBody(view, eventCode, reviewReason) : declareRuleBody(view, eventCode, form); }
    catch (failure) { setError((failure as Error).message); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      await (approve ? approveRuleHandling : declareRuleHandling)(trialId, body);
      if (generation.current !== gen) return;
      setNotice(approve ? "Independent decision recorded. The schedule still needs its normal approval."
        : "Request recorded under your name. Manual-only requests need another approver.");
      // Prevent a second write using a stale revision even if the refresh fails.
      setView(null); setForm(emptyManualForm()); setReviewReason("");
      try {
        const updated = await getRuleHandling(trialId, version);
        if (generation.current === gen) setView(updated);
      } catch (failure) {
        if (generation.current === gen) setError(`The decision was saved, but its current state could not be loaded. ${reviewFailureMessage(failure)}`);
      }
    } catch (failure) {
      if (generation.current === gen) setError(reviewFailureMessage(failure));
    } finally { if (generation.current === gen) setBusy(false); }
  };
  const selected = view?.events.find((row) => row.code === eventCode);
  const canEdit = !!view && editableRules(view.status);
  return <Card>
    <Body weight="700">Manual and unsupported rules</Body>
    <Small>Keep a rule visible when automatic scheduling is unsafe. Manual-only handling creates no automatic protocol target. Evidence and a separate approver are required.</Small>
    {versions.map((row) => <Pressable key={row.schedule_version_id} accessibilityRole="button"
      accessibilityState={{ selected: row.schedule_version_id === version }} disabled={busy}
      onPress={() => setVersion(row.schedule_version_id)}>
      <Text style={styles.link}>Version {row.version_number} · {row.status}{row.schedule_version_id === version ? " · selected" : ""}</Text>
    </Pressable>)}
    {versions.length === 0 && <Small>No schedule version is available for this trial.</Small>}
    {busy && <ActivityIndicator />}
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
    {!!notice && <Small accessibilityLiveRegion="polite">{notice}</Small>}
    {!!version && !busy && <Button variant="secondary" onPress={() => setRetry((value) => value + 1)}>Reload rules and access</Button>}
    {!!view && <>
      {!canEdit && <Small>This approved or archived version is read-only. Create a new draft to change handling.</Small>}
      {view.events.map((event) => <Pressable key={event.code} accessibilityRole="button"
        accessibilityState={{ selected: eventCode === event.code }} disabled={busy} onPress={() => chooseEvent(event.code)}>
        <Text style={styles.link}>{event.display_name} ({event.code}) · {event.handling
          ? `${event.handling.kind} / ${event.handling.status}` : "no manual handling declared"}</Text>
      </Pressable>)}
      {view.issues.filter((issue) => issue.blocking).map((issue, index) => <Small key={index}>
        {issue.issue_code}: {issue.message}{issue.details?.field_path ? ` (${issue.details.field_path})` : ""}</Small>)}
      {!!selected && <View style={styles.form}>
        <Body weight="700">{selected.display_name}</Body>
        {!!selected.handling && <>
          <Small>{selected.handling.reason}</Small><Small>Workflow: {selected.handling.policy_reference}</Small>
          <Small>Responsible requester: {selected.handling.owner_id}</Small>
          <Small>{selected.handling.approved_by ? `Independently approved by ${selected.handling.approved_by}` : "Independent approval pending"}</Small>
        </>}
        {canEdit && canHandleRules(mine, trialId, "schedule.correct") && <>
          {(["MANUAL_ONLY", "UNSUPPORTED"] as const).map((kind) => <Pressable key={kind} accessibilityRole="radio"
            accessibilityState={{ checked: form.kind === kind }} disabled={busy} onPress={() => setForm({ ...form, kind })}>
            <Text style={styles.link}>{form.kind === kind ? "● " : "○ "}{kind === "MANUAL_ONLY" ? "Request a reviewed manual workflow" : "Keep blocked as unsupported"}</Text>
          </Pressable>)}
          <TextInput style={styles.input} accessibilityLabel="Handling reason" placeholder="Reason for handling" value={form.reason}
            editable={!busy} onChangeText={(reason) => setForm({ ...form, reason })} multiline />
          <TextInput style={styles.input} accessibilityLabel="Manual workflow reference" placeholder="Permitted manual workflow or policy reference"
            value={form.policy} editable={!busy} onChangeText={(policy) => setForm({ ...form, policy })} multiline />
          <Small>Select the source evidence:</Small>
          {view.evidence.map((source) => <Pressable key={source.id} accessibilityRole="checkbox"
            accessibilityState={{ checked: form.evidence.includes(source.id) }} disabled={busy}
            onPress={() => setForm({ ...form, evidence: form.evidence.includes(source.id)
              ? form.evidence.filter((id) => id !== source.id) : [...form.evidence, source.id] })}>
            <Text style={styles.link}>{form.evidence.includes(source.id) ? "☑ " : "☐ "}
              {source.page_number ? `Page ${source.page_number}` : "Page not stated"} · {source.section_title || "Section not stated"}</Text>
            <Small>{source.source_text || "Source quote is missing; review the source before proceeding."}</Small>
          </Pressable>)}
          <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: form.responsible }} disabled={busy}
            onPress={() => setForm({ ...form, responsible: !form.responsible })}>
            <Text style={styles.link}>{form.responsible ? "☑ " : "☐ "}I accept responsibility for this request.</Text>
          </Pressable>
          <Button disabled={busy} onPress={() => void save(false)}>Record handling request</Button>
        </>}
        {canEdit && selected.handling?.kind === "MANUAL_ONLY" && selected.handling.status === "PENDING"
          && canHandleRules(mine, trialId, "schedule.approve") && <>
            <Small>You must be a different person from the requester. This decision does not approve the whole schedule.</Small>
            <TextInput style={styles.input} accessibilityLabel="Independent review reason" value={reviewReason}
              placeholder="Independent review reason" editable={!busy} onChangeText={setReviewReason} multiline />
            <Button disabled={busy} onPress={() => void save(true)}>Approve manual handling independently</Button>
          </>}
      </View>}
      {!!version && <RequiredInputsPanel key={`inputs-${version}-${view.revision}`} trialId={trialId} versionId={version}
        onChanged={() => setRetry((value) => value + 1)} />}
      {!!version && <ConflictPanel key={`conflicts-${version}-${view.revision}`} trialId={trialId} versionId={version}
        onResolved={() => setRetry((value) => value + 1)} />}
      {!!version && <PlaneReviewPanel key={`${version}-${view.revision}`} trialId={trialId} versionId={version}
        mayApprove={canHandleRules(mine, trialId, "schedule.approve")} onApproved={() => {
          setRetry((value) => value + 1); onApproved?.();
        }} />}
    </>}
  </Card>;
}
const styles = StyleSheet.create({
  form: { gap: 10, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  link: { color: colors.primary, fontFamily: fonts.medium, paddingVertical: 8 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground },
  error: { color: colors.destructive },
});
