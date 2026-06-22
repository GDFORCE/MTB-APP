import React, { useEffect, useState } from "react";
import { View, ScrollView } from "react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function AboutTrial() {
  const [trial, setTrial] = useState<any | null>(null);
  useEffect(() => { (async () => { const r = await api.get("/trials"); setTrial(r.data[0]); })(); }, []);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="About this study" title={trial?.protocol_id || "Trial"} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
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
          <Body weight="700">{trial?.sponsor_name || "Pfizer Global"}</Body>
        </Card>
        <Card>
          <Eyebrow style={{ marginBottom: 6 }}>Principal Investigator</Eyebrow>
          <Body weight="700">Dr. Rajesh Sharma</Body>
          <Small style={{ marginTop: 2 }}>AIIMS Delhi</Small>
        </Card>
        <Card>
          <Eyebrow style={{ marginBottom: 8 }}>What to expect</Eyebrow>
          {["10 in-person visits over 24 weeks", "Weekly blood draws & vitals", "Daily medication tracking", "24/7 access to study team via chat"].map((t, i) => (
            <View key={i} style={{ flexDirection: "row", gap: 8, marginBottom: 6 }}>
              <Small color={colors.accent} weight="700">·</Small>
              <Body style={{ flex: 1 }}>{t}</Body>
            </View>
          ))}
        </Card>
      </ScrollView>
    </ScreenContainer>
  );
}
