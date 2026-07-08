import React, { useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, ActivityIndicator } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Calendar as CalIcon, Clock, Building2, Stethoscope, Phone, CheckCircle } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

const fmtDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
const fmtTime = (d?: string) =>
  d ? new Date(d).toLocaleTimeString("en-GB", { hour: "numeric", minute: "2-digit", hour12: true }) : "";

export default function VisitDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [visit, setVisit] = useState<any | null>(null);
  const [protocol, setProtocol] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [v, t] = await Promise.all([api.get("/visits/mine"), api.get("/trials")]);
        const found = v.data.find((x: any) => x.id === id) || v.data[0] || null;
        setVisit(found);
        setProtocol(t.data.find((tr: any) => tr.id === found?.trial_id)?.protocol_id || "");
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) return (
    <ScreenContainer>
      <ScreenHeader eyebrow="My Trial" title="Visit Details" />
      <View style={{ paddingTop: spacing.xxl, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
    </ScreenContainer>
  );
  if (!visit) return (
    <ScreenContainer>
      <ScreenHeader eyebrow="My Trial" title="Visit Details" />
      <View style={{ padding: spacing.md }}>
        <Card style={{ alignItems: "center", paddingVertical: spacing.xl }}>
          <CalIcon size={28} color={colors.mutedFg} />
          <Body weight="700" style={{ marginTop: spacing.sm }}>Visit not found</Body>
          <Small style={{ marginTop: 2, textAlign: "center" }}>This visit is no longer available.</Small>
        </Card>
      </View>
    </ScreenContainer>
  );
  const done = visit.status === "completed";
  const eyebrow = protocol ? `My Trial · ${protocol}` : "My Trial";
  const site = visit.site || "";
  const pi = visit.pi_name || "";
  const siteLine = [site, pi].filter(Boolean).join(" · ");
  const checklist: string[] = Array.isArray(visit.checklist) ? visit.checklist : [];

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={eyebrow} title={`Visit ${visit.visit_number} Details`} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        {/* Hero */}
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Eyebrow color={colors.overlay25}>{protocol || "Trial"}</Eyebrow>
            <View style={s.chip}><Small weight="700" color={colors.primaryFg} style={{ textTransform: "capitalize" }}>{visit.status === "upcoming" ? "Upcoming" : done ? "Completed ✓" : "Scheduled"}</Small></View>
          </View>
          <H1 color={colors.primaryFg} style={{ marginTop: spacing.sm, fontSize: 20 }}>Visit {visit.visit_number} · {visit.name}</H1>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: spacing.sm }}>
            <View style={s.chipSm}><CalIcon size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{fmtDate(visit.scheduled_date)}</Small></View>
            <View style={s.chipSm}><Clock size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{fmtTime(visit.scheduled_date)}</Small></View>
            {!!site && <View style={s.chipSm}><Building2 size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{site}</Small></View>}
          </View>
          {!!siteLine && (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.sm }}>
              <Stethoscope size={14} color={colors.primaryFg} /><Small color={colors.primaryFg}>{siteLine}</Small>
            </View>
          )}
        </LinearGradient>

        {/* Before you come in */}
        {checklist.length > 0 && (
          <Card>
            <Eyebrow style={{ marginBottom: spacing.sm }}>Before you come in</Eyebrow>
            {checklist.map((it, i) => (
              <View key={i} style={{ flexDirection: "row", gap: 10, marginBottom: 8 }}>
                <View style={s.numCircle}><Small weight="700" color={colors.accent}>{i + 1}</Small></View>
                <Body style={{ flex: 1 }}>{it}</Body>
              </View>
            ))}
          </Card>
        )}

        {/* Clinical tasks */}
        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Clinical tasks</Eyebrow>
          {(visit.activities || ["Vital signs", "Blood draw", "ECG"]).map((t: string, i: number) => (
            <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <View style={s.checkbox} />
              <Small style={{ flex: 1 }}>{t}</Small>
            </View>
          ))}
          <Small color={colors.mutedFg} style={{ marginTop: 4 }}>Tasks are managed by the research team</Small>
        </Card>

        {done && (
          <View style={s.doneBanner}>
            <CheckCircle size={16} color={colors.success} />
            <Small color={colors.success} style={{ flex: 1 }}>Completed on {new Date(visit.scheduled_date).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}{pi ? ` · Confirmed by ${pi}` : ""}</Small>
          </View>
        )}

        <Button testID="contact-pi-button" onPress={() => router.push("/(app)/chat")}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Phone size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Contact PI</Small></View>
        </Button>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  chipSm: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  numCircle: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.accent + "22", alignItems: "center", justifyContent: "center" },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 2, borderColor: colors.border },
  doneBanner: { flexDirection: "row", alignItems: "center", gap: 8, padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.success + "40", backgroundColor: colors.success + "14" },
});
