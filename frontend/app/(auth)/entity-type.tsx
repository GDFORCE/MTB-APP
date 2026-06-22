import React, { useState } from "react";
import { View, StyleSheet, ScrollView, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Check, ArrowLeft } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Button } from "@/src/components/ui";

const entities = [
  { id: "sponsor", label: "Sponsor", desc: "Owns and funds the clinical trial" },
  { id: "cro", label: "CRO", desc: "Runs the trial on a sponsor's behalf" },
  { id: "smo", label: "SMO", desc: "Manages a network of trial sites" },
  { id: "pi", label: "Principal Investigator", desc: "Site lead overseeing the trial" },
  { id: "crc", label: "Research Coordinator (CRC)", desc: "Site coordinator running visits" },
  { id: "patient", label: "Patient", desc: "Taking part in a clinical trial" },
];

export default function EntityType() {
  const router = useRouter();
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={{ padding: spacing.lg }}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={{ marginBottom: spacing.md }}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
        <Eyebrow color={colors.accent}>Step 1 of 5</Eyebrow>
        <H1 style={{ marginTop: 6 }}>I am joining as…</H1>
        <Small style={{ marginTop: 6 }}>Choose the role that best describes you. Your registration form adapts to it.</Small>
      </View>
      <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg }} showsVerticalScrollIndicator={false}>
        <View style={s.list}>
          {entities.map((e, i) => {
            const on = selected === e.id;
            return (
              <Pressable key={e.id} testID={`entity-${e.id}`} onPress={() => setSelected(e.id)} style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }, on && { backgroundColor: colors.secondary + "55" }]}>
                {on && <View style={s.spine} />}
                <Body color={on ? colors.accent : colors.mutedFg} style={{ width: 28, fontWeight: "700" as any, fontVariant: ["tabular-nums"] }}>{String(i + 1).padStart(2, "0")}</Body>
                <View style={{ flex: 1 }}>
                  <Body weight="700" color={on ? colors.primary : colors.foreground}>{e.label}</Body>
                  <Small>{e.desc}</Small>
                </View>
                <View style={[s.check, on ? { backgroundColor: colors.primary, borderColor: colors.primary } : { borderColor: colors.border, backgroundColor: colors.card }]}>
                  {on && <Check size={14} color={colors.primaryFg} strokeWidth={3} />}
                </View>
              </Pressable>
            );
          })}
        </View>
      </ScrollView>
      <View style={{ padding: spacing.lg }}>
        <Button testID="entity-continue-button" disabled={!selected} onPress={() => router.push({ pathname: "/(auth)/register", params: { role: selected || "" } })}>Continue</Button>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  list: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: "hidden" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.md, paddingVertical: 14, position: "relative" },
  spine: { position: "absolute", left: 0, top: 0, bottom: 0, width: 3, backgroundColor: colors.accent },
  check: { width: 24, height: 24, borderRadius: 999, borderWidth: 1, alignItems: "center", justifyContent: "center" },
});
