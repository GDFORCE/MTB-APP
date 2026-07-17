import React, { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import {
  BarChart2,
  Bell,
  Building2,
  ChevronRight,
  FileText,
  FlaskConical,
  HelpCircle,
  Lock,
  LogOut,
  MapPin,
  KeyRound,
  ShieldCheck,
  UserPen,
  Users,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { consoleRouteForType } from "@/src/components/org-admin-kit";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { colors, dawnGradient, fonts, shadows } from "@/src/theme/tokens";

type Organization = { type?: string; address?: string };

export default function SponsorProfile() {
  const router = useRouter();
  const { user, signOut } = useAuth();
  const [organization, setOrganization] = useState<Organization>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user?.organization) { setLoading(false); return; }
    api.get("/organizations", { params: { search: user.organization } })
      .then((response) => {
        const list = Array.isArray(response.data) ? response.data : [];
        const match = list.find((item: any) => item.name === user.organization) || list[0];
        if (match) setOrganization(match);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user?.organization]);

  if (!user) return null;
  const entityLabel = user.role === "cro" ? "CRO" : "Sponsor";
  const designation = user.role === "cro" ? "Clinical Research Organization" : "Sponsor Representative";
  const initials = user.avatar_initials || user.full_name.split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase();

  const sections = [
    {
      title: "ACCOUNT",
      rows: [
        { icon: UserPen, label: "Edit Profile", route: "/(app)/clinical/profile/edit", tone: colors.info },
        { icon: Building2, label: "Entity Change", route: "/(app)/clinical/profile/entity-change", tone: colors.accent },
        { icon: Lock, label: "Change Password", route: "/(app)/clinical/profile/change-password", tone: colors.violet },
        { icon: Bell, label: "Notification Preferences", route: "/(app)/clinical/profile/notifications", tone: colors.warning },
      ],
    },
    {
      title: "TRIAL MANAGEMENT",
      rows: [
        { icon: FlaskConical, label: "My Trials", route: "/(app)/sponsor/trials", tone: colors.info },
        { icon: MapPin, label: "My Sites", route: "/(app)/sponsor/sites", tone: colors.accent },
        { icon: Users, label: "Team Members", route: "/(app)/clinical/team", tone: colors.success },
        ...(user.org_admin ? [{ icon: KeyRound, label: "Trial Access Requests", route: "/(app)/org-admin/trial-access-requests", tone: colors.warning }] : []),
        ...(user.org_admin ? [{ icon: ShieldCheck, label: "Organization Oversight", route: consoleRouteForType(organization.type), tone: colors.primary }] : []),
      ],
    },
    {
      title: "REPORTS & SUPPORT",
      rows: [
        { icon: BarChart2, label: "Reports", route: "/(app)/clinical/profile/reports", tone: colors.violet },
        { icon: FileText, label: "Terms & Conditions", route: "/(app)/clinical/profile/tnc", tone: colors.info },
        { icon: HelpCircle, label: "Help & Support", route: "/(app)/clinical/profile/help", tone: colors.accent },
      ],
    },
  ];

  const logout = async () => {
    await signOut();
    router.replace("/(auth)/welcome" as never);
  };

  return (
    <View style={s.page}>
      <SafeAreaView edges={["top"]} style={s.safe} />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        <LinearGradient colors={dawnGradient as any} style={s.hero}>
          <View style={s.glow} />
          <View style={s.avatar}><Text style={s.avatarText}>{initials}</Text></View>
          <View style={{ flex: 1 }}>
            <Text style={s.name}>{user.full_name}</Text>
            <View style={s.designation}><ShieldCheck size={12} color={colors.white} /><Text style={s.designationText}>{designation}</Text></View>
            <Text style={s.orgName}>{user.organization || "Organization not linked"}</Text>
          </View>
        </LinearGradient>

        <View style={s.details}>
          <Text style={s.eyebrow}>ACCOUNT DETAILS</Text>
          {loading ? <ActivityIndicator style={{ margin: 16 }} color={colors.primary} /> : [
            { label: "Phone Number", value: user.phone || "—", verified: true },
            { label: "Email ID", value: user.email, verified: true },
            { label: "Entity Type", value: entityLabel },
            { label: "Organization", value: user.organization || "—" },
            { label: "Organization Address", value: organization.address || "—" },
            { label: "Access", value: user.org_admin ? "Organization Admin" : entityLabel },
          ].map((row, index) => (
            <View key={row.label} style={[s.detailRow, index > 0 && s.detailBorder]}>
              <View style={s.detailLabelRow}>
                <Text style={s.detailLabel}>{row.label}</Text>
                {row.verified && <ShieldCheck size={11} color={colors.success} />}
              </View>
              <Text style={s.detailValue}>{row.value}</Text>
            </View>
          ))}
        </View>

        {sections.map((section) => (
          <View key={section.title}>
            <Text style={s.sectionTitle}>{section.title}</Text>
            <View style={s.menu}>
              {section.rows.map((row, index) => {
                const Icon = row.icon;
                return (
                  <Pressable key={row.label} onPress={() => router.push(row.route as never)} style={[s.menuRow, index > 0 && s.menuBorder]}>
                    <View style={[s.menuIcon, { backgroundColor: `${row.tone}18` }]}><Icon size={17} color={row.tone} /></View>
                    <Text style={s.menuLabel}>{row.label}</Text>
                    <ChevronRight size={17} color={colors.border} />
                  </Pressable>
                );
              })}
            </View>
          </View>
        ))}

        <Pressable onPress={logout} style={s.logout}><LogOut size={17} color={colors.destructive} /><Text style={s.logoutText}>Sign Out</Text></Pressable>
      </ScrollView>
      <SponsorBottomNav active="me" />
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  safe: { backgroundColor: colors.background },
  content: { padding: 15, paddingBottom: 28, gap: 15 },
  hero: { minHeight: 132, padding: 17, overflow: "hidden", flexDirection: "row", alignItems: "center", gap: 14, borderRadius: 25, ...shadows.md },
  glow: { position: "absolute", right: -38, top: -42, width: 130, height: 130, borderRadius: 65, backgroundColor: "rgba(255,255,255,0.16)" },
  avatar: { width: 69, height: 69, borderRadius: 35, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: "rgba(255,255,255,0.32)", backgroundColor: colors.primaryDeep },
  avatarText: { fontFamily: fonts.bold, fontSize: 20, color: colors.white },
  name: { fontFamily: fonts.heading, fontSize: 19, color: colors.white },
  designation: { alignSelf: "flex-start", marginTop: 7, paddingHorizontal: 8, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 4, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.16)" },
  designationText: { fontFamily: fonts.semibold, fontSize: 9.5, color: colors.white },
  orgName: { marginTop: 6, fontFamily: fonts.regular, fontSize: 10.5, color: "rgba(255,255,255,0.76)" },
  details: { padding: 15, borderRadius: 21, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  eyebrow: { marginBottom: 3, fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.primary },
  detailRow: { paddingVertical: 9 },
  detailBorder: { borderTopWidth: 1, borderTopColor: "rgba(230,214,197,0.62)" },
  detailLabelRow: { flexDirection: "row", alignItems: "center", gap: 4 },
  detailLabel: { fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  detailValue: { marginTop: 2, fontFamily: fonts.medium, fontSize: 12.5, color: colors.foreground },
  sectionTitle: { marginBottom: 7, paddingHorizontal: 4, fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.mutedFg },
  menu: { overflow: "hidden", borderRadius: 20, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  menuRow: { minHeight: 53, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", gap: 10 },
  menuBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  menuIcon: { width: 34, height: 34, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  menuLabel: { flex: 1, fontFamily: fonts.medium, fontSize: 12.5, color: colors.foreground },
  logout: { minHeight: 49, paddingHorizontal: 15, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 19, borderWidth: 1, borderColor: "rgba(192,57,43,0.20)", backgroundColor: colors.card },
  logoutText: { fontFamily: fonts.semibold, fontSize: 12.5, color: colors.destructive },
});
