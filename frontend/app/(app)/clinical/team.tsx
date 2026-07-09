import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { UserPlus, Mail, Phone } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";

export default function Team() {
  const router = useRouter();
  const { user } = useAuth();
  const [members, setMembers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await api.get("/team");   // org- + trial-scoped, not the whole directory
        if (alive) setMembers(r.data);
      } catch {
        if (alive) setError("Couldn't load your team. Please try again.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={`${members.length + 1} members`} title="Team" right={
        <Pressable testID="invite-fab" onPress={() => router.push("/(app)/clinical/invite-patient")} style={s.fab}><UserPlus size={18} color={colors.primaryFg} /></Pressable>
      } />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        <Eyebrow style={{ marginBottom: spacing.sm }}>Your team</Eyebrow>
        {user && (
          <Card style={{ marginBottom: spacing.sm, borderColor: colors.accent + "55" }}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <View style={s.avatar}><Body weight="700" color={colors.primary}>{user.avatar_initials}</Body></View>
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Body weight="700">{user.full_name} <Small color={colors.accent} weight="700">(you)</Small></Body>
                <Small style={{ textTransform: "uppercase" as any, marginTop: 2 }} weight="700" color={colors.accent}>{user.role}</Small>
              </View>
            </View>
          </Card>
        )}
        {loading && (
          <View style={{ paddingVertical: spacing.lg, alignItems: "center" }}>
            <ActivityIndicator color={colors.primary} />
          </View>
        )}
        {!loading && error && (
          <Card style={{ marginBottom: spacing.sm }}><Small color={colors.destructive}>{error}</Small></Card>
        )}
        {!loading && !error && members.length === 0 && (
          <Card style={{ marginBottom: spacing.sm }}><Small>No teammates yet. Invite a colleague to get started.</Small></Card>
        )}
        {members.map(m => (
          <Pressable key={m.id} testID={`team-${m.id}`} onPress={() => router.push("/(app)/chat")}>
            <Card style={{ marginBottom: spacing.sm }}>
              <View style={{ flexDirection: "row", alignItems: "center" }}>
                <View style={s.avatar}><Body weight="700" color={colors.primary}>{m.avatar_initials}</Body></View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Body weight="700">{m.full_name}</Body>
                  <Small style={{ marginTop: 2, textTransform: "uppercase" as any }} weight="700" color={colors.mutedFg}>{m.role}</Small>
                  <Small style={{ marginTop: 2 }}>{m.organization || m.email}</Small>
                </View>
                {m.is_online && <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success }} />}
              </View>
            </Card>
          </Pressable>
        ))}
        <Button testID="invite-member" variant="secondary" style={{ marginTop: spacing.md }} onPress={() => router.push("/(app)/clinical/invite-patient")}>+ Invite member or patient</Button>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  fab: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" },
  avatar: { width: 48, height: 48, borderRadius: 24, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
});
