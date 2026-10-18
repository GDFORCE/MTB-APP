import React, { useEffect, useMemo, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import {
  cancelAmendmentDecision, confirmAmendment, executeAmendmentDecision, getPatientAmendments,
  simulateAmendment,
} from "./api";
import {
  createAmendmentBinding, type AmendmentApi, type AmendmentSnapshot,
} from "./amendmentController.ts";
import {
  POINT_LABEL, SIMULATION_NOTE, buildPoint, consequences, decisionFor, describeBlocker,
  describeDecision, describePoint, describeVisit, groupVisits, type AdoptionMode, type PointForm,
} from "./amendments.ts";

const realApi: AmendmentApi = {
  status: getPatientAmendments, simulate: simulateAmendment, confirm: confirmAmendment,
  execute: executeAmendmentDecision, cancel: cancelAmendmentDecision,
};

const MODES: AdoptionMode[] = [
  "ADOPT_IMMEDIATELY", "ADOPT_AT_EVENT", "ADOPT_AT_CYCLE", "ADOPT_ON_DATE", "REMAIN_ON_V1"];

const EMPTY: AmendmentSnapshot = {
  phase: "checking", status: null, simulation: null, busy: false, error: "", blockers: [],
  notice: "", committedNeedsRefresh: false,
};

/**
 * Protocol amendments for ONE patient on the MongoDB patient plane (P6). It never moves a
 * patient by default: the clinician previews what a move would do (a read-only
 * calculation), chooses when - now, at a stated visit / cycle / date, or never - and
 * confirms with a reason. The preview is the server's own calculation; the server
 * recomputes and rechecks it on confirmation, and the schedule shown afterwards is the
 * committed evaluation, re-read from the server.
 */
export function PatientAmendmentPanel({
  trialId, externalPatientId, onChanged,
}: { trialId?: string; externalPatientId?: string | null; onChanged?: () => void }) {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<AmendmentSnapshot>(EMPTY);
  const [target, setTarget] = useState<string | null>(null);
  const [direction, setDirection] = useState<"ADOPT" | "RETURN">("ADOPT");
  const [form, setForm] = useState<PointForm>({ mode: "ADOPT_IMMEDIATELY" });
  const [reason, setReason] = useState("");
  const [replace, setReplace] = useState(false);
  const [localError, setLocalError] = useState("");
  const binding = useRef(createAmendmentBinding(realApi));
  const committed = useRef(0);
  const control = binding.current;

  useEffect(() => control.mount((next) => {
    setSnapshot(next);
    if (next.notice.startsWith("Done") || next.notice.startsWith("Recorded") || next.notice.startsWith("Already")) {
      committed.current += 1;
    }
  }), []);
  useEffect(() => {
    setTarget(null); setReason(""); setReplace(false); setLocalError(""); setDirection("ADOPT");
    control.setIdentity({ trialId, externalPatientId });
  }, [trialId, externalPatientId]);
  useEffect(() => { if (committed.current) onChanged?.(); }, [committed.current]);

  const { phase, status, simulation, busy, notice } = snapshot;
  const error = localError || snapshot.error;
  const available: any[] = status?.available ?? [];
  const chosen = available.find((item) => item.schedule_version_id === target) ?? available[0];
  const open = status?.open_decision ?? null;
  const groups = useMemo(() => groupVisits(simulation?.visits ?? []), [simulation]);

  if (phase === "checking" || phase === "absent") return null;
  if (phase === "forbidden") {
    return <Card><Body weight="700">Protocol amendments</Body>
      <Small>You do not have access to amendment decisions for this patient.</Small></Card>;
  }
  if (phase === "unavailable") {
    return <Card><Body weight="700">Protocol amendments</Body>
      <Small>{error || "Amendments could not be loaded."}</Small>
      <Pressable accessibilityRole="button" onPress={() => void control.retry()}>
        <Text style={styles.link}>Try again</Text></Pressable></Card>;
  }

  const current = status?.current_version;
  const openReview = () => router.push({
    pathname: "/(app)/clinical/amendment-review", params: { trialId } } as never);

  const preview = () => {
    const built = buildPoint(form);
    if ("error" in built) return setLocalError(built.error);
    if (direction === "ADOPT" && !chosen) return setLocalError("There is no later approved version to move to.");
    setLocalError("");
    void control.simulate({
      direction, adoption_point: built.point,
      ...(direction === "ADOPT" ? { to_schedule_version_id: chosen.schedule_version_id } : {}) });
  };
  const confirm = () => {
    if (reason.trim().length < 3) return setLocalError("Say why you are making this decision.");
    setLocalError("");
    const mode = (simulation?.adoption_point?.mode as AdoptionMode) ?? form.mode;
    void control.confirm({
      decision: decisionFor(mode, simulation?.direction ?? direction), reason: reason.trim(),
      ...(replace && open ? { replaces_decision_id: open.id } : {}) });
  };

  return (
    <Card>
      <Body weight="700">Protocol amendments</Body>
      {!!current && <Small>
        Current version {current.version_number} · approved {current.approved_at?.slice(0, 10) ?? "—"}
        {current.effective_from ? ` · in force from ${current.effective_from}` : ""}
        {current.verification !== "VERIFIED" ? " · content not verified (approved before checks existed)" : ""}
      </Small>}
      {!!notice && <Text style={styles.notice} accessibilityLiveRegion="polite">{notice}</Text>}
      {snapshot.committedNeedsRefresh && <Pressable accessibilityRole="button"
        onPress={() => void control.refreshAfterCommit()}><Text style={styles.link}>Reload</Text></Pressable>}

      {open && <View style={styles.row}>
        <Text style={styles.rowTitle}>Open decision</Text>
        <Small>{describeDecision(open)}</Small>
        {!!open.trigger_state?.detail && <Small>{open.trigger_state.detail}</Small>}
        {open.status === "NEEDS_RECONFIRMATION" && <Text style={styles.error}>
          What this would do no longer matches what was approved. Preview and confirm again, replacing this decision.</Text>}
        {(open.blockers || []).map((item: any, index: number) => (
          <Small key={index}>{describeBlocker(item).title}{item.detail ? ` — ${item.detail}` : ""}</Small>))}
        <View style={styles.actions}>
          {open.status === "WAITING" && <Button variant="secondary" disabled={busy}
            onPress={() => void control.execute(open.id)}>Apply if it is due</Button>}
          <Button variant="secondary" disabled={busy}
            onPress={() => void control.cancel(open.id, reason.trim().length >= 3 ? reason.trim() : "Cancelled from the patient screen")}>
            Cancel this decision</Button>
        </View>
      </View>}

      {available.length === 0 && !open && <Small>
        No later approved version of the protocol is waiting for this patient.</Small>}

      {available.length > 0 && !simulation && <View style={{ gap: 8 }}>
        {available.map((item) => (
          <Pressable key={item.schedule_version_id} accessibilityRole="button"
            onPress={() => setTarget(item.schedule_version_id)}
            style={[styles.row, chosen?.schedule_version_id === item.schedule_version_id && styles.rowOn]}>
            <Text style={styles.rowTitle}>Amendment: version {item.version_number}</Text>
            <Small>Approved {item.approved_at?.slice(0, 10) ?? "—"}
              {item.effective_from ? ` · in force from ${item.effective_from}` : ""}
              {item.change_summary ? ` · ${item.change_summary.clinical} clinical and ${item.change_summary.administrative} administrative change(s)` : ""}</Small>
            {!item.pair_id && <Small>A reviewer must compare this version with the current one first.</Small>}
            {item.pair_id && !item.ready_for_adoption && item.problems.map((problem: any, index: number) => {
              const view = describeBlocker(problem);
              return <Small key={index}>{view.title}. {view.action}</Small>;
            })}
            {item.pair_id && item.ready_for_adoption && <Small>Comparison reviewed.</Small>}
          </Pressable>))}
        <Pressable accessibilityRole="button" onPress={openReview}>
          <Text style={styles.link}>Open the comparison and event matching</Text></Pressable>

        <Body weight="700">When should this patient move?</Body>
        <Small>Nobody is moved automatically. Choose when - or that they stay.</Small>
        <View style={styles.chips}>
          {MODES.map((mode) => (
            <Pressable key={mode} accessibilityRole="button"
              onPress={() => { setForm({ ...form, mode }); setDirection("ADOPT"); }}
              style={[styles.chip, direction === "ADOPT" && form.mode === mode && styles.chipOn]}>
              <Text style={styles.chipText}>{POINT_LABEL[mode]}</Text></Pressable>))}
        </View>
        {form.mode === "ADOPT_AT_EVENT" && <>
          <TextInput accessibilityLabel="Visit code" placeholder="Visit code of the CURRENT version (e.g. C1D1)"
            placeholderTextColor={colors.mutedFg} style={styles.input} value={form.eventCode ?? ""}
            onChangeText={(value) => setForm({ ...form, eventCode: value })} />
          <TextInput accessibilityLabel="Occurrence" placeholder="Occurrence (0 is the first)"
            placeholderTextColor={colors.mutedFg} style={styles.input} value={form.occurrenceIndex ?? "0"}
            onChangeText={(value) => setForm({ ...form, occurrenceIndex: value })} /></>}
        {form.mode === "ADOPT_AT_CYCLE" && <>
          <TextInput accessibilityLabel="Repeat block" placeholder="Repeat block (e.g. TREATMENT)"
            placeholderTextColor={colors.mutedFg} style={styles.input} value={form.blockCode ?? ""}
            onChangeText={(value) => setForm({ ...form, blockCode: value })} />
          <TextInput accessibilityLabel="Cycle" placeholder="Cycle number (1 is the first)"
            placeholderTextColor={colors.mutedFg} style={styles.input} value={form.cycleNumber ?? ""}
            onChangeText={(value) => setForm({ ...form, cycleNumber: value })} /></>}
        {form.mode === "ADOPT_ON_DATE" && <TextInput accessibilityLabel="Date"
          placeholder="Move on (YYYY-MM-DD)" placeholderTextColor={colors.mutedFg} style={styles.input}
          value={form.onDate ?? ""} onChangeText={(value) => setForm({ ...form, onDate: value })} />}
        <Button onPress={preview} loading={busy} disabled={busy || (!!chosen && !chosen.ready_for_adoption)}>
          Preview what this would do</Button>
        {!!chosen && !chosen.ready_for_adoption && <Small>
          The preview needs the comparison reviewed first.</Small>}
      </View>}

      {status?.can_return && !simulation && <View style={{ gap: 6 }}>
        <Body weight="700">Return to the earlier version</Body>
        <Small>A return is a new, separately authorised decision. Facts recorded since the move are
          kept; anything that cannot go back is listed before you confirm.</Small>
        <Button variant="secondary" disabled={busy} onPress={() => {
          setDirection("RETURN"); setLocalError("");
          void control.simulate({ direction: "RETURN", adoption_point: { mode: "ADOPT_IMMEDIATELY" } });
        }}>Preview a return</Button>
      </View>}

      {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
      {snapshot.blockers.map((item, index) => {
        const view = describeBlocker(item);
        return <View key={`${item.code}-${index}`} style={styles.row}>
          <Text style={styles.error}>{view.title}</Text>
          {!!view.detail && <Small>{view.detail}</Small>}
          <Small>{view.action}</Small></View>;
      })}

      {!!simulation && <View style={{ gap: 8 }}>
        <Body weight="700">{simulation.direction === "RETURN" ? "Before you return this patient" : "Before you decide"}</Body>
        <Small>From version {simulation.from.version_number} to version {simulation.to.version_number}
          {" · "}{describePoint(simulation.adoption_point as any)}</Small>
        <Small>Version {simulation.from.version_number}: {simulation.from.verification === "VERIFIED" ? "content verified" : "not verified"}
          {" · "}version {simulation.to.version_number}: {simulation.to.verification === "VERIFIED" ? "content verified" : "not verified"}</Small>
        {consequences(simulation).map((line) => <Small key={line}>• {line}</Small>)}
        {groups.map((group) => <View key={group.id} style={{ gap: 4 }}>
          <Text style={styles.rowTitle}>{group.label} ({group.rows.length})</Text>
          {group.rows.map((row) => {
            const view = describeVisit(row);
            return <View key={`${row.from_key}-${row.to_key}`} style={styles.row}>
              <Text style={styles.rowTitle}>{view.title}</Text>
              {view.lines.map((line) => <Small key={line}>{line}</Small>)}</View>;
          })}
        </View>)}
        <Small>{SIMULATION_NOTE}</Small>
        {open && simulation.direction !== "RETURN" && simulation.adoption_point?.mode !== "REMAIN_ON_V1" && <Pressable
          accessibilityRole="checkbox" accessibilityState={{ checked: replace }}
          onPress={() => setReplace(!replace)}>
          <Text style={styles.link}>{replace ? "☑" : "☐"} Replace the open decision with this one</Text></Pressable>}
        <TextInput accessibilityLabel="Reason" placeholder="Why? (recorded in the audit trail)"
          placeholderTextColor={colors.mutedFg} style={styles.input} value={reason}
          onChangeText={setReason} multiline />
        <View style={styles.actions}>
          <Button onPress={confirm} loading={busy} disabled={busy || !simulation.can_confirm}>
            {simulation.direction === "RETURN" ? "Confirm the return"
              : simulation.adoption_point?.mode === "REMAIN_ON_V1" ? "Confirm: stay on the current version"
              : simulation.adoption_point?.mode === "ADOPT_IMMEDIATELY" ? "Confirm: move now" : "Confirm: move at that point"}
          </Button>
          <Button variant="secondary" onPress={() => control.discard()} disabled={busy}>Discard</Button>
        </View>
        {!simulation.can_confirm && <Small>It cannot be confirmed until the problems above are resolved.</Small>}
      </View>}

      {(status?.decisions?.length ?? 0) > 0 && <View style={{ gap: 4 }}>
        <Body weight="700">History</Body>
        {status.decisions.map((item: any) => <View key={item.id} style={styles.row}>
          <Text style={styles.rowTitle}>{describeDecision(item)}</Text>
          <Small>{item.created_at?.slice(0, 10)}{item.reason ? ` · ${item.reason}` : ""}
            {item.carried_fact_count ? ` · ${item.carried_fact_count} fact(s) carried` : ""}</Small>
        </View>)}
      </View>}
    </Card>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 10, paddingVertical: 6 },
  chipOn: { backgroundColor: colors.primary },
  chipText: { fontFamily: fonts.medium, fontSize: 12, color: colors.foreground },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10,
    fontFamily: fonts.regular, color: colors.foreground },
  error: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12 },
  notice: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 12 },
  row: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 8, gap: 2 },
  rowOn: { borderLeftWidth: 3, borderLeftColor: colors.primary, paddingLeft: 6 },
  rowTitle: { fontFamily: fonts.bold, fontSize: 13, color: colors.foreground },
  actions: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  link: { color: colors.primary, fontFamily: fonts.medium, fontSize: 12 },
});
