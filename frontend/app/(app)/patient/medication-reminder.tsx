import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, Switch, TextInput, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { Pill, Bell, Clock, Trash2, X } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function MedicationReminder() {
  const [reminders, setReminders] = useState<any[]>([]);
  const [adding, setAdding] = useState(false);
  const [med, setMed] = useState(""), [dose, setDose] = useState(""), [time, setTime] = useState("8:00 AM");

  const load = async () => { try { const r = await api.get("/reminders"); setReminders(r.data); } catch {} };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!med || !time) return;
    await api.post("/reminders", { medication: med, dosage: dose, time, enabled: true });
    setMed(""); setDose(""); setTime("8:00 AM"); setAdding(false); load();
  };
  const toggle = async (r: any) => { await api.patch(`/reminders/${r.id}`, { enabled: !r.enabled }); load(); };
  const del = async (id: string) => { await api.delete(`/reminders/${id}`); load(); };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Patient" title="Medication Reminder" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }} keyboardShouldPersistTaps="handled">
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
            {reminders.length === 0 && !adding ? <Card><Small>No reminders yet. Tap "Add reminder" below.</Small></Card> :
              reminders.map(r => (
                <Card key={r.id} style={{ marginBottom: spacing.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={s.icon}><Pill size={18} color={colors.primary} /></View>
                    <View style={{ flex: 1 }}>
                      <Body weight="700">{r.medication} {r.dosage}</Body>
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 4, marginTop: 2 }}>
                        <Clock size={11} color={colors.mutedFg} />
                        <Small>{r.time}</Small>
                      </View>
                    </View>
                    <Switch testID={`reminder-toggle-${r.id}`} value={r.enabled} onValueChange={() => toggle(r)} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />
                    <Pressable testID={`reminder-delete-${r.id}`} onPress={() => del(r.id)} hitSlop={8} style={{ marginLeft: 4 }}><Trash2 size={16} color={colors.destructive} /></Pressable>
                  </View>
                </Card>
              ))
            }
          </View>

          {adding ? (
            <Card>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.sm }}>
                <Body weight="700">New reminder</Body>
                <Pressable onPress={() => setAdding(false)} hitSlop={8}><X size={18} color={colors.mutedFg} /></Pressable>
              </View>
              <Small color={colors.foreground} style={{ marginBottom: 4, fontWeight: "600" as any }}>Medication</Small>
              <TextInput testID="reminder-med" value={med} onChangeText={setMed} style={s.input} />
              <Small color={colors.foreground} style={{ marginTop: 8, marginBottom: 4, fontWeight: "600" as any }}>Dosage</Small>
              <TextInput testID="reminder-dose" value={dose} onChangeText={setDose} placeholder="e.g. 500mg" placeholderTextColor={colors.mutedFg} style={s.input} />
              <Small color={colors.foreground} style={{ marginTop: 8, marginBottom: 4, fontWeight: "600" as any }}>Time</Small>
              <TextInput testID="reminder-time" value={time} onChangeText={setTime} placeholder="8:00 AM" placeholderTextColor={colors.mutedFg} style={s.input} />
              <Button testID="reminder-save" style={{ marginTop: spacing.md }} onPress={create}>Save reminder</Button>
            </Card>
          ) : (
            <Button testID="add-reminder-button" variant="secondary" onPress={() => setAdding(true)}>+ Add reminder</Button>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}
const s = StyleSheet.create({
  icon: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, color: colors.foreground },
});
