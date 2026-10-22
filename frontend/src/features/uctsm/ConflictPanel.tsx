import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import { getConflicts, getMyCapabilities, resolveConflict } from "./api";
import { conflictSummary, emptyConflictForm, evidenceLocation, resolveConflictBody, type Conflict } from "./conflicts.ts";
import { canHandleRules, reviewFailureMessage } from "./manualRules.ts";

/** Lists unresolved readings with their competing protocol text; a reviewer confirms one with evidence and a reason. */
export function ConflictPanel({ trialId, versionId, onResolved }: { trialId: string; versionId: string; onResolved?: () => void }) {
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [mine, setMine] = useState<Awaited<ReturnType<typeof getMyCapabilities>> | null>(null);
  const [active, setActive] = useState("");
  const [form, setForm] = useState(emptyConflictForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const gen = ++generation.current;
    setConflicts(null); setActive(""); setForm(emptyConflictForm()); setError("");
    setBusy(true);
    Promise.all([getConflicts(trialId, versionId), getMyCapabilities()]).then(([data, rights]) => {
      if (generation.current === gen) { setConflicts(data.conflicts); setMine(rights); }
    }).catch((failure) => { if (generation.current === gen) setError(reviewFailureMessage(failure)); })
      .finally(() => { if (generation.current === gen) setBusy(false); });
    return () => { generation.current++; };
  }, [trialId, versionId, retry]);
  const selected = conflicts?.find((item) => item.event_id === active);
  const mayResolve = canHandleRules(mine, trialId, "schedule.correct");
  const save = async () => {
    if (!selected || busy) return;
    let body;
    try { body = resolveConflictBody(selected, form); } catch (failure) { setError((failure as Error).message); return; }
    const gen = generation.current;
    setBusy(true); setError(""); setNotice("");
    try {
      await resolveConflict(trialId, versionId, selected.event_id, body);
      if (generation.current !== gen) return;
      setNotice("Reading confirmed under your name. The schedule needs its normal review and approval.");
      setConflicts(null); setActive(""); setForm(emptyConflictForm());
      setRetry((value) => value + 1); onResolved?.();
    } catch (failure) {
      if (generation.current === gen) setError(reviewFailureMessage(failure));
    } finally { if (generation.current === gen) setBusy(false); }
  };
  return <Card>
    <Body weight="700">Unresolved interpretations</Body>
    {busy && <ActivityIndicator />}
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
    {!!notice && <Small accessibilityLiveRegion="polite">{notice}</Small>}
    {!!conflicts && <Small accessibilityLiveRegion="polite">{conflictSummary(conflicts)}</Small>}
    {conflicts?.map((item) => <Pressable key={item.event_id} accessibilityRole="button"
      accessibilityState={{ selected: active === item.event_id }} disabled={busy}
      onPress={() => { setActive(item.event_id); setForm(emptyConflictForm()); setError(""); setNotice(""); }}>
      <Text style={styles.link}>{item.display_name} ({item.event_code}) · {item.status}{item.resolvable ? "" : " · no quoted text"}</Text>
    </Pressable>)}
    {!!selected && <View style={styles.form}>
      <Small>Competing protocol text for this visit:</Small>
      {selected.evidence.map((item) => {
        const checked = form.chosen.includes(item.evidence_id);
        return <Pressable key={item.evidence_id} accessibilityRole="checkbox" accessibilityState={{ checked }}
          disabled={busy || !mayResolve} onPress={() => setForm({ ...form, chosen: checked
            ? form.chosen.filter((id) => id !== item.evidence_id) : [...form.chosen, item.evidence_id] })}>
          <Text style={styles.link}>{checked ? "☑ " : "☐ "}{evidenceLocation(item)}</Text>
          <Small>{item.quote || "Source quote is missing; review the source before proceeding."}</Small>
        </Pressable>;
      })}
      {selected.missing_evidence_ids.length > 0 && <Small>{selected.missing_evidence_ids.length} linked source(s) could not be found.</Small>}
      {!mayResolve && <Small>You can read this evidence but resolving it needs the schedule.correct capability.</Small>}
      {mayResolve && <>
        <TextInput style={styles.input} accessibilityLabel="Confirmed reading" placeholder="The reading you are confirming"
          value={form.reading} editable={!busy} onChangeText={(reading) => setForm({ ...form, reading })} multiline />
        <TextInput style={styles.input} accessibilityLabel="Reason" placeholder="Why the chosen text supports it"
          value={form.reason} editable={!busy} onChangeText={(reason) => setForm({ ...form, reason })} multiline />
        <Button disabled={busy} onPress={() => void save()}>Confirm this reading</Button>
      </>}
    </View>}
    {!busy && <Button variant="secondary" onPress={() => setRetry((value) => value + 1)}>Reload interpretations</Button>}
  </Card>;
}
const styles = StyleSheet.create({
  form: { gap: 10, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 10 },
  link: { color: colors.primary, fontFamily: fonts.medium, paddingVertical: 8 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground },
  error: { color: colors.destructive },
});
