import React, { useEffect, useState } from "react";
import {
  View, Text, ScrollView, Pressable, Switch, TextInput, StyleSheet, Modal,
  KeyboardAvoidingView, Platform, ActivityIndicator, StatusBar,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import {
  ChevronLeft, ChevronRight, ChevronDown, Lock, AlertTriangle, Eye, EyeOff,
  Check, X, MessageCircle, Mail, Phone, Clock, Ticket, HelpCircle, BarChart2, Building2, FlaskConical,
} from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";

// ── Password strength rules (mirrors patient profile) ─────────────────────────
const passwordRules = [
  { label: "Minimum 8 characters", test: (p: string) => p.length >= 8 },
  { label: "Uppercase letter (A-Z)", test: (p: string) => /[A-Z]/.test(p) },
  { label: "Lowercase letter (a-z)", test: (p: string) => /[a-z]/.test(p) },
  { label: "Numeric character (0-9)", test: (p: string) => /[0-9]/.test(p) },
  { label: "Special character (!@#$%…)", test: (p: string) => /[^A-Za-z0-9]/.test(p) },
];

const ENTITY_TYPES = ["Sponsor", "CRO", "SMO", "Site / Hospital"];

// ── Shared header (plum dawn bar) ─────────────────────────────────────────────
function SubHeader({ eyebrow, title, onBack, rightLabel, onRight }: {
  eyebrow: string; title: string; onBack?: () => void; rightLabel?: string; onRight?: () => void;
}) {
  const router = useRouter();
  return (
    <View style={{ backgroundColor: colors.primaryDeep }}>
      <SafeAreaView edges={["top"]}>
        <View style={h.row}>
          <Pressable testID="profile-back" onPress={onBack || (() => router.back())} hitSlop={10} style={h.back}>
            <ChevronLeft size={22} color={colors.primaryFg} />
          </Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={h.eyebrow}>{eyebrow.toUpperCase()}</Text>
            <Text style={h.title} numberOfLines={1}>{title}</Text>
          </View>
          {rightLabel ? (
            <Springy onPress={onRight} style={h.rightBtn}><Small weight="700" color={colors.primaryFg}>{rightLabel}</Small></Springy>
          ) : null}
        </View>
      </SafeAreaView>
    </View>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={p.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function Toggle({ on, onToggle, testID }: { on: boolean; onToggle: () => void; testID?: string }) {
  return (
    <Switch testID={testID} value={on} onValueChange={onToggle} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  EDIT PROFILE  — PATCH /auth/me (name), OTP flow for phone/email
// ══════════════════════════════════════════════════════════════════════════════
function EditProfile() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [prof, setProf] = useState({ fullName: "", phone: "", email: "" });
  const [entity, setEntity] = useState({ type: "Site / Hospital", orgName: "—", orgAddress: "—", role: "" });
  const [loaded, setLoaded] = useState({ phone: "", email: "" });
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState("");

  type OtpItem = { field: "email" | "phone"; value: string };
  const [otp, setOtp] = useState<{ open: boolean; field: "email" | "phone"; value: string; code: string; step: "sending" | "code"; error: string; busy: boolean }>(
    { open: false, field: "email", value: "", code: "", step: "sending", error: "", busy: false });
  const [otpQueue, setOtpQueue] = useState<OtpItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setLoadError(false);
      try {
        const me = (await api.get("/auth/me")).data;
        if (cancelled) return;
        setProf({ fullName: me.full_name || "", phone: (me.phone || "").replace(/^\+91\s?/, ""), email: me.email || "" });
        setLoaded({ phone: me.phone || "", email: me.email || "" });
        const role = me.role === "pi" ? "PI" : "Research Team";
        setEntity(e => ({ ...e, orgName: me.organization || "—", role }));
        if (me.organization) {
          try {
            const r = await api.get("/organizations", { params: { search: me.organization } });
            const match = (r.data || []).find((o: any) => o.name === me.organization) || (r.data || [])[0];
            if (!cancelled && match) setEntity(e => ({ ...e, orgAddress: match.address || "—", type: ORG_TYPE_LABEL[match.type] || e.type }));
          } catch {}
        }
      } catch { if (!cancelled) setLoadError(true); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  const beginOtpQueue = (queue: OtpItem[]) => {
    if (!queue.length) { router.back(); return; }
    setOtpQueue(queue);
    startContact(queue[0]);
  };
  const startContact = async (item: OtpItem) => {
    setOtp({ open: true, field: item.field, value: item.value, code: "", step: "sending", error: "", busy: true });
    try {
      await api.post("/auth/change-contact/start", { field: item.field, value: item.value });
      setOtp(o => ({ ...o, step: "code", busy: false }));
    } catch (e: any) {
      setOtp(o => ({ ...o, step: "code", busy: false, error: e?.response?.data?.detail || "Could not send the verification code." }));
    }
  };
  const verifyContact = async () => {
    setOtp(o => ({ ...o, busy: true, error: "" }));
    try {
      await api.post("/auth/change-contact/verify", { code: otp.code });
      const { field, value } = otp;
      setLoaded(l => ({ ...l, [field]: value }));
      if (field === "email") setProf(pp => ({ ...pp, email: value }));
      else setProf(pp => ({ ...pp, phone: value.replace(/^\+91\s?/, "") }));
      await refresh();
      const rest = otpQueue.slice(1);
      setOtpQueue(rest);
      if (rest.length) startContact(rest[0]);
      else { setOtp(o => ({ ...o, open: false, busy: false })); router.back(); }
    } catch (e: any) {
      setOtp(o => ({ ...o, busy: false, error: e?.response?.data?.detail || "Incorrect code. Please try again." }));
    }
  };
  const cancelOtp = () => { setOtp(o => ({ ...o, open: false })); setOtpQueue([]); };

  const save = async () => {
    setSaving(true); setSaveError("");
    try {
      const newEmail = prof.email.trim().toLowerCase();
      const loadedEmail = (loaded.email || "").trim().toLowerCase();
      const newPhoneDigits = prof.phone.replace(/\D/g, "");
      const loadedPhoneDigits = (loaded.phone || "").replace(/^\+91\s?/, "").replace(/\D/g, "");
      const newPhone = newPhoneDigits ? "+91" + newPhoneDigits : "";
      await api.patch("/auth/me", { full_name: prof.fullName });
      await refresh();
      const queue: OtpItem[] = [];
      if (newEmail && newEmail !== loadedEmail) queue.push({ field: "email", value: newEmail });
      if (newPhoneDigits && newPhoneDigits !== loadedPhoneDigits) queue.push({ field: "phone", value: newPhone });
      if (queue.length) beginOtpQueue(queue);
      else router.back();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Could not save your changes. Please try again.");
    } finally { setSaving(false); }
  };

  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Account" title="Edit Profile" rightLabel={saving ? "Saving…" : "Save"} onRight={save} />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
          {loading ? (
            <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Small color={colors.mutedFg}>Loading your profile…</Small>
            </View>
          ) : null}
          {loadError ? (
            <View style={[p.warn, { marginBottom: spacing.md }]}>
              <AlertTriangle size={14} color={colors.warning} />
              <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load your profile. Some details may be missing.</Small>
            </View>
          ) : null}
          <Rise delay={40}>
            <View style={p.card}>
              <Field label="Full Name *"><TextInput value={prof.fullName} onChangeText={v => setProf({ ...prof, fullName: v })} style={p.input} /></Field>
            </View>
          </Rise>
          <Rise delay={110}>
            <View style={[p.card, { marginTop: spacing.md }]}>
              <Eyebrow color={colors.mutedFg} style={{ marginBottom: spacing.sm }}>Contact — verified channels</Eyebrow>
              <Field label="Phone Number">
                <View style={{ flexDirection: "row", gap: 8 }}>
                  <View style={p.prefix}><Text style={{ color: colors.mutedFg, fontFamily: fonts.semibold }}>+91</Text></View>
                  <TextInput value={prof.phone} onChangeText={v => setProf({ ...prof, phone: v })} keyboardType="phone-pad" style={[p.input, { flex: 1 }]} />
                </View>
                <View style={p.warn}><AlertTriangle size={14} color={colors.warning} /><Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Changing this requires OTP verification.</Small></View>
              </Field>
              <Field label="Email ID">
                <TextInput value={prof.email} onChangeText={v => setProf({ ...prof, email: v })} keyboardType="email-address" autoCapitalize="none" style={p.input} />
                <View style={p.warn}><AlertTriangle size={14} color={colors.warning} /><Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Changing this requires OTP verification.</Small></View>
              </Field>
            </View>
          </Rise>
          <Rise delay={200}>
            <View style={[p.card, { marginTop: spacing.md }]}>
              <Eyebrow color={colors.mutedFg} style={{ marginBottom: spacing.sm }}>Entity — managed by your admin</Eyebrow>
              {[
                { label: "Entity Type", val: entity.type },
                { label: "Organization", val: entity.orgName },
                { label: "Org. Address", val: entity.orgAddress },
                { label: "Role", val: entity.role },
              ].map((r, i) => (
                <View key={r.label} style={[{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 10 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border + "99" }]}>
                  <View style={{ flex: 1, minWidth: 0, paddingRight: spacing.sm }}>
                    <Small>{r.label}</Small>
                    <Body weight="600" style={{ marginTop: 2 }}>{r.val}</Body>
                  </View>
                  <Lock size={14} color={colors.mutedFg} />
                </View>
              ))}
              <Springy onPress={() => router.replace("/(app)/clinical/profile/entity-change")} style={p.linkBtn}>
                <Building2 size={15} color={colors.primary} />
                <Small color={colors.primary} weight="700">Request an entity change</Small>
              </Springy>
            </View>
          </Rise>
          {saveError ? <Small color={colors.destructive} style={{ marginTop: spacing.md }}>{saveError}</Small> : null}
        </ScrollView>
        <View style={p.footer}>
          <Springy onPress={save} disabled={saving} style={[p.cta, { backgroundColor: colors.primaryDeep }]}>
            <Text style={p.ctaText}>{saving ? "Saving…" : "Save Changes"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>

      {/* Contact-change OTP verification */}
      <Modal visible={otp.open} transparent animationType="fade" onRequestClose={cancelOtp}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <View style={p.dialogOverlay}>
            <Pressable style={StyleSheet.absoluteFill} onPress={otp.busy ? undefined : cancelOtp} />
            <View style={p.dialog}>
              <View style={[p.iconCircle, { backgroundColor: colors.primary + "1A", marginBottom: 12 }]}>
                {otp.field === "email" ? <Mail size={22} color={colors.primary} /> : <Phone size={22} color={colors.primary} />}
              </View>
              <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginBottom: 6 }}>Verify your {otp.field === "email" ? "email" : "phone"}</Text>
              <Small style={{ marginBottom: 16, lineHeight: 20 }}>
                {otp.step === "sending" ? "Sending a verification code…" : `Enter the 6-digit code sent to ${otp.value}.`}
              </Small>
              <TextInput
                value={otp.code}
                onChangeText={v => setOtp(o => ({ ...o, code: v.replace(/\D/g, "").slice(0, 6), error: "" }))}
                keyboardType="number-pad" placeholder="000000" placeholderTextColor={colors.mutedFg + "99"}
                editable={otp.step === "code" && !otp.busy}
                style={[p.input, { textAlign: "center", letterSpacing: 8, fontSize: 20, marginBottom: 10 }]}
              />
              {otp.error ? <Small color={colors.destructive} style={{ marginBottom: 10 }}>{otp.error}</Small> : null}
              <View style={{ flexDirection: "row", gap: 12 }}>
                <Springy onPress={cancelOtp} style={[p.dialogBtn, { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={colors.foreground}>Cancel</Small></Springy>
                <Springy onPress={verifyContact} disabled={otp.busy || otp.code.length < 6} style={[p.dialogBtn, { backgroundColor: (otp.busy || otp.code.length < 6) ? colors.surface : colors.primaryDeep }]}>
                  <Small weight="700" color={(otp.busy || otp.code.length < 6) ? colors.mutedFg : colors.primaryFg}>{otp.busy ? "Verifying…" : "Verify"}</Small>
                </Springy>
              </View>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  ENTITY CHANGE  — demo request flow (no backend endpoint; local confirmation)
// ══════════════════════════════════════════════════════════════════════════════
function EntityChange() {
  const router = useRouter();
  const [current, setCurrent] = useState({ type: "Site / Hospital", orgName: "—" });
  const [form, setForm] = useState<{ field: string; newValue: string }>({ field: "Entity Type", newValue: "" });
  const [warn, setWarn] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setLoadError(false);
      try {
        const me = (await api.get("/auth/me")).data;
        if (cancelled) return;
        setCurrent(c => ({ ...c, orgName: me.organization || "—" }));
        if (me.organization) {
          try {
            const r = await api.get("/organizations", { params: { search: me.organization } });
            const match = (r.data || []).find((o: any) => o.name === me.organization) || (r.data || [])[0];
            if (!cancelled && match) setCurrent(c => ({ ...c, type: ORG_TYPE_LABEL[match.type] || c.type }));
          } catch {}
        }
      } catch { if (!cancelled) setLoadError(true); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  const isType = form.field === "Entity Type";
  const currentVal = isType ? current.type : current.orgName;
  const canSubmit = !!form.newValue.trim();

  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Account" title="Entity Change" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
          {loading ? (
            <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Small color={colors.mutedFg}>Loading your entity details…</Small>
            </View>
          ) : null}
          {loadError ? (
            <View style={[p.warn, { marginBottom: spacing.md }]}>
              <AlertTriangle size={14} color={colors.warning} />
              <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load your current entity details.</Small>
            </View>
          ) : null}
          <Small style={{ marginBottom: spacing.md, lineHeight: 20 }}>Request a change to your registered entity details. Our team verifies each request before it takes effect.</Small>

          <Field label="What are you changing?">
            <View style={{ flexDirection: "row", gap: 8 }}>
              {["Entity Type", "Organization Name"].map(o => {
                const on = form.field === o;
                return (
                  <Springy key={o} onPress={() => setForm({ field: o, newValue: "" })} style={[p.chip, { flex: 1 }, on ? { backgroundColor: colors.primary } : { borderWidth: 1, borderColor: colors.border }]}>
                    <Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{o}</Small>
                  </Springy>
                );
              })}
            </View>
          </Field>

          <Field label="Current Value">
            <View style={[p.input, { backgroundColor: colors.surface }]}><Text style={{ color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 15 }}>{currentVal}</Text></View>
          </Field>

          <Field label="Change To">
            {isType ? (
              <View style={{ gap: 8 }}>
                {ENTITY_TYPES.filter(o => o !== current.type).map(o => {
                  const on = form.newValue === o;
                  return (
                    <Springy key={o} onPress={() => setForm(c => ({ ...c, newValue: o }))} style={[p.selectRow, on && { borderColor: colors.primary, backgroundColor: colors.primary + "12" }]}>
                      <View style={[p.radio, { borderColor: on ? colors.primary : colors.border }]}>{on && <View style={p.radioDot} />}</View>
                      <Body weight={on ? "600" : "400"}>{o}</Body>
                    </Springy>
                  );
                })}
              </View>
            ) : (
              <TextInput value={form.newValue} onChangeText={v => setForm(c => ({ ...c, newValue: v }))} placeholder="Enter new organization name" placeholderTextColor={colors.mutedFg + "99"} style={p.input} />
            )}
          </Field>
        </ScrollView>
        <View style={p.footer}>
          <Springy onPress={() => setWarn(true)} disabled={!canSubmit} style={[p.cta, { backgroundColor: canSubmit ? colors.primaryDeep : colors.surface }]}>
            <Text style={[p.ctaText, { color: canSubmit ? colors.primaryFg : colors.mutedFg }]}>Submit Request</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>

      {/* Confirmation warning */}
      <Modal visible={warn} transparent animationType="fade" onRequestClose={() => setWarn(false)}>
        <View style={p.dialogOverlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setWarn(false)} />
          <View style={p.dialog}>
            <View style={[p.iconCircle, { backgroundColor: colors.warning + "26", marginBottom: 12 }]}><AlertTriangle size={22} color={colors.warning} /></View>
            <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginBottom: 6 }}>{isType ? "Change entity type?" : "Submit change request?"}</Text>
            <Small style={{ marginBottom: 20, lineHeight: 20 }}>
              {isType
                ? `Changing your entity type to ${form.newValue || "the selected type"} will re-scope your access and unlink data from your current originator. This cannot be undone.`
                : `Submit a request to change your organization name to "${form.newValue}"?`}
            </Small>
            <View style={{ flexDirection: "row", gap: 12 }}>
              <Springy onPress={() => setWarn(false)} style={[p.dialogBtn, { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={colors.foreground}>Cancel</Small></Springy>
              <Springy onPress={() => { setWarn(false); setSubmitted(true); }} style={[p.dialogBtn, { backgroundColor: colors.primaryDeep }]}><Small weight="700" color={colors.primaryFg}>Confirm</Small></Springy>
            </View>
          </View>
        </View>
      </Modal>

      {/* Success */}
      <Modal visible={submitted} transparent animationType="fade" onRequestClose={() => { setSubmitted(false); router.back(); }}>
        <View style={p.dialogOverlay}>
          <View style={p.dialog}>
            <View style={[p.iconCircle, { backgroundColor: colors.success + "26", marginBottom: 12 }]}><Check size={22} color={colors.success} /></View>
            <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginBottom: 6 }}>Request submitted</Text>
            <Small style={{ marginBottom: 20, lineHeight: 20 }}>We'll verify your request and update your entity details within 24 hours.</Small>
            <Springy onPress={() => { setSubmitted(false); router.back(); }} style={[p.cta, { backgroundColor: colors.primaryDeep }]}><Text style={p.ctaText}>Done</Text></Springy>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  CHANGE PASSWORD  — POST /auth/change-password
// ══════════════════════════════════════════════════════════════════════════════
function ChangePassword() {
  const router = useRouter();
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [showPw, setShowPw] = useState({ current: false, next: false, confirm: false });
  const [err, setErr] = useState(""); const [saving, setSaving] = useState(false);

  const passStrength = passwordRules.filter(r => r.test(pw.next)).length;
  const passLabel = passStrength <= 2 ? "Weak" : passStrength <= 3 ? "Medium" : "Strong";
  const passColor = passStrength <= 2 ? colors.destructive : passStrength <= 3 ? colors.warning : colors.success;
  const passMatch = pw.confirm.length > 0 && pw.next === pw.confirm;
  const canUpdate = pw.current.length > 0 && passStrength === 5 && passMatch && !saving;

  const changePassword = async () => {
    if (!canUpdate) return;
    setSaving(true); setErr("");
    try {
      await api.post("/auth/change-password", { current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", confirm: "" });
      router.back();
    } catch (e: any) { setErr(e?.response?.data?.detail || "Could not change password."); } finally { setSaving(false); }
  };

  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Account" title="Change Password" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
          {([["current", "Current Password *"], ["next", "New Password *"], ["confirm", "Confirm New Password *"]] as const).map(([key, label], i) => (
            <Rise key={key} delay={40 + i * 70}>
              <Field label={label}>
                <View style={{ position: "relative" }}>
                  <TextInput
                    value={(pw as any)[key]} onChangeText={v => setPw({ ...pw, [key]: v })}
                    secureTextEntry={!(showPw as any)[key]} autoCapitalize="none"
                    placeholder={label.replace(" *", "")} placeholderTextColor={colors.mutedFg + "99"}
                    style={[p.input, { paddingRight: 44 }]}
                  />
                  <Pressable onPress={() => setShowPw({ ...showPw, [key]: !(showPw as any)[key] })} hitSlop={8} style={p.eye}>
                    {(showPw as any)[key] ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}
                  </Pressable>
                </View>
              </Field>
              {key === "next" && pw.next.length > 0 && (
                <View style={{ marginBottom: spacing.md }}>
                  <View style={{ flexDirection: "row", gap: 6, marginBottom: 6 }}>
                    {[1, 2, 3, 4, 5].map(n => <View key={n} style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: passStrength >= n ? passColor : colors.border }} />)}
                  </View>
                  <Small>Password strength: <Small color={colors.foreground} weight="700">{passLabel}</Small></Small>
                </View>
              )}
              {key === "next" && (
                <View style={[p.card, { marginBottom: spacing.md }]}>
                  <Eyebrow color={colors.mutedFg} style={{ marginBottom: 8 }}>Requirements</Eyebrow>
                  {passwordRules.map(r => {
                    const met = r.test(pw.next);
                    return (
                      <View key={r.label} style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 8 }}>
                        <View style={[p.ruleDot, met ? { backgroundColor: colors.success + "26" } : { backgroundColor: colors.surface }]}>{met ? <Check size={11} color={colors.success} strokeWidth={3} /> : <X size={11} color={colors.mutedFg + "80"} />}</View>
                        <Small color={met ? colors.foreground : colors.mutedFg}>{r.label}</Small>
                      </View>
                    );
                  })}
                </View>
              )}
              {key === "confirm" && pw.confirm.length > 0 && (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: spacing.sm }}>
                  {passMatch ? <Check size={14} color={colors.success} /> : <X size={14} color={colors.destructive} />}
                  <Small color={passMatch ? colors.success : colors.destructive}>{passMatch ? "Passwords match" : "Passwords do not match"}</Small>
                </View>
              )}
            </Rise>
          ))}
          {err ? <Small color={colors.destructive}>{err}</Small> : null}
        </ScrollView>
        <View style={p.footer}>
          <Springy onPress={changePassword} disabled={!canUpdate} style={[p.cta, { backgroundColor: canUpdate ? colors.primaryDeep : colors.surface }]}>
            <Text style={[p.ctaText, { color: canUpdate ? colors.primaryFg : colors.mutedFg }]}>{saving ? "Updating…" : "Update Password"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  NOTIFICATIONS  — GET/PATCH /preferences (allow-listed keys only)
// ══════════════════════════════════════════════════════════════════════════════
function Notifications() {
  const router = useRouter();
  const [prefs, setPrefs] = useState<Record<string, boolean>>({
    reminders_visits: true, trial_updates: true, system_notifs: false,
    notifications_email: true, notifications_sms: false, notifications_push: true,
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setLoadError(false);
      try {
        const pr = (await api.get("/preferences")).data || {};
        if (cancelled) return;
        setPrefs(prev => {
          const next = { ...prev };
          Object.keys(prev).forEach(k => { if (typeof pr[k] === "boolean") next[k] = pr[k]; });
          return next;
        });
      } catch { if (!cancelled) setLoadError(true); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  const togglePref = async (k: string) => {
    const value = !prefs[k];
    setPrefs(p2 => ({ ...p2, [k]: value }));
    try { await api.patch("/preferences", { [k]: value }); }
    catch { setPrefs(p2 => (p2[k] === value ? { ...p2, [k]: !value } : p2)); }
  };

  const groups: { title: string; items: { label: string; desc: string; key: string }[] }[] = [
    { title: "Activity", items: [
      { label: "Visit reminders", desc: "Upcoming and overdue patient visits", key: "reminders_visits" },
      { label: "Patient & trial updates", desc: "New messages and status changes", key: "trial_updates" },
      { label: "Protocol deviations", desc: "System alerts when a deviation is logged", key: "system_notifs" },
    ] },
    { title: "Channels", items: [
      { label: "Email", desc: "Send notifications to your email", key: "notifications_email" },
      { label: "SMS", desc: "Send text messages to your phone", key: "notifications_sms" },
      { label: "Push", desc: "In-app push notifications", key: "notifications_push" },
    ] },
  ];

  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Account" title="Notifications" />
      <ScrollView contentContainerStyle={p.body}>
        {loading ? (
          <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading your preferences…</Small>
          </View>
        ) : null}
        {loadError ? (
          <View style={[p.warn, { marginBottom: spacing.md }]}>
            <AlertTriangle size={14} color={colors.warning} />
            <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load your preferences. Showing defaults.</Small>
          </View>
        ) : null}
        {groups.map((g, gi) => (
          <Rise key={g.title} delay={40 + gi * 80}>
            <View style={[p.card, { marginBottom: spacing.md }]}>
              <Eyebrow color={colors.mutedFg} style={{ borderBottomWidth: 1, borderBottomColor: colors.border, paddingBottom: 10, marginBottom: 4 }}>{g.title}</Eyebrow>
              {g.items.map((it, i) => (
                <View key={it.key} style={[{ flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: 12 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border + "99" }]}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Body weight="600">{it.label}</Body>
                    <Small style={{ marginTop: 2 }}>{it.desc}</Small>
                  </View>
                  <Toggle testID={`pref-${it.key}`} on={!!prefs[it.key]} onToggle={() => togglePref(it.key)} />
                </View>
              ))}
            </View>
          </Rise>
        ))}
        <Springy onPress={() => router.back()} style={[p.cta, { backgroundColor: colors.primaryDeep }]}><Text style={p.ctaText}>Save Preferences</Text></Springy>
      </ScrollView>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  REPORTS  — no dedicated backend endpoint yet; report types shown as a preview
// ══════════════════════════════════════════════════════════════════════════════
function Reports() {
  const CARDS = [
    { label: "Enrolment Summary", desc: "Screened, randomized & withdrawn", tint: colors.info },
    { label: "Visit Compliance", desc: "On-time vs overdue visits", tint: colors.success },
    { label: "Protocol Deviations", desc: "Logged deviations by trial", tint: colors.warning },
    { label: "Patient Status", desc: "Active, completed & dropouts", tint: colors.violet },
  ];
  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Insights" title="Reports" />
      <ScrollView contentContainerStyle={p.body}>
        {CARDS.map((r, i) => (
          <Rise key={r.label} delay={40 + i * 60}>
            <View style={[p.card, { marginBottom: spacing.sm, flexDirection: "row", alignItems: "center", gap: 12 }]}>
              <View style={[p.iconTile, { backgroundColor: r.tint + "1A" }]}><BarChart2 size={20} color={r.tint} /></View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Body weight="600">{r.label}</Body>
                <Small style={{ marginTop: 2 }}>{r.desc}</Small>
              </View>
            </View>
          </Rise>
        ))}
        <View style={[p.warn, { marginTop: spacing.sm }]}>
          <Clock size={14} color={colors.warning} />
          <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Downloadable report exports are coming soon.</Small>
        </View>
      </ScrollView>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  T&C  — GET /legal/terms
// ══════════════════════════════════════════════════════════════════════════════
type LegalDoc = { version: string; effective_date: string; blocks: { heading: string; body: string }[] };
function Tnc() {
  const [doc, setDoc] = useState<LegalDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError(false);
      try {
        const data = (await api.get("/legal/terms")).data as LegalDoc;
        if (!cancelled) setDoc(data);
      } catch { if (!cancelled) setError(true); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  const fallback: [string, string][] = [
    ["1. Use of Application", "This platform helps research teams manage clinical-trial visit schedules, patient records, and communication."],
    ["2. Data Privacy & Compliance", "All personal and clinical data is handled per applicable data-protection laws and used solely for clinical-trial management."],
    ["3. Data Security", "We use encryption at rest and in transit. You are responsible for keeping your credentials confidential."],
    ["4. Audit & Compliance", "All actions are logged for audit and may be shared with authorized regulators upon request."],
  ];
  const version = doc?.version || "2.1";
  const effective = doc?.effective_date || "01 Jan 2025";
  const blocks: [string, string][] = doc ? doc.blocks.map(b => [b.heading, b.body]) : fallback;

  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Legal" title="Terms & Conditions" />
      <ScrollView contentContainerStyle={p.body}>
        {loading && !doc ? (
          <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading latest document…</Small>
          </View>
        ) : null}
        {error && !doc ? (
          <View style={[p.warn, { marginBottom: spacing.md }]}>
            <AlertTriangle size={14} color={colors.warning} />
            <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load the latest version. Showing a saved copy.</Small>
          </View>
        ) : null}
        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.md }}>
          <Eyebrow color={colors.mutedFg}>Version {version}</Eyebrow>
          <Small style={{ fontFamily: fonts.mono }}>Effective {effective}</Small>
        </View>
        {blocks.map(([hd, bd], i) => (
          <Rise key={hd} delay={40 + i * 60}>
            <View style={[p.card, { marginBottom: spacing.sm }]}>
              <Text style={{ fontFamily: fonts.heading, fontSize: 15, color: colors.foreground, marginBottom: 4 }}>{hd}</Text>
              <Small style={{ lineHeight: 20 }}>{bd}</Small>
            </View>
          </Rise>
        ))}
      </ScrollView>
    </View>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
//  HELP & SUPPORT  — GET /faq, GET/POST /support/tickets, GET /app/config
// ══════════════════════════════════════════════════════════════════════════════
const FAQ_FALLBACK = [
  { q: "How do I reset my password?", a: "Go to Account → Change Password, enter your current password, then set a new one that meets all the strength requirements." },
  { q: "How are patient visits scheduled?", a: "Visits are auto-calculated from the patient's baseline date using the trial's visit template, then reviewed by your team." },
  { q: "How do I report a protocol deviation?", a: "Open the relevant patient or visit record and use 'Report Deviation'. It's logged for audit and routed to the PI and sponsor." },
];

function Help() {
  const [view, setView] = useState<"menu" | "faq" | "contact" | "tickets">("menu");
  const [faqOpen, setFaqOpen] = useState<number | null>(null);
  const [faqs, setFaqs] = useState(FAQ_FALLBACK);
  const [contact, setContact] = useState({ category: "Login Issue", subject: "", description: "" });
  const [ticketSubmitted, setTicketSubmitted] = useState(false);
  const [lastTicketId, setLastTicketId] = useState("");
  const [tickets, setTickets] = useState<any[]>([]);
  const [support, setSupport] = useState({ email: "support@mytrialboard.app", phone: "1800-123-4567", hours: "Mon – Fri, 9:00 AM – 6:00 PM" });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [ticketError, setTicketError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setLoadError(false);
      let failed = false;
      try { const fq = (await api.get("/faq")).data; if (!cancelled && Array.isArray(fq) && fq.length) setFaqs(fq); } catch { failed = true; }
      try { const tk = (await api.get("/support/tickets")).data; if (!cancelled) setTickets(tk); } catch { failed = true; }
      try {
        const cfg = (await api.get("/app/config")).data || {};
        if (!cancelled) setSupport(s => ({ email: cfg.support_email || s.email, phone: cfg.support_phone || s.phone, hours: cfg.support_hours || s.hours }));
      } catch {}
      if (!cancelled) { setLoadError(failed); setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  const submitTicket = async () => {
    setSubmitting(true); setTicketError("");
    try {
      const r = await api.post("/support/tickets", contact);
      setLastTicketId(r.data.ticket_id || r.data.id);
      setTicketSubmitted(true);
      setContact({ category: "Login Issue", subject: "", description: "" });
      try { setTickets((await api.get("/support/tickets")).data); } catch {}
    } catch (e: any) {
      setTicketError(e?.response?.data?.detail || "Couldn't submit your ticket. Please try again.");
    } finally { setSubmitting(false); }
  };

  const statusTone = (s: string) => s === "Resolved" ? colors.success : s === "In Progress" ? colors.warning : colors.info;

  // ── FAQ ──
  if (view === "faq") {
    return (
      <View style={p.container}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <SubHeader eyebrow="Help & support" title="FAQ" onBack={() => setView("menu")} />
        <ScrollView contentContainerStyle={p.body}>
          {loading ? (
            <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Small color={colors.mutedFg}>Loading help articles…</Small>
            </View>
          ) : null}
          {loadError ? (
            <View style={[p.warn, { marginBottom: spacing.md }]}>
              <AlertTriangle size={14} color={colors.warning} />
              <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load the latest content. Showing saved help.</Small>
            </View>
          ) : null}
          {faqs.map((f, i) => {
            const open = faqOpen === i;
            return (
              <Rise key={i} delay={40 + i * 60}>
                <View style={[p.card, { marginBottom: spacing.sm, borderColor: open ? colors.primary + "66" : colors.border }]}>
                  <Pressable onPress={() => setFaqOpen(open ? null : i)} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                    <Body weight="600" color={open ? colors.primary : colors.foreground} style={{ flex: 1 }}>{f.q}</Body>
                    <ChevronDown size={18} color={open ? colors.primary : colors.mutedFg} style={{ transform: [{ rotate: open ? "180deg" : "0deg" }] }} />
                  </Pressable>
                  {open && <Small style={{ marginTop: 10, lineHeight: 20 }}>{f.a}</Small>}
                </View>
              </Rise>
            );
          })}
        </ScrollView>
      </View>
    );
  }

  // ── Contact support ──
  if (view === "contact") {
    if (ticketSubmitted) {
      return (
        <View style={p.container}>
          <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
          <SubHeader eyebrow="Help & support" title="Contact Support" onBack={() => { setTicketSubmitted(false); setView("menu"); }} />
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md }}>
            <View style={p.successCircle}><Check size={32} color={colors.success} /></View>
            <Text style={{ fontFamily: fonts.heading, fontSize: 20, color: colors.foreground }}>Ticket Submitted!</Text>
            <Small style={{ textAlign: "center" }}>We'll respond within 24 hours.</Small>
            <View style={p.ticketIdBox}><Eyebrow color={colors.mutedFg}>Ticket ID</Eyebrow><Text style={{ fontFamily: fonts.mono, color: colors.primaryDeep, marginTop: 2 }}>{lastTicketId}</Text></View>
            <Pressable onPress={() => setView("tickets")}><Small color={colors.info} weight="700">View my tickets →</Small></Pressable>
          </View>
        </View>
      );
    }
    return (
      <View style={p.container}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <SubHeader eyebrow="Help & support" title="Contact Support" onBack={() => setView("menu")} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
            <View style={p.card}>
              <Field label="Issue Category">
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                  {["Login Issue", "Notification Problem", "App Bug", "Visit Query", "Other"].map(c => {
                    const on = contact.category === c;
                    return <Springy key={c} onPress={() => setContact({ ...contact, category: c })} style={[p.chip, on ? { backgroundColor: colors.primary } : { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{c}</Small></Springy>;
                  })}
                </View>
              </Field>
              <Field label="Subject"><TextInput value={contact.subject} onChangeText={v => setContact({ ...contact, subject: v })} placeholder="Brief subject" placeholderTextColor={colors.mutedFg + "99"} style={p.input} /></Field>
              <Field label="Description"><TextInput value={contact.description} onChangeText={v => setContact({ ...contact, description: v })} placeholder="Describe your issue…" placeholderTextColor={colors.mutedFg + "99"} multiline style={[p.input, { height: 110, textAlignVertical: "top" }]} /></Field>
            </View>
            {ticketError ? <Small color={colors.destructive} style={{ marginTop: spacing.md }}>{ticketError}</Small> : null}
          </ScrollView>
          <View style={p.footer}>
            <Springy onPress={submitTicket} disabled={submitting} style={[p.cta, { backgroundColor: colors.primaryDeep }]}><Text style={p.ctaText}>{submitting ? "Submitting…" : "Submit Ticket"}</Text></Springy>
          </View>
        </KeyboardAvoidingView>
      </View>
    );
  }

  // ── Tickets ──
  if (view === "tickets") {
    return (
      <View style={p.container}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <SubHeader eyebrow="Help & support" title="My Tickets" onBack={() => setView("menu")} />
        <ScrollView contentContainerStyle={p.body}>
          {loading ? (
            <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Small color={colors.mutedFg}>Loading your tickets…</Small>
            </View>
          ) : null}
          {loadError ? (
            <View style={[p.warn, { marginBottom: spacing.md }]}>
              <AlertTriangle size={14} color={colors.warning} />
              <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Couldn't load your tickets. Pull back later to retry.</Small>
            </View>
          ) : null}
          {tickets.length === 0 ? (
            <View style={[p.card, { alignItems: "center", paddingVertical: 32, gap: 8 }]}><Ticket size={32} color={colors.mutedFg + "66"} /><Small>You haven't raised any tickets yet.</Small></View>
          ) : tickets.map((t, i) => (
            <Rise key={t.id || i} delay={40 + i * 60}>
              <View style={[p.card, { marginBottom: spacing.sm }]}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <Text style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.primaryDeep, fontWeight: "700" }}>{t.ticket_id || t.id}</Text>
                  <View style={[p.statusTag, { backgroundColor: statusTone(t.status) + "22" }]}><Small weight="700" color={statusTone(t.status)} style={{ fontSize: 10 }}>{t.status}</Small></View>
                </View>
                <Body weight="600">{t.subject}</Body>
                <Small style={{ marginTop: 2 }}>{t.category}{t.created_at ? ` · ${new Date(t.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}` : ""}</Small>
              </View>
            </Rise>
          ))}
        </ScrollView>
        <View style={p.footer}>
          <Springy onPress={() => { setTicketSubmitted(false); setView("contact"); }} style={[p.cta, { backgroundColor: colors.primaryDeep, flexDirection: "row", gap: 8 }]}><MessageCircle size={16} color={colors.primaryFg} /><Text style={p.ctaText}>Raise New Ticket</Text></Springy>
        </View>
      </View>
    );
  }

  // ── Menu ──
  const items = [
    { icon: HelpCircle, tint: colors.info, label: "Frequently Asked Questions", sub: "Browse common questions", go: () => setView("faq") },
    { icon: MessageCircle, tint: colors.success, label: "Contact Support", sub: "Get help from our team", go: () => { setTicketSubmitted(false); setView("contact"); } },
    { icon: Ticket, tint: colors.violet, label: "My Tickets", sub: "Track your raised tickets", go: () => setView("tickets") },
  ];
  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Reports & support" title="Help & Support" />
      <ScrollView contentContainerStyle={p.body}>
        {items.map((it, i) => (
          <Rise key={it.label} delay={40 + i * 60}>
            <Springy onPress={it.go} style={[p.card, { marginBottom: spacing.sm, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <View style={[p.iconTile, { backgroundColor: it.tint + "1A" }]}><it.icon size={20} color={it.tint} /></View>
                <View><Body weight="600">{it.label}</Body><Small>{it.sub}</Small></View>
              </View>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Springy>
          </Rise>
        ))}
        <View style={[p.card, { marginTop: spacing.sm }]}>
          <Eyebrow color={colors.primary} style={{ marginBottom: 12 }}>Contact Us</Eyebrow>
          <View style={{ gap: 10 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><View style={[p.iconSm, { backgroundColor: colors.info + "1A" }]}><Mail size={15} color={colors.info} /></View><Small>{support.email}</Small></View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><View style={[p.iconSm, { backgroundColor: colors.success + "1A" }]}><Phone size={15} color={colors.success} /></View><Small>{support.phone}</Small></View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><View style={[p.iconSm, { backgroundColor: colors.warning + "1A" }]}><Clock size={15} color={colors.warning} /></View><Small>{support.hours}</Small></View>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

// ── Unknown slug fallback ─────────────────────────────────────────────────────
function NotFound() {
  const router = useRouter();
  return (
    <View style={p.container}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SubHeader eyebrow="Profile" title="Not found" />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md }}>
        <FlaskConical size={28} color={colors.mutedFg} />
        <Small style={{ textAlign: "center" }}>This section isn't available.</Small>
        <Springy onPress={() => router.back()} style={[p.cta, { backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.xl }]}><Text style={p.ctaText}>Go back</Text></Springy>
      </View>
    </View>
  );
}

const ORG_TYPE_LABEL: Record<string, string> = { sponsor: "Sponsor", cro: "CRO", smo: "SMO", site: "Site / Hospital" };

// ══════════════════════════════════════════════════════════════════════════════
//  Router — picks the sub-screen from the [slug] segment
// ══════════════════════════════════════════════════════════════════════════════
export default function ProfileSubScreen() {
  const params = useLocalSearchParams<{ slug?: string }>();
  const slug = String(params.slug || "edit");
  switch (slug) {
    case "edit": return <EditProfile />;
    case "entity-change": return <EntityChange />;
    case "change-password": return <ChangePassword />;
    case "notifications": return <Notifications />;
    case "reports": return <Reports />;
    case "tnc": return <Tnc />;
    case "help": return <Help />;
    default: return <NotFound />;
  }
}

const h = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.md, paddingTop: 6, paddingBottom: 14 },
  back: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  eyebrow: { color: colors.overlay25, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4 },
  title: { color: colors.primaryFg, fontFamily: fonts.display, fontSize: 20, letterSpacing: -0.4 },
  rightBtn: { backgroundColor: colors.overlay20, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 7 },
});

const p = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.md, paddingBottom: spacing.xxl },
  card: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md },
  iconTile: { width: 40, height: 40, borderRadius: radii.md, alignItems: "center", justifyContent: "center" },
  iconCircle: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  iconSm: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center" },
  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: colors.mutedFg, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6 },
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  prefix: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  warn: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 8, backgroundColor: colors.warning + "14", borderWidth: 1, borderColor: colors.warning + "33", borderRadius: radii.md, padding: 10 },
  linkBtn: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border + "99" },
  chip: { paddingHorizontal: 12, paddingVertical: 10, borderRadius: 999, alignItems: "center" },
  selectRow: { flexDirection: "row", alignItems: "center", gap: 12, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, backgroundColor: colors.card },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.primary },
  eye: { position: "absolute", right: 12, top: 0, bottom: 0, justifyContent: "center" },
  ruleDot: { width: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  successCircle: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" },
  ticketIdBox: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 20, paddingVertical: 12, alignItems: "center" },
  statusTag: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  footer: { paddingHorizontal: spacing.md, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  ctaText: { fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg },
  dialogOverlay: { flex: 1, backgroundColor: colors.black + "80", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  dialog: { backgroundColor: colors.card, borderRadius: 28, padding: spacing.lg, width: "100%", maxWidth: 320 },
  dialogBtn: { flex: 1, paddingVertical: 12, borderRadius: radii.md, alignItems: "center" },
});
