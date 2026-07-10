import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text, Image, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  Camera, ShieldCheck, UserPen, Lock, Bell, ChevronRight, LogOut,
  Building2, FlaskConical, FileText, HelpCircle, BarChart2, Users, ScrollText,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import * as ImagePicker from "expo-image-picker";
import { uploadFile, fetchFileUri } from "@/src/lib/upload";

const ORG_TYPE_LABEL: Record<string, string> = { sponsor: "Sponsor", cro: "CRO", smo: "SMO", site: "Site / Hospital" };

const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8", secondary: "#F0D7DC",
  accent: "#E69B5C", info: "#7B6BB8", violet: "#8E5BB4", warning: "#D89A3C", success: "#5C9A6E", destructive: "#C0392B",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;

export default function SiteUserProfile() {
  const router = useRouter();
  const { user, signOut, refresh } = useAuth();

  // Avatar (uploaded profile photo). `avatarUri` is a render-ready object URL /
  // data URI fetched through the authed api client; null → fall back to initials.
  const [avatarUri, setAvatarUri] = useState<string | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarErr, setAvatarErr] = useState("");
  useEffect(() => {
    (async () => {
      try {
        const me = (await api.get("/auth/me")).data;
        if (me.avatar_file_id) setAvatarUri(await fetchFileUri(me.avatar_file_id));
      } catch {}
    })();
  }, []);

  const pickAvatar = async () => {
    if (avatarBusy) return;
    setAvatarErr("");
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) { setAvatarErr("Photo access is needed to change your picture."); return; }
      const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], allowsEditing: true, aspect: [1, 1], quality: 0.8 });
      if (res.canceled || !res.assets?.length) return;
      const a = res.assets[0];
      const name = a.fileName || `avatar.${(a.uri.split(".").pop() || "jpg").split("?")[0]}`;
      setAvatarBusy(true);
      const uploaded = await uploadFile(
        { uri: a.uri, name, mimeType: a.mimeType || "image/jpeg", file: (a as any).file },
        { scopeType: "user" },
      );
      await api.patch("/auth/me", { avatar_file_id: uploaded.id });
      await refresh();
      setAvatarUri(await fetchFileUri(uploaded.id));
    } catch (e: any) {
      setAvatarErr(e?.response?.data?.detail || "Couldn't update your photo. Please try again.");
    } finally {
      setAvatarBusy(false);
    }
  };

  // Real entity data: org name comes from the user record; address + entity type
  // are resolved from the organizations directory (falls back gracefully).
  const [org, setOrg] = useState<{ address: string; type: string }>({ address: "", type: "Site / Hospital" });
  useEffect(() => {
    if (!user?.organization) return;
    (async () => {
      try {
        const r = await api.get("/organizations", { params: { search: user.organization } });
        const match = (r.data || []).find((o: any) => o.name === user.organization) || (r.data || [])[0];
        if (match) setOrg({ address: match.address || "", type: ORG_TYPE_LABEL[match.type] || "Site / Hospital" });
      } catch {}
    })();
  }, [user?.organization]);

  if (!user) return null;
  const isPi = user.role === "pi";
  const designation = isPi ? "Principal Investigator" : "Clinical Research Coordinator";
  const entityType = org.type;
  const orgName = user.organization || "—";
  const orgAddress = org.address || "—";
  const role = isPi ? "PI" : "Research Team";

  const account = [
    { icon: UserPen, label: "Edit Profile", onPress: () => router.push("/(app)/clinical/profile/edit"), bg: "rgba(123,107,184,0.10)", ic: C.info },
    { icon: Building2, label: "Entity Change", onPress: () => router.push("/(app)/clinical/profile/entity-change"), bg: "rgba(230,155,92,0.12)", ic: C.accent },
    { icon: Lock, label: "Change Password", onPress: () => router.push("/(app)/clinical/profile/change-password"), bg: "rgba(142,91,180,0.10)", ic: C.violet },
    { icon: Bell, label: "Notification Preferences", onPress: () => router.push("/(app)/clinical/profile/notifications"), bg: "rgba(216,154,60,0.15)", ic: C.warning },
  ];
  const trialMgmt = [
    { icon: FlaskConical, label: "My Trials", onPress: () => router.push("/(app)/clinical/my-trials"), bg: "rgba(123,107,184,0.10)", ic: C.info },
    { icon: Users, label: "Team Members", onPress: () => router.push("/(app)/clinical/team"), bg: "rgba(92,154,110,0.15)", ic: C.success },
  ];
  const reports = [
    { icon: ScrollText, label: "Audit Trail", onPress: () => router.push("/(app)/audit-trail" as any), bg: "rgba(166,33,63,0.10)", ic: C.primary },
    { icon: BarChart2, label: "Reports", onPress: () => router.push("/(app)/clinical/profile/reports"), bg: "rgba(142,91,180,0.10)", ic: C.violet },
    { icon: FileText, label: "T&C", onPress: () => router.push("/(app)/clinical/profile/tnc"), bg: "rgba(123,107,184,0.10)", ic: C.info },
    { icon: HelpCircle, label: "Help & Support", onPress: () => router.push("/(app)/clinical/profile/help"), bg: "rgba(230,155,92,0.12)", ic: C.accent },
  ];

  const detailRows = [
    { label: "Phone Number", val: user.phone || "—", verify: true },
    { label: "Email ID", val: user.email, verify: true },
    { label: "Entity Type", val: entityType },
    { label: "Org. Name", val: orgName },
    { label: "Org. Address", val: orgAddress },
    { label: "Role", val: role },
  ];

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="dark-content" backgroundColor={C.bg} />
      <SafeAreaView edges={["top"]} style={{ backgroundColor: C.bg }} />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 32 }} showsVerticalScrollIndicator={false}>

        {/* ── Identity hero (dawn gradient card with avatar + camera + designation pill) ── */}
        <View style={{ paddingHorizontal: 16, paddingTop: 16 }}>
          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={pp.hero}>
            {/* Subtle inner glow */}
            <View pointerEvents="none" style={{ position: "absolute", top: -40, right: -40, width: 120, height: 120, borderRadius: 60, backgroundColor: "rgba(255,255,255,0.18)" }} />
            <View style={{ flexDirection: "row", alignItems: "center", gap: 16 }}>
              <View>
                {avatarUri ? (
                  <Image source={{ uri: avatarUri }} style={pp.avatar} />
                ) : (
                  <View style={pp.avatar}>
                    <Text style={pp.avatarText}>{user.avatar_initials || "MC"}</Text>
                  </View>
                )}
                <Pressable testID="avatar-camera" onPress={pickAvatar} disabled={avatarBusy} style={pp.cameraBtn}>
                  {avatarBusy ? <ActivityIndicator size="small" color={C.primary} /> : <Camera size={14} color={C.primary} />}
                </Pressable>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={pp.heroName}>{user.full_name}</Text>
                <View style={pp.designationPill}>
                  <ShieldCheck size={12} color={C.primaryFg} />
                  <Text style={pp.designationText}>{designation}</Text>
                </View>
              </View>
            </View>
          </LinearGradient>
          {avatarErr ? <Text style={{ color: C.destructive, fontSize: 12, marginTop: 8, paddingHorizontal: 4 }}>{avatarErr}</Text> : null}
        </View>

        {/* ── Account details card ── */}
        <View style={{ paddingHorizontal: 16, marginTop: 16 }}>
          <View style={pp.card}>
            <Text style={pp.eyebrow}>ACCOUNT DETAILS</Text>
            <View>
              {detailRows.map((r, i) => (
                <View key={r.label} style={[{ paddingVertical: 10 }, i > 0 && { borderTopWidth: 1, borderTopColor: "rgba(230,214,197,0.60)" }]}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                    <Text style={{ color: C.muted, fontSize: 12 }}>{r.label}</Text>
                    {r.verify && <ShieldCheck size={12} color={C.success} />}
                  </View>
                  <Text style={{ color: C.fg, fontSize: 14, fontWeight: "500", marginTop: 2 }}>{r.val}</Text>
                </View>
              ))}
            </View>
          </View>
        </View>

        {/* Grouped menus */}
        {[
          { title: "ACCOUNT", rows: account },
          { title: "TRIAL MANAGEMENT", rows: trialMgmt },
          { title: "REPORTS & SUPPORT", rows: reports },
        ].map(group => (
          <View key={group.title} style={{ paddingHorizontal: 16, marginTop: 16 }}>
            <Text style={[pp.eyebrow, { marginBottom: 8, paddingHorizontal: 4 }]}>{group.title}</Text>
            <View style={pp.menuCard}>
              {group.rows.map((r, i) => (
                <Pressable
                  key={r.label}
                  testID={`menu-${r.label.toLowerCase().replace(/\s+/g, "-").replace(/&/g, "and")}`}
                  onPress={r.onPress}
                  style={[pp.menuRow, i > 0 && { borderTopWidth: 1, borderTopColor: C.border }]}
                >
                  <View style={[pp.menuIcon, { backgroundColor: r.bg }]}>
                    <r.icon size={20} color={r.ic} />
                  </View>
                  <Text style={pp.menuLabel}>{r.label}</Text>
                  <ChevronRight size={16} color="rgba(123,95,115,0.40)" />
                </Pressable>
              ))}
            </View>
          </View>
        ))}

        {/* Sign Out — destructive bordered card */}
        <View style={{ paddingHorizontal: 16, marginTop: 20 }}>
          <Pressable
            testID="sign-out"
            onPress={async () => { await signOut(); router.replace("/(auth)/welcome"); }}
            style={pp.signOut}
          >
            <LogOut size={16} color={C.destructive} />
            <Text style={{ color: C.destructive, fontSize: 14, fontWeight: "700" }}>Sign Out</Text>
          </Pressable>
        </View>
      </ScrollView>
    </View>
  );
}

const pp = StyleSheet.create({
  hero: { borderRadius: 24, padding: 20, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.12, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 4 },
  avatar: { width: 64, height: 64, borderRadius: 32, backgroundColor: "rgba(255,255,255,0.20)", alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: "rgba(255,255,255,0.30)" },
  avatarText: { color: C.primaryFg, fontSize: 24, fontWeight: "700" },
  cameraBtn: { position: "absolute", bottom: -4, right: -4, width: 28, height: 28, borderRadius: 14, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center", shadowColor: "#2E1B33", shadowOpacity: 0.15, shadowRadius: 4, shadowOffset: { width: 0, height: 2 }, elevation: 3 },
  heroName: { color: C.primaryFg, fontSize: 20, fontWeight: "700", letterSpacing: -0.2 },
  designationPill: { alignSelf: "flex-start", marginTop: 6, flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.20)" },
  designationText: { color: C.primaryFg, fontSize: 12, fontWeight: "600" },
  card: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 16, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  eyebrow: { color: C.muted, fontSize: 11, fontWeight: "700", letterSpacing: 1.5, marginBottom: 4 },
  menuCard: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 14 },
  menuIcon: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  menuLabel: { flex: 1, color: C.fg, fontSize: 15 },
  signOut: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: 14, borderWidth: 1, borderColor: "rgba(192,57,43,0.25)", backgroundColor: C.card },
});
