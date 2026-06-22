import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { Calendar as CalIcon, Check, AlertCircle } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

// CRC's daily schedule review — list of today's expected visits to approve / mark issues
export default function ScheduleReview() {
  const router = useRouter();
  const [patients, setPatients] = useState<any[]>([]);
  useEffect(() => { (async () => { const r = await api.get("/patients"); setPatients(r.data); })(); }, []);

  const today = patients.slice(0, 4).map((p, i) => ({ ...p, visit: ["Week 12", "Week 14 Follow-Up", "Baseline", "Screening"][i], time: ["9:30 AM", "10:15 AM", "11:00 AM", "2:30 PM"][i] }));

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Coordinator queue" title="Schedule Review" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        <Card>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
            <View>
              <Eyebrow color={colors.accent}>Today</Eyebrow>
              <Body weight="700" style={{ marginTop: 2, fontSize: 17 }}>{new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}</Body>
              <Small style={{ marginTop: 2 }}>{today.length} visits scheduled</Small>
            </View>
            <View style={s.bigIcon}><CalIcon size={26} color={colors.primary} /></View>
          </View>
        </Card>

        <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Awaiting review</Eyebrow>
        {today.map(v => (
          <Card key={v.id} style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <View style={s.avatar}><Body weight="700" color={colors.primary}>{v.avatar_initials}</Body></View>
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Body weight="700">{v.full_name}</Body>
                <Small style={{ marginTop: 2 }}>{v.visit} · {v.time}</Small>
              </View>
            </View>
            <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.sm, paddingTop: spacing.sm, borderTopWidth: 1, borderColor: colors.border }}>
              <Pressable testID={`approve-${v.id}`} style={[s.action, { backgroundColor: colors.success }]}><Check size={14} color={colors.successFg} /><Small color={colors.successFg} weight="700">Approve</Small></Pressable>
              <Pressable testID={`flag-${v.id}`} style={[s.action, { backgroundColor: colors.warning + "1A", borderWidth: 1, borderColor: colors.warning + "55" }]}><AlertCircle size={14} color={colors.warning} /><Small color={colors.warning} weight="700">Flag</Small></Pressable>
              <Pressable testID={`open-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.id } })} style={[s.action, { backgroundColor: colors.surface }]}><Small color={colors.primary} weight="700">Open</Small></Pressable>
            </View>
          </Card>
        ))}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  bigIcon: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  action: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 999 },
});
