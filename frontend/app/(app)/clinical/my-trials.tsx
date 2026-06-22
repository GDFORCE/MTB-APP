import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { FlaskConical, Users, ChevronRight, MapPin } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function MyTrials() {
  const router = useRouter();
  const [trials, setTrials] = useState<any[]>([]);
  const [patients, setPatients] = useState<any[]>([]);
  useEffect(() => { (async () => {
    const [t, p] = await Promise.all([api.get("/trials"), api.get("/patients").catch(() => ({ data: [] }))]);
    setTrials(t.data); setPatients(p.data);
  })(); }, []);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Clinical · Active studies" title="My Trials" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        {trials.map(t => {
          const count = patients.filter(p => p.trial_id === t.id).length;
          return (
            <Pressable key={t.id} testID={`trial-card-${t.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: t.id } })}>
              <View style={{ marginBottom: spacing.md }}>
                <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, padding: spacing.md }}>
                  <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 }}><Small color={colors.primaryFg} weight="700" style={{ fontFamily: "monospace" as any }}>{t.protocol_id}</Small></View>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 }}><Small color={colors.primaryFg} weight="700" style={{ textTransform: "capitalize" }}>{t.status}</Small></View>
                  </View>
                  <H1 color={colors.primaryFg} style={{ fontSize: 17, marginTop: spacing.sm }}>{t.title}</H1>
                </LinearGradient>
                <Card style={{ borderTopLeftRadius: 0, borderTopRightRadius: 0, borderTopWidth: 0, marginBottom: 0 }}>
                  <View style={{ flexDirection: "row", gap: spacing.md, marginBottom: spacing.sm }}>
                    <View style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Phase</Eyebrow><Body weight="700" style={{ marginTop: 2, fontSize: 13 }}>{t.phase}</Body></View>
                    <View style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Condition</Eyebrow><Body weight="700" style={{ marginTop: 2, fontSize: 13 }}>{t.condition}</Body></View>
                    <View style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Enrolled</Eyebrow><Body weight="700" color={colors.accent} style={{ marginTop: 2, fontSize: 13 }}>{count}</Body></View>
                  </View>
                  <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: spacing.sm, borderTopWidth: 1, borderColor: colors.border }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><MapPin size={14} color={colors.accent} /><Small>AIIMS Delhi</Small></View>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><Small weight="700" color={colors.accent}>View summary</Small><ChevronRight size={14} color={colors.accent} /></View>
                  </View>
                </Card>
              </View>
            </Pressable>
          );
        })}
      </ScrollView>
    </ScreenContainer>
  );
}
