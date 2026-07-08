import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, Linking, ActivityIndicator } from "react-native";
import { Phone, Mail } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function AboutTrial() {
  const [trial, setTrial] = useState<any | null>(null);
  const [care, setCare] = useState<any>({ site: "", pi_name: "", pi_phone: "", pi_email: "" });
  const [visitCount, setVisitCount] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [t, v] = await Promise.all([api.get("/trials"), api.get("/visits/mine")]);
        const visits: any[] = v.data || [];
        // Use the ENROLLED trial (the one this patient's visits belong to),
        // not simply trials[0]. Fall back to the first listed trial.
        const enrolledId = visits[0]?.trial_id;
        const enrolled = t.data.find((tr: any) => tr.id === enrolledId) || t.data[0] || null;
        setTrial(enrolled);
        const mine = enrolledId ? visits.filter(x => x.trial_id === enrolledId) : visits;
        setVisitCount(mine.length);
        if (mine[0]) {
          setCare({
            site: mine[0].site || "",
            pi_name: mine[0].pi_name || "",
            pi_phone: mine[0].pi_phone || "",
            pi_email: mine[0].pi_email || "",
          });
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="About this study" title={trial?.protocol_id || "Trial"} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        {loading ? (
          <View style={{ paddingTop: spacing.xxl, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
        ) : !trial ? (
          <Card style={{ alignItems: "center", paddingVertical: spacing.xl }}>
            <Body weight="700">No study found</Body>
            <Small style={{ marginTop: 2, textAlign: "center" }}>You are not enrolled in any study yet.</Small>
          </Card>
        ) : (
        <>
        <Card>
          <Eyebrow style={{ marginBottom: 6 }}>Study title</Eyebrow>
          <Body weight="700">{trial?.title}</Body>
        </Card>
        <Card>
          <Eyebrow style={{ marginBottom: 6 }}>Overview</Eyebrow>
          <Body>{trial?.description || "A randomized, double-blind study."}</Body>
        </Card>
        <View style={{ flexDirection: "row", gap: spacing.sm }}>
          <Card style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Phase</Eyebrow><Body weight="700" style={{ marginTop: 4 }}>{trial?.phase}</Body></Card>
          <Card style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Condition</Eyebrow><Body weight="700" style={{ marginTop: 4 }}>{trial?.condition}</Body></Card>
        </View>
        <Card>
          <Eyebrow style={{ marginBottom: 6 }}>Sponsor</Eyebrow>
          <Body weight="700">{trial?.sponsor_name || "Sponsor"}</Body>
        </Card>
        <Card>
          <Eyebrow style={{ marginBottom: 6 }}>Principal Investigator</Eyebrow>
          <Body weight="700">{care.pi_name || "Assigned on enrollment"}</Body>
          {!!care.site && <Small style={{ marginTop: 2 }}>{care.site}</Small>}
          {(!!care.pi_phone || !!care.pi_email) && (
            <View style={{ flexDirection: "row", gap: spacing.sm, marginTop: spacing.md }}>
              {!!care.pi_phone && (
                <Pressable testID="pi-call" onPress={() => Linking.openURL(`tel:${care.pi_phone}`)} style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 8, paddingHorizontal: 12, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border }}>
                  <Phone size={14} color={colors.accent} /><Small weight="700" color={colors.accent}>Call</Small>
                </Pressable>
              )}
              {!!care.pi_email && (
                <Pressable testID="pi-email" onPress={() => Linking.openURL(`mailto:${care.pi_email}`)} style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 8, paddingHorizontal: 12, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border }}>
                  <Mail size={14} color={colors.accent} /><Small weight="700" color={colors.accent}>Email</Small>
                </Pressable>
              )}
            </View>
          )}
        </Card>
        <Card>
          <Eyebrow style={{ marginBottom: 8 }}>What to expect</Eyebrow>
          {[`${visitCount || "Multiple"} scheduled study visits`, "Blood draws & vitals at study visits", "Daily medication tracking", "24/7 access to study team via chat"].map((t, i) => (
            <View key={i} style={{ flexDirection: "row", gap: 8, marginBottom: 6 }}>
              <Small color={colors.accent} weight="700">·</Small>
              <Body style={{ flex: 1 }}>{t}</Body>
            </View>
          ))}
        </Card>
        </>
        )}
      </ScrollView>
    </ScreenContainer>
  );
}
