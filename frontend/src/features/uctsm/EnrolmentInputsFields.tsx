import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Body, Small } from "@/src/components/ui";
import { colors, fonts, radii } from "@/src/theme/tokens";
import { getEnrolmentInputs } from "./api";
import { reviewFailureMessage } from "./manualRules.ts";
import { buildEnrolmentInputs, emptyEntry, enrolmentReady, KIND_HELP, type EntryValue, type RequiredInput } from "./requiredInputs.ts";

type Result = { inputs: Record<string, string>; deferred_inputs: string[] } | null;

/**
 * One field per detail the approved schedule requires. It reports the request fragment (or null while something
 * is missing or invalid) and says plainly when the list is not available, never rendering nothing silently.
 */
export function EnrolmentInputsFields({ trialId, definitionId, onChange }: {
  trialId: string; definitionId: string; onChange: (result: Result, ready: boolean) => void;
}) {
  const [entries, setEntries] = useState<RequiredInput[] | null>(null);
  const [values, setValues] = useState<Record<string, EntryValue>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true); setError(""); setEntries(null); setValues({});
    getEnrolmentInputs(trialId, definitionId).then((data) => {
      if (live) setEntries(data.enforced ? data.entries : []);
    }).catch((failure) => { if (live) setError(reviewFailureMessage(failure)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [trialId, definitionId]);
  useEffect(() => {
    if (!entries) { onChange(null, false); return; }
    let result: Result = null;
    try { result = buildEnrolmentInputs(entries, values); } catch { result = null; }
    onChange(result, enrolmentReady(entries, values));
  }, [entries, values]);
  const set = (code: string, patch: Partial<EntryValue>) =>
    setValues((current) => ({ ...current, [code]: { ...(current[code] ?? emptyEntry()), ...patch } }));
  if (loading) return <ActivityIndicator />;
  if (error) return <Text style={styles.error} accessibilityLiveRegion="polite">
    The list of required patient details could not be loaded. {error}</Text>;
  if (!entries || !entries.length) return <Small>This schedule asks for no extra patient details.</Small>;
  return <View style={styles.box}>
    <Body weight="700">Details this protocol needs</Body>
    {entries.map((entry) => {
      const state = values[entry.code] ?? emptyEntry();
      return <View key={entry.code} style={styles.field}>
        <Text style={styles.label}>{entry.label} *</Text>
        <Small>{entry.reason}</Small>
        {!state.later && <>
          <TextInput style={styles.input} accessibilityLabel={entry.label} value={state.value}
            placeholder={entry.kind === "DATE" || entry.kind === "DATETIME" ? "YYYY-MM-DD" : entry.kind === "NUMBER" ? "0" : "Text"}
            onChangeText={(value) => set(entry.code, { value })} />
          {entry.kind === "DATETIME" && <TextInput style={styles.input} accessibilityLabel={`${entry.label} time`}
            value={state.time} placeholder="HH:MM (24 hour, India time)" onChangeText={(time) => set(entry.code, { time })} />}
          <Small>{KIND_HELP[entry.kind]}</Small>
        </>}
        <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: state.later }}
          onPress={() => set(entry.code, { later: !state.later })}>
          <Text style={styles.link}>{state.later ? "☑ " : "☐ "}Will enter later (visits that depend on it will wait)</Text>
        </Pressable>
      </View>;
    })}
  </View>;
}
const styles = StyleSheet.create({
  box: { gap: 12 }, field: { gap: 6 },
  label: { fontFamily: fonts.medium, color: colors.foreground },
  link: { color: colors.primary, fontFamily: fonts.medium, paddingVertical: 6 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground },
  error: { color: colors.destructive },
});
