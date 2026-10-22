import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, Text, TextInput, View } from "react-native";
import { Body, Button, Small } from "@/src/components/ui";
import { colors } from "@/src/theme/tokens";
import { approvePlaneTable, getPlaneReview, type PlaneReview } from "./api";
import { reviewFailureMessage } from "./manualRules.ts";
import { REBASE_POLICIES } from "./trackReview";

/** This panel reviews the native version already selected; it never mirrors a SQL draft. */
export function PlaneReviewPanel({ trialId, versionId, mayApprove, onApproved }: {
  trialId: string; versionId: string; mayApprove: boolean; onApproved: () => void;
}) {
  const [data, setData] = useState<PlaneReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [openRules, setOpenRules] = useState<Record<string, boolean>>({});
  const generation = useRef(0);
  useEffect(() => {
    const gen = ++generation.current;
    setData(null); setError(""); setReason(""); setConfirmed(false); setOpenRules({}); setBusy(true);
    getPlaneReview(trialId, versionId).then((current) => {
      if (generation.current === gen) setData(current);
    }).catch((failure) => { if (generation.current === gen) setError(reviewFailureMessage(failure)); })
      .finally(() => { if (generation.current === gen) setBusy(false); });
    return () => { generation.current++; };
  }, [trialId, versionId, retry]);
  async function approve() {
    if (!data || busy || !confirmed || !mayApprove || reason.trim().length < 3) return;
    const gen = generation.current;
    setBusy(true); setError("");
    try {
      const saved = await approvePlaneTable(trialId, versionId, {
        expected_revision: data.revision, reviewed_entire_table: true, reason: reason.trim(),
      });
      if (generation.current !== gen) return;
      setData({ ...data, ...saved }); setConfirmed(false); onApproved();
    } catch (failure) {
      if (generation.current === gen) {
        setError(reviewFailureMessage(failure)); setConfirmed(false);
      }
    } finally { if (generation.current === gen) setBusy(false); }
  }
  return <View style={{ gap: 10, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 14 }}>
    <Body weight="700">Review the whole schedule</Body>
    <Small>Review every visit, rule and source below. Approval does not move enrolled patients to this version.</Small>
    {busy && <ActivityIndicator />}
    {!!error && <Text accessibilityLiveRegion="polite" style={{ color: colors.destructive }}>{error}</Text>}
    <Button variant="secondary" disabled={busy} onPress={() => setRetry((n) => n + 1)}>Reload schedule review</Button>
    {!!data && <>
      <Small>Version status: {data.status}</Small>
      <Small>{data.view.source_coverage_status.message}</Small>
      {data.view.document_level_issues.map((issue) => <Small key={issue.id}>{issue.message}</Small>)}
      {!!data.view.tracks?.length && <Body weight="700">Tracks and movement rules</Body>}
      {data.view.tracks?.map((track) => <View key={track.code} style={{ gap: 3 }}>
        <Body weight="700">{track.display_name} ({track.code})</Body>
        <Small>{REBASE_POLICIES.find((policy) => policy.value === track.rebase_policy)?.label || "Movement policy not stated"}</Small>
        <Small>Reference: {track.anchor_code || "No single track anchor stated"} · {track.review_state}</Small>
        <Small>Stopping: {track.stoppable === true ? "allowed" : track.stoppable === false ? "not allowed" : "not stated"}</Small>
        <Small>Visits: {track.events.join(", ") || "No members"}{track.process_code ? ` · Process: ${track.process_code}` : ""}</Small>
      </View>)}
      {data.view.track_links?.map((link, i) => <Small key={`link-${i}`}>
        {link.target_track_code} follows {link.relation === "FOLLOWS_ACTUAL" ? "actuals" : "planned dates"} in {link.source_track_code} · {link.review_state}
      </Small>)}
      {data.view.processes?.map((process) => <Small key={process.code}>
        {process.display_name}: {process.hold_resume_label}. {process.resume_trigger_label}. Visits: {process.visits.join(", ")}
      </Small>)}
      {data.view.rows.map((row) => <View key={row.id} style={{ gap: 4, paddingVertical: 8 }}>
        <Body weight="700">{row.visit_name} ({row.code})</Body>
        <Small>{row.timing_summary}</Small><Small>Window: {row.window_summary}</Small>
        <Small>Applies: {row.applicability_summary}</Small>
        {row.pending_reasons.map((text, i) => <Small key={`pending-${i}`}>{text}</Small>)}
        {row.details.recurrence && <Small>Repeats: {row.details.recurrence.summary}</Small>}
        {row.details.anchors.map((anchor) => <Small key={anchor.code}>Reference: {anchor.display_name} · {anchor.status}</Small>)}
        {row.details.constraints.map((rule) => <View key={rule.id}>
          <Small>{rule.summary}</Small>
          <Pressable accessibilityRole="button" accessibilityState={{ expanded: !!openRules[rule.id] }}
            onPress={() => setOpenRules((current) => ({ ...current, [rule.id]: !current[rule.id] }))}>
            <Text style={{ color: colors.primary, paddingVertical: 6 }}>{openRules[rule.id] ? "Hide" : "Show"} exact stated rule</Text>
          </Pressable>
          {!!openRules[rule.id] && <Text selectable style={{ color: colors.foreground }}>{JSON.stringify(rule.rule, null, 2)}</Text>}
        </View>)}
        {row.details.activities.map((activity) => <View key={activity.id}>
          <Small>Assessment: {activity.name} · {activity.requiredness}{activity.timing_summary ? ` · ${activity.timing_summary}` : ""}</Small>
          <Small>{activity.applicability_summary}</Small>
          {activity.conditions.map((condition, i) => <Small key={i}>Only if {condition}</Small>)}
          {activity.footnotes.map((note, i) => <Small key={i}>Footnote {note.marker}: {note.text} · {note.resolved ? "resolved" : "needs review"}</Small>)}
        </View>)}
        {row.details.evidence.map((source) => <Small key={source.id}>
          Source {source.page_number ? `page ${source.page_number}` : "page not stated"}: {source.quote || "Quote missing"}
        </Small>)}
      </View>)}
      {data.view.shared_review_questions.map((question) => <Small key={question.id}>{question.title}</Small>)}
      {data.view.approval_blockers.map((blocker, i) => <Small key={`block-${i}`}>{blocker}</Small>)}
      {data.status === "APPROVED" && <Small accessibilityLiveRegion="polite">Approved. This version is immutable.</Small>}
      {mayApprove && data.view.can_approve && !["APPROVED", "SUPERSEDED", "ARCHIVED"].includes(data.status) && <>
        <Small>The person who last made a material edit must use another authorised approver.</Small>
        <TextInput accessibilityLabel="Whole schedule approval reason" placeholder="Reason confirming the whole schedule review"
          value={reason} editable={!busy} maxLength={2000} onChangeText={setReason} multiline
          style={{ borderWidth: 1, borderColor: colors.border, padding: 10, color: colors.foreground }} />
        <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: confirmed }} disabled={busy}
          onPress={() => setConfirmed((value) => !value)}>
          <Text style={{ color: colors.primary, paddingVertical: 8 }}>
            {confirmed ? "☑ " : "☐ "}I reviewed the entire table, including rules, manual handling and source evidence.
          </Text>
        </Pressable>
        <Button disabled={busy || !confirmed || reason.trim().length < 3} onPress={() => void approve()}>
          Approve this schedule version
        </Button>
      </>}
    </>}
  </View>;
}
