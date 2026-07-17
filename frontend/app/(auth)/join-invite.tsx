import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { AxiosError } from "axios";
import { Ticket, Building2, BadgeCheck, ArrowRight, BriefcaseBusiness, LockKeyhole, Mail, Pencil, Phone, ShieldCheck, UserRound, AlertCircle, Clock, XCircle, CheckCircle2 } from "lucide-react-native";
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
  admin_name?: string;
  org_name?: string;
  full_name?: string;
  designation?: string;
  phone?: string;
  email: string;
  status: string; // pending | expired | cancelled | accepted
  expires_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO Manager", site: "Site / Hospital",
  pi: "Principal Investigator", crc: "Research Coordinator (CRC)", patient: "Patient",
};
const roleLabel = (r?: string) => (r ? ROLE_LABELS[r] || r : "Team member");
const roleCode = (value: string, fallback = "patient") => {
  const normalized = value.trim().toLowerCase();
  const match = Object.entries(ROLE_LABELS).find(([code, label]) =>
    code.toLowerCase() === normalized || label.toLowerCase() === normalized
  );
  return match?.[0] || fallback;
};

const normalizeInviteCode = (value: string) => {
  let raw = value.trim();
  if (raw.includes("/")) raw = raw.replace(/\/+$/, "").split("/").pop()?.split("?")[0] || raw;
  const compact = raw.replace(/[^a-z0-9]/gi, "");
  if (compact.slice(0, 3).toUpperCase() === "MTB") {
    const suffix = compact.slice(3, 11).toUpperCase();
    const groups = suffix.match(/.{1,4}/g) || [];
    return ["MTB", ...groups].join("-");
  }
  return /^[a-f0-9]{32}$/i.test(compact) ? compact.toLowerCase() : raw;
};

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
  const [draft, setDraft] = useState({ fullName: "", designation: "", role: "", phone: "" });
  const [editingField, setEditingField] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [accepting, setAccepting] = useState(false);

  const canVerify = code.trim().length >= 4;
  const isPending = phase === "resolved" && invite?.status === "pending";
  const isAccepted = phase === "resolved" && invite?.status === "accepted";

  const onCodeChange = (t: string) => {
    setCode(normalizeInviteCode(t));
    if (phase !== "idle") { setPhase("idle"); setInvite(null); setErrorMsg(""); }
  };

  const verify = async () => {
    const token = normalizeInviteCode(code);
    if (token.length < 4) return;
    setPhase("loading");
    setInvite(null);
    setErrorMsg("");
    try {
      const res = await api.get<ResolvedInvite>(`/invitations/${encodeURIComponent(token)}`);
      setInvite(res.data);
      setDraft({
        fullName: res.data.full_name || "",
        designation: res.data.designation || "",
        role: roleLabel(res.data.role),
        phone: res.data.phone || "",
      });
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
    const token = normalizeInviteCode(code);
    setAccepting(true);
    try {
      router.push({
        pathname: "/(auth)/register",
        params: {
          inviteToken: token,
          role: invite.role || "patient",
          org: invite.org || "",
          email: invite.email || "",
          fullName: draft.fullName.trim(),
          designation: roleCode(invite.role) === "patient" ? "" : draft.designation.trim(),
          phone: draft.phone.trim(),
        },
      });
    } finally {
      setAccepting(false);
    }
  };

  const stateInfo = phase === "resolved" && invite && invite.status !== "pending" ? STATE_INFO[invite.status] : null;
  const isPatientInvite = draft.role.trim().toLowerCase() === "patient";
  const updateDraft = (key: keyof typeof draft) => (value: string) => setDraft((current) => ({ ...current, [key]: value }));

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
                autoCapitalize="characters"
                autoCorrect={false}
                placeholder="MTB-XXXX-XXXX"
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
                  <Eyebrow color={colors.accent}>{"You've been invited"}</Eyebrow>
                </View>
                <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 9 }}>
                  <Building2 size={20} color={colors.primary} style={{ marginTop: 2 }} />
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontFamily: fonts.heading, fontSize: 19, color: colors.primary }}>{invite.org || "Your organization"}</Text>
                    {invite.site ? <Small>{invite.site}</Small> : null}
                  </View>
                </View>

                <View style={s.detailsSection}>
                  <Eyebrow color={colors.mutedFg}>Your details</Eyebrow>
                  <View style={s.fieldGrid}>
                    <EditableField label="User name" icon={UserRound} value={draft.fullName} onChangeText={updateDraft("fullName")} editing={editingField === "fullName"} onEdit={() => setEditingField("fullName")} onDone={() => setEditingField(null)} />
                    {!isPatientInvite ? <EditableField label="Designation" icon={BriefcaseBusiness} value={draft.designation} onChangeText={updateDraft("designation")} editing={editingField === "designation"} onEdit={() => setEditingField("designation")} onDone={() => setEditingField(null)} /> : null}
                    <EditableField label="Role" icon={ShieldCheck} value={draft.role} onChangeText={updateDraft("role")} editing={editingField === "role"} onEdit={() => setEditingField("role")} onDone={() => setEditingField(null)} />
                    <EditableField label="Phone number" icon={Phone} value={draft.phone} onChangeText={updateDraft("phone")} keyboardType="phone-pad" editing={editingField === "phone"} onEdit={() => setEditingField("phone")} onDone={() => setEditingField(null)} />
                    <EditableField label="Email ID" icon={Mail} value={invite.email} editable={false} />
                  </View>
                  <Small style={s.editHint}>Tap a value to edit it. Email ID is fixed by the invitation.</Small>
                </View>

                <View style={s.inviterCard}>
                  <Eyebrow color={colors.accent}>Invited by</Eyebrow>
                  <Row label="Organization" value={invite.org_name || invite.org || "Your organization"} />
                  <Row label="Admin name" value={invite.admin_name || invite.inviter || "Organization admin"} />
                </View>
                <View style={s.footNote}>
                  <Small style={{ fontSize: 12 }}>Review your details before accepting the invitation.</Small>
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

function EditableField({
  label,
  icon: Icon,
  value,
  onChangeText,
  editable = true,
  keyboardType = "default",
  editing = false,
  onEdit,
  onDone,
}: {
  label: string;
  icon: typeof UserRound;
  value: string;
  onChangeText?: (value: string) => void;
  editable?: boolean;
  keyboardType?: "default" | "phone-pad";
  editing?: boolean;
  onEdit?: () => void;
  onDone?: () => void;
}) {
  return (
    <View style={s.editField}>
      <View style={s.fieldLabel}>
        <Icon size={13} color={colors.mutedFg} />
        <Small style={{ fontSize: 11 }}>{label}</Small>
      </View>
      {editing && editable ? (
        <TextInput
          autoFocus
          value={value}
          onChangeText={onChangeText}
          onBlur={onDone}
          onSubmitEditing={onDone}
          keyboardType={keyboardType}
          placeholder={`Enter ${label.toLowerCase()}`}
          placeholderTextColor={colors.mutedFg + "77"}
          style={s.detailInput}
        />
      ) : (
        <Pressable onPress={editable ? onEdit : undefined} accessibilityRole={editable ? "button" : "text"} accessibilityLabel={editable ? `Edit ${label}` : `${label}, not editable`} style={s.detailValue}>
          <Text numberOfLines={1} style={[s.detailValueText, !editable && { color: colors.mutedFg }]}>{value || "Add details"}</Text>
          {editable ? <Pencil size={13} color={colors.accent} /> : <LockKeyhole size={13} color={colors.mutedFg} />}
        </Pressable>
      )}
    </View>
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
  detailsSection: { marginTop: 16, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 14, gap: 10 },
  fieldGrid: { borderTopWidth: 1, borderTopColor: colors.border },
  editField: { minHeight: 48, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: colors.border, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  fieldLabel: { width: 108, flexDirection: "row", alignItems: "center", gap: 5 },
  detailValue: { flex: 1, minHeight: 30, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 7 },
  detailValueText: { flexShrink: 1, textAlign: "right", color: colors.foreground, fontFamily: fonts.semibold, fontSize: 12 },
  detailInput: { flex: 1, minHeight: 34, borderBottomWidth: 2, borderBottomColor: colors.accent, paddingHorizontal: 0, paddingVertical: 5, textAlign: "right", color: colors.foreground, fontFamily: fonts.semibold, fontSize: 12 },
  editHint: { fontSize: 11, lineHeight: 16 },
  inviterCard: { marginTop: 14, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background + "99", padding: 12, gap: 8 },
  footNote: { marginTop: 16, marginHorizontal: -(spacing.md + 4), marginBottom: -(spacing.md + 4), paddingHorizontal: spacing.md + 4, paddingVertical: 10, backgroundColor: colors.surface + "99", borderTopWidth: 1, borderTopColor: colors.border },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { flexDirection: "row", gap: 8, paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
