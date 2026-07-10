// Admin portal group: role=admin guard + slide-in drawer shell.
//
// GUARD — every screen under /(app)/admin lives inside this layout, so the
// role check here fail-closes the whole portal: a non-admin is redirected to
// "/" (app/index re-routes them to their own home). The backend also 403s
// every /api/admin route, so this is defence-in-depth, not the only gate.
//
// SHELL — a custom left drawer (no @react-navigation/drawer dependency) whose
// nav list already covers every admin section (dashboard + the batches landing
// in 6.3-6.5). Links point at routes that may not exist yet; that is expected —
// the screens slot in later. Any admin screen opens the drawer via
// `useAdminDrawer()`.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, Modal, Animated, Dimensions, Text as RNText, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Redirect, Slot, useRouter, usePathname, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  LayoutDashboard, Users, Building2, Mail, Database, Inbox, ShieldAlert, BellRing,
  ScrollText, FlaskConical, FileText, BarChart3, Share2, KeyRound, MessageSquare,
  UserCog, LogOut, X, FlaskConical as Logo, type LucideIcon,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { colors as C, dawnGradient, fonts } from "@/src/theme/tokens";

const W = { w10: "rgba(255,255,255,0.10)", w15: "rgba(255,255,255,0.15)", w25: "rgba(255,255,255,0.25)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };
const PANEL_W = Math.min(300, Math.round(Dimensions.get("window").width * 0.82));

// ── Full admin nav (dashboard + every section shipping in 6.3-6.5) ──────────
export type AdminNavItem = { id: string; label: string; icon: LucideIcon; href: Href };
export const ADMIN_NAV: AdminNavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, href: "/(app)/admin" as Href },
  { id: "users", label: "Users", icon: Users, href: "/(app)/admin/users" as Href },
  { id: "organizations", label: "Organizations", icon: Building2, href: "/(app)/admin/organizations" as Href },
  { id: "invitations", label: "Invitations", icon: Mail, href: "/(app)/admin/invitations" as Href },
  { id: "master-data", label: "Master Data", icon: Database, href: "/(app)/admin/master-data" as Href },
  { id: "tickets", label: "Support Tickets", icon: Inbox, href: "/(app)/admin/tickets" as Href },
  { id: "alerts", label: "System Alerts", icon: ShieldAlert, href: "/(app)/admin/alerts" as Href },
  { id: "notification-monitoring", label: "Notifications", icon: BellRing, href: "/(app)/admin/notification-monitoring" as Href },
  { id: "audit-logs", label: "Audit Logs", icon: ScrollText, href: "/(app)/admin/audit-logs" as Href },
  { id: "trials", label: "Trials", icon: FlaskConical, href: "/(app)/admin/trials" as Href },
  { id: "terms", label: "Terms & Privacy", icon: FileText, href: "/(app)/admin/terms" as Href },
  { id: "reports", label: "Reports", icon: BarChart3, href: "/(app)/admin/reports" as Href },
  { id: "delegation", label: "Delegation", icon: Share2, href: "/(app)/admin/delegation" as Href },
  { id: "emergency-access", label: "Emergency Access", icon: KeyRound, href: "/(app)/admin/emergency-access" as Href },
  { id: "messages", label: "Messages", icon: MessageSquare, href: "/(app)/admin/messages" as Href },
  { id: "profile", label: "My Profile", icon: UserCog, href: "/(app)/admin/profile" as Href },
];

// ── Drawer context: any admin screen calls useAdminDrawer().open() ──────────
type DrawerCtx = { open: () => void; close: () => void; isOpen: boolean };
const AdminDrawerCtx = createContext<DrawerCtx>({ open: () => {}, close: () => {}, isOpen: false });
export const useAdminDrawer = () => useContext(AdminDrawerCtx);

export default function AdminLayout() {
  const { user, loading, signOut } = useAuth();

  // Fail-closed guard. `role` is typed without 'admin' in the frontend User,
  // so compare via string to stay tsc-clean while matching the backend role.
  const role = (user?.role as string) || "";

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: C.background }}>
        <ActivityIndicator color={C.primary} />
      </View>
    );
  }
  if (!user) return <Redirect href="/(auth)/welcome" />;
  if (role !== "admin") return <Redirect href="/" />;

  return <AdminShell signOut={signOut} />;
}

function AdminShell({ signOut }: { signOut: () => Promise<void> }) {
  const router = useRouter();
  const pathname = usePathname();
  const [isOpen, setOpen] = useState(false);
  const tx = useRef(new Animated.Value(-PANEL_W)).current;
  const fade = useRef(new Animated.Value(0)).current;

  const open = useCallback(() => setOpen(true), []);
  const close = useCallback(() => {
    Animated.parallel([
      Animated.timing(tx, { toValue: -PANEL_W, duration: 200, useNativeDriver: true }),
      Animated.timing(fade, { toValue: 0, duration: 200, useNativeDriver: true }),
    ]).start(() => setOpen(false));
  }, [tx, fade]);

  useEffect(() => {
    if (isOpen) {
      Animated.parallel([
        Animated.timing(tx, { toValue: 0, duration: 220, useNativeDriver: true }),
        Animated.timing(fade, { toValue: 1, duration: 220, useNativeDriver: true }),
      ]).start();
    }
  }, [isOpen, tx, fade]);

  const ctx = useMemo<DrawerCtx>(() => ({ open, close, isOpen }), [open, close, isOpen]);

  const go = (item: AdminNavItem) => {
    close();
    // Push after the close animation kicks off; dashboard is a replace so the
    // drawer never stacks the same route twice.
    setTimeout(() => {
      if (item.id === "dashboard") router.replace(item.href);
      else router.push(item.href);
    }, 60);
  };

  const doSignOut = async () => {
    close();
    await signOut();
    router.replace("/(auth)/welcome");
  };

  // active if the current path ends with the section id (dashboard = /admin).
  const isActive = (item: AdminNavItem) =>
    item.id === "dashboard"
      ? pathname === "/(app)/admin" || pathname === "/admin" || pathname.endsWith("/admin")
      : pathname.includes(`/admin/${item.id}`);

  return (
    <AdminDrawerCtx.Provider value={ctx}>
      <View style={{ flex: 1, backgroundColor: C.background }}>
        <Slot />

        <Modal visible={isOpen} transparent animationType="none" onRequestClose={close}>
          <View style={{ flex: 1, flexDirection: "row" }}>
            <Animated.View style={[st.panel, { transform: [{ translateX: tx }] }]}>
              <LinearGradient colors={[C.primaryDeep, C.primary] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={StyleSheet.absoluteFill} />
              <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1 }}>
                {/* Brand */}
                <View style={st.brand}>
                  <View style={st.logo}>
                    <Logo size={20} color={C.primaryFg} />
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <RNText style={st.brandTitle} numberOfLines={1}>TrialSync</RNText>
                    <RNText style={st.brandSub}>ADMIN PORTAL</RNText>
                  </View>
                  <Pressable testID="admin-drawer-close" onPress={close} style={st.closeBtn} hitSlop={8}>
                    <X size={18} color={W.w70} />
                  </Pressable>
                </View>

                <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingVertical: 8 }} showsVerticalScrollIndicator={false}>
                  {ADMIN_NAV.map((item) => {
                    const active = isActive(item);
                    const Icon = item.icon;
                    return (
                      <Pressable
                        key={item.id}
                        testID={`admin-nav-${item.id}`}
                        onPress={() => go(item)}
                        style={[st.navRow, active && st.navRowActive]}
                      >
                        {active && <View style={st.navAccent} />}
                        <Icon size={18} color={active ? C.primaryFg : W.w70} />
                        <RNText style={[st.navLabel, active && { color: C.primaryFg, fontFamily: fonts.semibold }]} numberOfLines={1}>
                          {item.label}
                        </RNText>
                      </Pressable>
                    );
                  })}
                </ScrollView>

                <Pressable testID="admin-signout" onPress={doSignOut} style={st.signOut}>
                  <LogOut size={18} color={W.w70} />
                  <RNText style={st.navLabel}>Sign out</RNText>
                </Pressable>
              </SafeAreaView>
            </Animated.View>

            {/* Backdrop */}
            <Animated.View style={{ flex: 1, opacity: fade }}>
              <Pressable testID="admin-drawer-backdrop" onPress={close} style={{ flex: 1, backgroundColor: "rgba(46,27,51,0.45)" }} />
            </Animated.View>
          </View>
        </Modal>
      </View>
    </AdminDrawerCtx.Provider>
  );
}

const st = StyleSheet.create({
  panel: { width: PANEL_W, height: "100%", overflow: "hidden" },
  brand: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: W.w15 },
  logo: { width: 40, height: 40, borderRadius: 14, backgroundColor: W.w15, alignItems: "center", justifyContent: "center" },
  brandTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 17 },
  brandSub: { color: W.w55, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.6, marginTop: 1 },
  closeBtn: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  navRow: { flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 12, paddingHorizontal: 18, marginHorizontal: 8, borderRadius: 12, position: "relative" },
  navRowActive: { backgroundColor: W.w15 },
  navAccent: { position: "absolute", left: 4, top: 12, bottom: 12, width: 3, borderRadius: 2, backgroundColor: C.accent },
  navLabel: { color: W.w70, fontFamily: fonts.medium, fontSize: 14, flex: 1 },
  signOut: { flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 14, paddingHorizontal: 18, borderTopWidth: 1, borderTopColor: W.w15 },
});
