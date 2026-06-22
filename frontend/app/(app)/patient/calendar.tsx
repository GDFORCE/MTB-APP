import React from "react";
import { View, StyleSheet, ScrollView, Pressable } from "react-native";
import { Calendar as CalIcon, Plus } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";

// Calendar placeholder per user request: structure & buttons present, calendar
// rendering is intentionally left for the user to wire up themselves.
export default function PatientCalendar() {
  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Your schedule" title="Calendar" right={<Pressable testID="cal-add" hitSlop={12} style={s.headerBtn}><Plus size={18} color={colors.primaryFg} /></Pressable>} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <View style={s.tabs}>
          {["Day", "Week", "Month"].map((t, i) => (
            <Pressable key={t} testID={`cal-view-${t.toLowerCase()}`} style={[s.tab, i === 2 && s.tabActive]}>
              <Small weight="700" color={i === 2 ? colors.foreground : colors.mutedFg}>{t}</Small>
            </Pressable>
          ))}
        </View>

        <Card style={{ alignItems: "center", paddingVertical: 36 }}>
          <View style={s.calIcon}><CalIcon size={32} color={colors.primary} /></View>
          <H1 style={{ marginTop: spacing.md, fontSize: 18 }}>Calendar coming soon</H1>
          <Small style={{ marginTop: 6, textAlign: "center" }}>Day, Week & Month grid will be wired up here.{"\n"}You'll plug in your own implementation.</Small>
        </Card>

        <View>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Quick actions</Eyebrow>
          <Button testID="cal-add-event" variant="secondary">+ Add visit / event</Button>
          <View style={{ height: 8 }} />
          <Button testID="cal-settings" variant="secondary">Calendar settings</Button>
          <View style={{ height: 8 }} />
          <Button testID="cal-share" variant="secondary">Share schedule</Button>
        </View>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  headerBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" },
  tabs: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: 999, padding: 4 },
  tab: { flex: 1, paddingVertical: 10, alignItems: "center", borderRadius: 999 },
  tabActive: { backgroundColor: colors.card },
  calIcon: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
});
