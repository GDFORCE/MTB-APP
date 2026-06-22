import React from "react";
import { View, Pressable, StyleSheet, Text, StatusBar } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { ChevronLeft, Wrench } from "lucide-react-native";

const C = { primaryDeep: "#6B1437", primaryFg: "#FBF2E8", bg: "#FBF2E8", card: "#FEFAF1", border: "#E6D6C5", fg: "#2E1B33", muted: "#7B5F73", accent: "#E69B5C" };

const TITLES: Record<string, { eye: string; title: string }> = {
  "edit": { eye: "Profile", title: "Edit Profile" },
  "entity-change": { eye: "Profile", title: "Entity Change" },
  "change-password": { eye: "Profile", title: "Change Password" },
  "notifications": { eye: "Profile", title: "Notification Preferences" },
  "reports": { eye: "Reports & Support", title: "Reports" },
  "tnc": { eye: "Reports & Support", title: "Terms & Conditions" },
  "help": { eye: "Reports & Support", title: "Help & Support" },
};

export default function ProfileSubScreenPlaceholder() {
  const router = useRouter();
  const params = useLocalSearchParams<{ slug?: string }>();
  // expo-router will route /(app)/clinical/profile/edit etc here based on filename.
  // For dynamic slug fallback, we read it from the URL path via window/location if needed.
  const t = TITLES[String(params.slug || "edit")] || { eye: "Profile", title: "Coming soon" };

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <View style={{ backgroundColor: C.primaryDeep, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 16 }}>
        <SafeAreaView edges={["top"]}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <Pressable testID="profile-back" onPress={() => router.back()} hitSlop={10} style={s.backBtn}><ChevronLeft size={20} color={C.primaryFg} /></Pressable>
            <View style={{ flex: 1 }}>
              <Text style={s.eyebrow}>{t.eye.toUpperCase()}</Text>
              <Text style={s.title}>{t.title}</Text>
            </View>
          </View>
        </SafeAreaView>
      </View>
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <View style={s.iconBox}><Wrench size={28} color={C.accent} /></View>
        <Text style={s.h2}>Pixel-perfect rebuild coming next iteration</Text>
        <Text style={s.body}>This sub-section is part of the Site User Profile flow. The main "Me" page is complete; this deep view will match your reference 1:1 in the next iteration.</Text>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  backBtn: { width: 32, height: 32, alignItems: "center", justifyContent: "center", borderRadius: 16 },
  eyebrow: { color: "rgba(251,242,232,0.55)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  title: { color: C.primaryFg, fontSize: 18, fontWeight: "700" },
  iconBox: { width: 72, height: 72, borderRadius: 36, backgroundColor: "rgba(230,155,92,0.15)", alignItems: "center", justifyContent: "center", marginBottom: 16 },
  h2: { color: C.fg, fontSize: 17, fontWeight: "700", textAlign: "center" },
  body: { color: C.muted, fontSize: 14, textAlign: "center", marginTop: 8, maxWidth: 320 },
});
