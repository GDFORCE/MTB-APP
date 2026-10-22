import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import {
  acknowledgeRequiredInputs, addRequiredInput, getMyCapabilities, getRequiredInputs, removeRequiredInput,
} from "./api";
import { canHandleRules, reviewFailureMessage } from "./manualRules.ts";
import {
  acknowledgeBody, customBody, editableInputs, emptyCustomForm, KIND_HELP, reviewNotice, type InputKind,
  type RequiredInputsView,
} from "./requiredInputs.ts";

const KINDS: InputKind[] = ["TEXT", "NUMBER", "DATE", "DATETIME"];

/** The notice a reviewer must read and acknowledge: which patient details a clinic has to enter for this protocol. */
export function RequiredInputsPanel({ trialId, versionId, onChanged }: { trialId: string; versionId: string; onChanged?: () => void }) {
  const [view, setView] = useState<RequiredInputsView | null>(null);
  const [mine, setMine] = useState<Awaited<ReturnType<typeof getMyCapabilities>> | null>(null);
  const [form, setForm] = useState(emptyCustomForm);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const gen = ++generation.current;
    setView(null); setError(""); setBusy(true);
    Promise.all([getRequiredInputs(trialId, versionId), getMyCapabilities()]).then(([data, rights]) => {
      if (generation.current === gen) { setView(data); setMine(rights); }
    }).catch((failure) => { if (generation.current === gen) setError(reviewFailureMessage(failure)); })
      .finally(() => { if (generation.current === gen) setBusy(false); });
    return () => { generation.current++; };
  }, [trialId, versionId, retry]);
  const act = async (work: () => Promise<unknown>, after?: () => void) => {
    if (busy) return;
    const gen = generation.current;
    setBusy(true); setError("");
    try {
      await work();
      if (generation.current !== gen) return;
      after?.(); setRetry((value) => value + 1); onChanged?.();
    } catch (failure) {
      if (generation.current === gen) setError((failure as Error).message && !(failure as { response?: unknown }).response
        ? (failure as Error).message : reviewFailureMessage(failure));
    } finally { if (generation.current === gen) setBusy(false); }
  };
  const canEdit = !!view && editableInputs(view.status);
  const mayCorrect = canHandleRules(mine, trialId, "schedule.correct");
  const mayReview = canHandleRules(mine, trialId, "schedule.review");
  return <Card>
    <Body weight="700">Details the clinic must enter for each patient</Body>
    {busy && <ActivityIndicator />}
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
    {!!view && <>
      <Small accessibilityLiveRegion="polite">{reviewNotice(view)}</Small>
      {view.effective.map((item) => <View key={item.code} style={styles.row}>
        <Text style={styles.name}>{item.label} · {item.kind === "DATETIME" ? "date and time" : item.kind.toLowerCase()}
          {item.source === "CUSTOM" ? " · added by reviewer" : " · from the protocol rules"}</Text>
        <Small>{item.reason}</Small>
        {canEdit && mayCorrect && item.source === "CUSTOM" && <Button variant="secondary" disabled={busy}
          onPress={() => void act(() => removeRequiredInput(trialId, versionId, item.code, { reason: "Removed by reviewer" }))}>
          Remove</Button>}
      </View>)}
      {canEdit && mayCorrect && <View style={styles.form}>
        <Small>Add a detail of your own (for example the patient's weight):</Small>
        <TextInput style={styles.input} accessibilityLabel="Code" placeholder="CODE, for example WEIGHT_KG" value={form.code}
          editable={!busy} autoCapitalize="characters" onChangeText={(code) => setForm({ ...form, code })} />
        <TextInput style={styles.input} accessibilityLabel="Label" placeholder="What the clinic sees, for example Weight (kg)"
          value={form.label} editable={!busy} onChangeText={(label) => setForm({ ...form, label })} />
        {KINDS.map((kind) => <Pressable key={kind} accessibilityRole="radio" accessibilityState={{ checked: form.kind === kind }}
          disabled={busy} onPress={() => setForm({ ...form, kind })}>
          <Text style={styles.link}>{form.kind === kind ? "● " : "○ "}{kind === "DATETIME" ? "Date and time" : kind[0] + kind.slice(1).toLowerCase()} — {KIND_HELP[kind]}</Text>
        </Pressable>)}
        <TextInput style={styles.input} accessibilityLabel="Why the protocol needs it" placeholder="Why the protocol needs it"
          value={form.reason} editable={!busy} multiline onChangeText={(text) => setForm({ ...form, reason: text })} />
        <Button disabled={busy} onPress={() => { let body; try { body = customBody(form); } catch (failure) { setError((failure as Error).message); return; }
          void act(() => addRequiredInput(trialId, versionId, body), () => setForm(emptyCustomForm())); }}>Add this detail</Button>
      </View>}
      {canEdit && mayReview && view.needs_acknowledgement && !view.acknowledged && <View style={styles.form}>
        <Small>Approval stays blocked until you confirm you reviewed this list.</Small>
        <TextInput style={styles.input} accessibilityLabel="Acknowledgement" placeholder="Confirm you reviewed the list"
          value={reason} editable={!busy} multiline onChangeText={setReason} />
        <Button disabled={busy} onPress={() => { let body; try { body = acknowledgeBody(view, reason); } catch (failure) { setError((failure as Error).message); return; }
          void act(() => acknowledgeRequiredInputs(trialId, versionId, body), () => setReason("")); }}>I reviewed this list</Button>
      </View>}
      {view.acknowledgement && view.acknowledged && <Small>Acknowledged under reviewer {view.acknowledgement.reviewer_id}.</Small>}
    </>}
    {!busy && <Button variant="secondary" onPress={() => setRetry((value) => value + 1)}>Reload list</Button>}
  </Card>;
}
const styles = StyleSheet.create({
  row: { gap: 4, paddingVertical: 6 },
  name: { fontFamily: fonts.medium, color: colors.foreground },
  form: { gap: 10, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  link: { color: colors.primary, fontFamily: fonts.medium, paddingVertical: 6 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground },
  error: { color: colors.destructive },
});
