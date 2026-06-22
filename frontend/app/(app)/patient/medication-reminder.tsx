import React, { useState } from "react";
import { View, ScrollView, Pressable, Switch, StyleSheet } from "react-native";
import { Pill, Bell, Clock } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";

export default function MedicationReminder() {
  const [reminders, setReminders] = useState([
    { id: "1", med: "Metformin 500mg", time: "8:00 AM", enabled: true },
    { id: "2", med: "Aspirin 75mg", time: "2:00 PM", enabled: true },
    { id: "3", med: "Metformin 500mg", time: "8:00 PM", enabled: false },
  ]);
  const toggle = (id: string) => setReminders(prev => prev.map(r => r.id === id ? { ...r, enabled: !r.enabled } : r));

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Patient" title="Medication Reminder" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.icon}><Bell size={20} color={colors.primary} /></View>
            <View style={{ flex: 1 }}>
              <Body weight="700">Daily reminders</Body>
              <Small>Get notified for each dose</Small>
            </View>
          </View>
        </Card>

        <View>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Your reminders</Eyebrow>
          {reminders.map(r => (
            <Card key={r.id} style={{ marginBottom: spacing.sm }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <View style={s.icon}><Pill size={18} color={colors.primary} /></View>
                <View style={{ flex: 1 }}>
                  <Body weight="700">{r.med}</Body>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 4, marginTop: 2 }}>
                    <Clock size={11} color={colors.mutedFg} />
                    <Small>{r.time}</Small>
                  </View>
                </View>
                <Switch testID={`reminder-toggle-${r.id}`} value={r.enabled} onValueChange={() => toggle(r.id)} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />
              </View>
            </Card>
          ))}
        </View>

        <Button testID="add-reminder-button" variant="secondary">+ Add reminder</Button>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({ icon: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" } });
