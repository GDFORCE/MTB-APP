import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, Switch, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { User, Lock, Bell, FileText, HelpCircle, ChevronRight, LogOut, Pill, Info, Globe, Check } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { setLanguage } from "@/src/i18n";
import { useTranslation } from "react-i18next";

const LANGS = [{ id: "en", label: "English" }, { id: "hi", label: "हिंदी (Hindi)" }];

export default function Profile() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const { i18n } = useTranslation();
  const [prefs, setPrefs] = useState<any>({ notifications_email: true, notifications_push: true, notifications_sms: false, language: "en" });
  const [langOpen, setLangOpen] = useState(false);

  useEffect(() => { (async () => { try { const r = await api.get("/preferences"); setPrefs(r.data); if (r.data.language) i18n.changeLanguage(r.data.language); } catch {} })(); }, []);

  const togglePref = async (k: string) => {
    const next = { ...prefs, [k]: !prefs[k] }; setPrefs(next);
    await api.patch("/preferences", { [k]: next[k] });
  };

  const pickLang = async (lng: string) => {
    setPrefs((p: any) => ({ ...p, language: lng })); setLangOpen(false);
    await setLanguage(lng);
    await api.patch("/preferences", { language: lng });
  };

  if (!user) return null;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Account" title="Profile & Settings" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.avatar}><Body weight="700" color={colors.primary} style={{ fontSize: 20 }}>{user.avatar_initials}</Body></View>
            <View style={{ flex: 1 }}>
              <Body weight="700" style={{ fontSize: 17 }}>{user.full_name}</Body>
              <Small>{user.email}</Small>
              <Small color={colors.accent} style={{ marginTop: 2, textTransform: "capitalize" as any, fontWeight: "700" as any }}>{user.role}</Small>
            </View>
          </View>
        </Card>

        <Card padded={false}>
          <Row icon={Bell} label="Email notifications" right={<Switch testID="pref-email" value={!!prefs.notifications_email} onValueChange={() => togglePref("notifications_email")} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />} />
          <Row icon={Bell} label="Push notifications" right={<Switch testID="pref-push" value={!!prefs.notifications_push} onValueChange={() => togglePref("notifications_push")} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />} />
          <Row icon={Bell} label="SMS notifications" right={<Switch testID="pref-sms" value={!!prefs.notifications_sms} onValueChange={() => togglePref("notifications_sms")} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />} last />
        </Card>

        <Card padded={false}>
          <Pressable testID="profile-language" onPress={() => setLangOpen(o => !o)} style={s.row}>
            <View style={s.iconCircle}><Globe size={18} color={colors.primary} /></View>
            <View style={{ flex: 1 }}>
              <Body weight="500">Language</Body>
              <Small style={{ marginTop: 2 }}>{LANGS.find(l => l.id === prefs.language)?.label || "English"}</Small>
            </View>
            <ChevronRight size={18} color={colors.mutedFg} />
          </Pressable>
          {langOpen && LANGS.map(l => (
            <Pressable key={l.id} testID={`lang-${l.id}`} onPress={() => pickLang(l.id)} style={[s.row, { borderTopWidth: 1, borderTopColor: colors.border, paddingLeft: 64 }]}>
              <Body style={{ flex: 1 }}>{l.label}</Body>
              {prefs.language === l.id && <Check size={16} color={colors.primary} />}
            </Pressable>
          ))}
        </Card>

        <Card padded={false}>
          {user.role === "patient" && [
            { icon: Pill, label: "Medication reminder", onPress: () => router.push("/(app)/patient/medication-reminder") },
            { icon: Info, label: "About this trial", onPress: () => router.push("/(app)/patient/about-trial") },
          ].map((it, i) => (
            <Pressable key={i} testID={`profile-${it.label.toLowerCase().replace(/\s+/g, "-")}`} onPress={it.onPress} style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <View style={s.iconCircle}><it.icon size={18} color={colors.primary} /></View>
              <Body weight="500" style={{ flex: 1 }}>{it.label}</Body>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Pressable>
          ))}
          {[
            { icon: User, label: "Edit profile" },
            { icon: Lock, label: "Change password" },
            { icon: FileText, label: "Terms & conditions" },
            { icon: HelpCircle, label: "Help & support" },
          ].map((it, i) => (
            <Pressable key={`s${i}`} testID={`profile-${it.label.toLowerCase().replace(/\s+/g, "-")}`} style={[s.row, { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <View style={s.iconCircle}><it.icon size={18} color={colors.primary} /></View>
              <Body weight="500" style={{ flex: 1 }}>{it.label}</Body>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Pressable>
          ))}
        </Card>

        <Pressable testID="profile-logout" onPress={async () => { await signOut(); router.replace("/(auth)/welcome"); }}>
          <Card style={{ borderColor: colors.destructive + "33" }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <View style={[s.iconCircle, { backgroundColor: colors.destructive + "1A" }]}><LogOut size={18} color={colors.destructive} /></View>
              <Body weight="700" color={colors.destructive}>Log out</Body>
            </View>
          </Card>
        </Pressable>
      </ScrollView>
    </ScreenContainer>
  );
}

function Row({ icon: Icon, label, right, last }: any) {
  return (
    <View style={[s.row, !last && { borderBottomWidth: 1, borderBottomColor: colors.border }]}>
      <View style={s.iconCircle}><Icon size={18} color={colors.primary} /></View>
      <Body weight="500" style={{ flex: 1 }}>{label}</Body>
      {right}
    </View>
  );
}

const s = StyleSheet.create({
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.md, paddingVertical: 14 },
  iconCircle: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
});
