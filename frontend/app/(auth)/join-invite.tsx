import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { AxiosError } from "axios";
import { Ticket, Building2, BadgeCheck, ArrowRight, Mail, AlertCircle, Clock, XCircle, CheckCircle2 } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";

// Resolved from GET /api/invitations/{token}: the admin who sent the invite already
// chose the org/site and the role — the invitee only accepts, never edits them.
interface ResolvedInvite {
  org: string;
  site: string;
  role: string;
  inviter: string;
  email: string;
  status: string; // pending | expired | cancelled | accepted
  expires_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO Manager", site: "Site / Hospital",
  pi: "Principal Investigator", crc: "Research Coordinator (CRC)", patient: "Patient",
};
const roleLabel = (r?: string) => (r ? ROLE_LABELS[r] || r : "Team member");

// UI copy for a resolved-but-not-acceptable invite, keyed by effective status.
const STATE_INFO: Record<string, { icon: React.ReactNode; title: string; note: string; tone: string }> = {
  expired: { icon: <Clock size={18} color={colors.accent} />, title: "This invitation has expired", note: "Ask your admin to resend the invitation, then enter the new code here.", tone: colors.accent },
  cancelled: { icon: <XCircle size={18} color={colors.destructive} />, title: "This invitation was cancelled", note: "This invite is no longer valid. Please contact your admin for a new one.", tone: colors.destructive },
  accepted: { icon: <CheckCircle2 size={18} color={colors.success} />, title: "Already accepted", note: "This invitation has already been accepted. You can sign in to your account.", tone: colors.success },
};

export default function JoinInvite() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "resolved" | "error">("idle");
  const [invite, setInvite] = useState<ResolvedInvite | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [accepting, setAccepting] = useState(false);

  const canVerify = code.trim().length >= 4;
  const isPending = phase === "resolved" && invite?.status === "pending";
  const isAccepted = phase === "resolved" && invite?.status === "accepted";

  const onCodeChange = (t: string) => {
    // Invite tokens are case-sensitive hex — do not force-uppercase.
    setCode(t.trim());
    if (phase !== "idle") { setPhase("idle"); setInvite(null); setErrorMsg(""); }
  };

  const verify = async () => {
    const token = code.trim();
    if (token.length < 4) return;
    setPhase("loading");
    setInvite(null);
    setErrorMsg("");
    try {
      const res = await api.get<ResolvedInvite>(`/invitations/${encodeURIComponent(token)}`);
      setInvite(res.data);
      setPhase("resolved");
    } catch (e) {
      const status = (e as AxiosError)?.response?.status;
      setErrorMsg(
        status === 404
          ? "We couldn't find an invitation for that code. Double-check it with the person who invited you."
          : "Something went wrong while checking this code. Please try again in a moment."
      );
      setPhase("error");
    }
  };

  const accept = async () => {
    if (!invite || invite.status !== "pending") return;
    const token = code.trim();
    setAccepting(true);
    try {
      const res = await api.post<{ ok: boolean; status: string; email: string; role: string; org: string }>(
        `/invitations/${encodeURIComponent(token)}/accept`
      );
      router.push({
        pathname: "/(auth)/register",
        params: {
          role: res.data?.role || invite.role || "patient",
          org: res.data?.org || invite.org || "",
          email: res.data?.email || invite.email || "",
        },
      });
    } catch (e) {
      const status = (e as AxiosError)?.response?.status;
      setErrorMsg(
        status === 400
          ? "This invitation can no longer be accepted. Please ask your admin for a new one."
          : "We couldn't accept the invitation just now. Please try again."
      );
      setPhase("error");
    } finally {
      setAccepting(false);
    }
  };

  const stateInfo = phase === "resolved" && invite && invite.status !== "pending" ? STATE_INFO[invite.status] : null;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Join your team"
          title="Enter your invite"
          subtitle="Paste the code from the invitation your site admin sent you. No new site is created — you join theirs."
          onBack={() => router.back()}
        />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {/* Code entry */}
          <Rise delay={160}>
            <View style={s.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <View style={s.iconBadge}><Ticket size={14} color={colors.primary} /></View>
                <Eyebrow color={colors.mutedFg}>Invitation code</Eyebrow>
              </View>
              <TextInput
                value={code}
                onChangeText={onCodeChange}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="Paste your invite code"
                placeholderTextColor={colors.mutedFg + "66"}
                style={s.codeInput}
              />
              {phase !== "resolved" && (
                <Pressable onPress={verify} disabled={!canVerify || phase === "loading"} style={[s.next, canVerify ? { backgroundColor: colors.secondary } : { backgroundColor: colors.surface }]}>
                  {phase === "loading" ? (
                    <ActivityIndicator size="small" color={colors.primary} />
                  ) : (
                    <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: canVerify ? colors.primary : colors.mutedFg }}>Check invite</Text>
                  )}
                </Pressable>
              )}
            </View>
          </Rise>

          {/* Error state (not found / network / accept failure) */}
          {phase === "error" && (
            <Rise delay={40}>
              <View style={s.errorCard}>
                <AlertCircle size={18} color={colors.destructive} />
                <Small color={colors.destructive} style={{ flex: 1, lineHeight: 19 }}>{errorMsg}</Small>
              </View>
            </Rise>
          )}

          {/* Resolved but not acceptable (expired / cancelled / accepted) */}
          {stateInfo && (
            <Rise delay={40}>
              <View style={[s.stateCard, { borderColor: stateInfo.tone + "40", backgroundColor: stateInfo.tone + "12" }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  {stateInfo.icon}
                  <Body weight="700" style={{ fontSize: 15, color: stateInfo.tone }}>{stateInfo.title}</Body>
                </View>
                <Small style={{ lineHeight: 19 }}>{stateInfo.note}</Small>
                {invite && (invite.org || invite.role) ? (
                  <Small style={{ marginTop: 8 }}>Invite for {roleLabel(invite.role)}{invite.org ? ` · ${invite.org}` : ""}</Small>
                ) : null}
              </View>
            </Rise>
          )}

          {/* Resolved & valid invitation */}
          {isPending && invite && (
            <Rise delay={40}>
              <LinearGradient colors={[colors.secondary + "66", colors.card]} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={s.inviteCard}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 16 }}>
                  <BadgeCheck size={16} color={colors.accent} />
                  <Eyebrow color={colors.accent}>You've been invited</Eyebrow>
                </View>
                <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12 }}>
                  <View style={s.orgIcon}><Building2 size={20} color={colors.primary} /></View>
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontFamily: fonts.heading, fontSize: 19, color: colors.primary }}>{invite.org || "Your organization"}</Text>
                    {invite.site ? <Small>{invite.site}</Small> : null}
                  </View>
                </View>
                <View style={s.dl}>
                  <Row label="Your role" value={roleLabel(invite.role)} />
                  {invite.inviter ? <Row label="Invited by" value={invite.inviter} /> : null}
                  {invite.email ? <Row label="Account email" value={invite.email} icon={<Mail size={14} color={colors.mutedFg} />} /> : null}
                </View>
                <View style={s.footNote}>
                  <Small style={{ fontSize: 12 }}>Your role is set by your admin and can't be changed here.</Small>
                </View>
              </LinearGradient>
            </Rise>
          )}
        </ScrollView>

        <View style={s.footer}>
          {isAccepted ? (
            <Springy onPress={() => router.push("/(auth)/sign-in")} style={[s.cta, { backgroundColor: colors.primary }]}>
              <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>Go to sign in</Text>
              <ArrowRight size={16} color={colors.primaryFg} />
            </Springy>
          ) : (
            <Springy onPress={accept} disabled={!isPending || accepting} style={[s.cta, isPending ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
              {accepting ? (
                <ActivityIndicator size="small" color={colors.primaryFg} />
              ) : (
                <>
                  <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: isPending ? colors.primaryFg : colors.mutedFg }}>Accept & continue</Text>
                  {isPending && <ArrowRight size={16} color={colors.primaryFg} />}
                </>
              )}
            </Springy>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Row({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
        {icon}
        <Small>{label}</Small>
      </View>
      <Body weight="700" style={{ fontSize: 13, flexShrink: 1, textAlign: "right" }}>{value}</Body>
    </View>
  );
}

const s = StyleSheet.create({
  card: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md + 4 },
  iconBadge: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  codeInput: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, paddingHorizontal: 16, paddingVertical: 14, textAlign: "center", fontFamily: fonts.mono, fontSize: 16, letterSpacing: 2, color: colors.foreground },
  next: { marginTop: 12, paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  errorCard: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginTop: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "12", padding: spacing.md },
  stateCard: { marginTop: spacing.lg, borderRadius: radii.xl, borderWidth: 1, padding: spacing.md + 4 },
  inviteCard: { marginTop: spacing.lg, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.primary + "40", padding: spacing.md + 4, overflow: "hidden" },
  orgIcon: { width: 44, height: 44, borderRadius: radii.md, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  dl: { marginTop: 16, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 16, gap: 10 },
  footNote: { marginTop: 16, marginHorizontal: -(spacing.md + 4), marginBottom: -(spacing.md + 4), paddingHorizontal: spacing.md + 4, paddingVertical: 10, backgroundColor: colors.surface + "99", borderTopWidth: 1, borderTopColor: colors.border },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { flexDirection: "row", gap: 8, paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
