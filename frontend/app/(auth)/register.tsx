import React, { useState, useEffect, useRef } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform, ActivityIndicator, NativeSyntheticEvent, NativeScrollEvent } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, ClipboardCheck, ShieldCheck, FileText, Building2, UserPlus, Mail, Phone, ChevronDown } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";

// ── Role → header label + which form variant to render ──────────────────────
const labelMap: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO",
  site: "Site / Hospital", pi: "Site / Hospital", crc: "Site / Hospital",
  patient: "Patient",
};
function variantFor(role?: string): "sponsor" | "site" | "smo" | "patient" {
  if (role === "smo") return "smo";
  if (role === "site" || role === "pi" || role === "crc") return "site";
  if (role === "patient") return "patient";
  return "sponsor";
}
// Demo prefills — convenient while developing, but must NOT ship in production
// builds. `initFields` returns these only under __DEV__; otherwise empty fields.
const DEMO_FIELDS: Record<string, Record<string, string>> = {
  site: { fullName: "Dr. Rajesh Kumar", designation: "Principal Investigator", email: "r.kumar@apollo.com", phone: "98100 12345", orgName: "Apollo Hospitals Mumbai", orgAddress: "", hospitalType: "Private", role: "PI", department: "" },
  smo: { fullName: "Dr. Rajesh Kumar", designation: "SMO Manager", email: "r.kumar@smo.com", phone: "98100 12345", orgName: "MedSites SMO Pvt Ltd", orgAddress: "" },
  patient: { fullName: "Priya Kapoor", phone: "98765 43210", email: "", dob: "1985-06-15", gender: "", language: "English" },
  sponsor: { fullName: "John Doe", designation: "Clinical Research Manager", email: "john.doe@pharmaco.com", phone: "98765 43210", orgName: "PharmaCo Ltd", orgAddress: "21 Business Park, Mumbai 400001" },
};
function initFields(variant: string): Record<string, string> {
  const shape = DEMO_FIELDS[variant] || DEMO_FIELDS.sponsor;
  if (__DEV__) return { ...shape };
  // Production: same field keys, but blank — no demo data leaks into the build.
  const empty: Record<string, string> = {};
  for (const k of Object.keys(shape)) empty[k] = k === "hospitalType" ? "Private" : k === "role" ? "PI" : "";
  return empty;
}

// Responsibilities shown before an organization fills the form.
const registrationInstructions = [
  "The Authorized Signatory / Responsible person of the organization should fill this form.",
  "All fields marked with an asterisk (*) are mandatory.",
  "After submitting, check your registered email for verification before signing in.",
  "Uploaded documents must be self-attested with the company stamp and seal.",
  "You are responsible for the accuracy and authenticity of all information provided.",
];

// An organization from GET /api/organizations. On Continue we check whether the
// typed org name already matches an onboarded org; if so we surface its contact,
// else we offer to create it (the org auto-creates server-side at registration).
interface Org { id: string; name: string; type: string; address?: string; contact?: string; email?: string; website?: string; status?: string }
// Map the selected role to the org `type` used to narrow the directory search.
function orgTypeFor(role?: string): string | undefined {
  if (role === "sponsor") return "sponsor";
  if (role === "cro") return "cro";
  if (role === "smo") return "smo";
  if (role === "site" || role === "pi" || role === "crc") return "site";
  return undefined;
}
const orgTypeLabels: Record<string, string> = { sponsor: "Sponsor", cro: "CRO", smo: "SMO", site: "Site / Hospital" };
function adminInitials(name: string) {
  const parts = name.replace(/^(Dr\.|Mr\.|Ms\.|Mrs\.)\s+/i, "").trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase();
}
function ageFromDob(dob: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dob.trim());
  if (!m) return "";
  const b = new Date(+m[1], +m[2] - 1, +m[3]);
  if (isNaN(b.getTime())) return "";
  const now = new Date();
  let a = now.getFullYear() - b.getFullYear();
  const md = now.getMonth() - b.getMonth();
  if (md < 0 || (md === 0 && now.getDate() < b.getDate())) a--;
  return a >= 0 && a < 130 ? `${a} yrs` : "";
}

// ── Shared primitives ───────────────────────────────────────────────────────
function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={f.label}>{label}{required && <Text style={{ color: colors.accent }}> *</Text>}</Text>
      {children}
    </View>
  );
}
function Input(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput placeholderTextColor={colors.mutedFg + "99"} {...props} style={[f.input, props.style]} />;
}
function PhoneInput({ value, onChangeText }: { value: string; onChangeText: (v: string) => void }) {
  return (
    <View style={{ flexDirection: "row", gap: 8 }}>
      <View style={f.prefix}><Text style={{ color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 15 }}>+91</Text></View>
      <Input value={value} onChangeText={onChangeText} keyboardType="phone-pad" placeholder="98XXXXXXXX" style={{ flex: 1 }} />
    </View>
  );
}
function SectionRow({ title }: { title: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, paddingTop: spacing.sm, marginBottom: spacing.md }}>
      <Eyebrow>{title}</Eyebrow>
      <View style={{ flex: 1, height: 1, backgroundColor: colors.border }} />
    </View>
  );
}
function SegmentToggle({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={f.segment}>
      {options.map((opt) => {
        const on = value === opt;
        return (
          <Pressable key={opt} onPress={() => onChange(opt)} style={[f.segmentBtn, on && { backgroundColor: colors.primary }]}>
            <Text style={{ fontSize: 14, fontFamily: fonts.semibold, color: on ? colors.primaryFg : colors.mutedFg }}>{opt}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}
function Select({ value, placeholder, options, onChange }: { value: string; placeholder: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} style={[f.input, { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
        <Text style={{ fontSize: 15, color: value ? colors.foreground : colors.mutedFg + "99", fontFamily: fonts.regular }}>{value || placeholder}</Text>
        <ChevronDown size={18} color={colors.mutedFg} />
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={f.selectOverlay} onPress={() => setOpen(false)}>
          <View style={f.selectSheet}>
            {options.map((o) => {
              const on = o === value;
              return (
                <Pressable key={o} onPress={() => { onChange(o); setOpen(false); }} style={[f.selectItem, on && { backgroundColor: colors.secondary + "55" }]}>
                  <Text style={{ fontSize: 15, color: on ? colors.primary : colors.foreground, fontFamily: on ? fonts.semibold : fonts.regular }}>{o}</Text>
                  {on && <Check size={16} color={colors.primary} strokeWidth={3} />}
                </Pressable>
              );
            })}
          </View>
        </Pressable>
      </Modal>
    </>
  );
}

// Centered modal card — the shared shell for responsibilities / declaration / terms / org-check.
function ModalCard({ visible, onClose, children }: { visible: boolean; onClose: () => void; children: React.ReactNode }) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={f.cardOverlay}>
        {/* Full-screen backdrop catches taps to dismiss; the card sits above it as a
            plain View so the inner ScrollView keeps its scroll gesture (Android). */}
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={f.card}>
          <Rise delay={0} distance={12} duration={320}>{children}</Rise>
        </View>
      </View>
    </Modal>
  );
}
function ModalHead({ icon, eyebrow, title, subtitle }: { icon: React.ReactNode; eyebrow: string; title: string; subtitle?: string }) {
  return (
    <View style={{ paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.md, alignItems: "center" }}>
      <View style={f.iconBadge}>{icon}</View>
      <Eyebrow color={colors.accent} style={{ marginBottom: 4 }}>{eyebrow}</Eyebrow>
      <Text style={{ fontFamily: fonts.heading, fontSize: 20, color: colors.foreground, textAlign: "center" }}>{title}</Text>
      {subtitle ? <Small style={{ marginTop: 6, textAlign: "center", lineHeight: 20 }}>{subtitle}</Small> : null}
    </View>
  );
}

export default function Register() {
  const router = useRouter();
  // `org`/`email` may arrive prefilled when the user came from an accepted invite.
  const { role, org: orgParam, email: emailParam } = useLocalSearchParams<{ role: string; org?: string; email?: string }>();
  const variant = variantFor(role);
  const isPatient = variant === "patient";
  const orgNoun = variant === "site" ? "site" : variant === "smo" ? "SMO" : "organization";

  const [fld, setFld] = useState<Record<string, string>>(() => {
    const base = initFields(variant);
    // Invite prefills are real user data (not demo defaults) — always apply them.
    if (orgParam) base.orgName = String(orgParam);
    if (emailParam) base.email = String(emailParam);
    return base;
  });
  const up = (k: string) => (v: string) => setFld((s) => ({ ...s, [k]: v }));

  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [declarationAccepted, setDeclarationAccepted] = useState(false);
  const [showInstructions, setShowInstructions] = useState(() => !isPatient);
  const [showDeclaration, setShowDeclaration] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [termsScrolled, setTermsScrolled] = useState(false);
  const [orgCheck, setOrgCheck] = useState<"exists" | "create" | null>(null);
  // True while Continue performs a fresh, awaited org lookup because the debounced
  // results are still in-flight or stale for the currently typed name.
  const [checkingOrg, setCheckingOrg] = useState(false);
  const [err, setErr] = useState("");

  // ── Live org directory lookup (debounced) ─────────────────────────────────
  const [orgMatches, setOrgMatches] = useState<Org[]>([]);
  const [orgSearching, setOrgSearching] = useState(false);
  const [showOrgSuggestions, setShowOrgSuggestions] = useState(false);
  // The exact (trimmed) query the current `orgMatches` were fetched for. Used by
  // Continue to detect when the latest results are stale vs. the typed org name.
  const lastSearchedQuery = useRef("");
  useEffect(() => {
    if (isPatient) return;
    const q = (fld.orgName || "").trim();
    if (q.length < 2) { setOrgMatches([]); setOrgSearching(false); return; }
    setOrgSearching(true);
    // Stale-response guard: a slow in-flight response for an older query must not
    // overwrite newer results. Cleanup flips `ignore` so late responses are dropped.
    let ignore = false;
    const t = setTimeout(async () => {
      try {
        const params: Record<string, string> = { search: q };
        const type = orgTypeFor(role);
        if (type) params.type = type;
        const res = await api.get("/organizations", { params });
        if (ignore) return;
        setOrgMatches(Array.isArray(res.data) ? res.data : []);
        lastSearchedQuery.current = q;
      } catch {
        if (!ignore) setOrgMatches([]);
      } finally {
        if (!ignore) setOrgSearching(false);
      }
    }, 300);
    return () => { ignore = true; clearTimeout(t); };
  }, [fld.orgName, role, isPatient]);

  // ── Terms & Conditions content (fetched when the modal opens) ─────────────
  const [termsBlocks, setTermsBlocks] = useState<{ heading: string; body: string }[] | null>(null);
  const [termsLoading, setTermsLoading] = useState(false);
  const [termsError, setTermsError] = useState("");
  useEffect(() => {
    if (!showTerms || termsBlocks || termsLoading) return;
    setTermsLoading(true);
    setTermsError("");
    api.get("/legal/terms")
      .then((res) => setTermsBlocks(Array.isArray(res.data?.blocks) ? res.data.blocks : []))
      .catch(() => setTermsError("Unable to load the latest terms right now. Please check your connection and try again."))
      .finally(() => setTermsLoading(false));
  }, [showTerms, termsBlocks, termsLoading]);

  const canContinue = agreedToTerms && declarationAccepted;
  const matchedOrg = orgMatches.find((o) => o.name.trim().toLowerCase() === (fld.orgName || "").trim().toLowerCase()) || null;
  const declarationText = isPatient
    ? "I confirm that the information I have provided is true, accurate and complete, and I agree to comply with the platform's Terms of Use and Privacy Policy."
    : "I confirm that I am authorized to register and represent this organization, that the information provided is accurate, and I agree to comply with the platform's Terms of Use and Privacy Policy.";

  const onTermsScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 8) setTermsScrolled(true);
  };

  // Validate, then route: patients go straight on; orgs hit the directory prompt first.
  const validate = (): boolean => {
    setErr("");
    const email = (fld.email || "").trim().toLowerCase();
    const phone = (fld.phone || "").trim();
    if (!fld.fullName?.trim()) { setErr("Please enter your full name"); return false; }
    if (isPatient) {
      if (!phone) { setErr("Phone number is required"); return false; }
    } else {
      if (!email || !email.includes("@")) { setErr("A valid email is required"); return false; }
      if (!phone) { setErr("Phone number is required"); return false; }
      if (!fld.orgName?.trim()) { setErr("Organization name is required"); return false; }
    }
    return true;
  };

  const proceed = () => {
    router.push({
      pathname: "/(auth)/security-questions",
      params: { role: role || "patient", variant, payload: JSON.stringify({ ...fld, email: (fld.email || "").trim().toLowerCase(), phone: (fld.phone || "").trim() }) },
    });
  };

  const handleContinue = async () => {
    if (!canContinue || !validate() || checkingOrg) return;
    if (isPatient) { proceed(); return; }
    setShowOrgSuggestions(false);
    const q = (fld.orgName || "").trim();

    // The exists-vs-create decision must not read stale/empty debounced results.
    // If a search is still pending/in-flight for this name, or the latest results
    // were fetched for a different name, do a fresh awaited lookup before deciding.
    if (orgSearching || lastSearchedQuery.current.toLowerCase() !== q.toLowerCase()) {
      setCheckingOrg(true);
      try {
        const params: Record<string, string> = { search: q };
        const type = orgTypeFor(role);
        if (type) params.type = type;
        const res = await api.get("/organizations", { params });
        const list: Org[] = Array.isArray(res.data) ? res.data : [];
        setOrgMatches(list);
        lastSearchedQuery.current = q;
        setOrgSearching(false);
        const fresh = list.find((o) => o.name.trim().toLowerCase() === q.toLowerCase()) || null;
        setOrgCheck(fresh ? "exists" : "create");
      } catch {
        // Lookup failed — keep the non-blocking free-text path (offer to create).
        setOrgCheck("create");
      } finally {
        setCheckingOrg(false);
      }
      return;
    }

    // Latest debounced results are for the current name — trust the derived match.
    setOrgCheck(matchedOrg ? "exists" : "create");
  };

  const onOrgNameChange = (v: string) => { setFld((s) => ({ ...s, orgName: v })); setShowOrgSuggestions(true); };
  const pickOrg = (o: Org) => {
    setFld((s) => ({ ...s, orgName: o.name, orgAddress: o.address || s.orgAddress }));
    setShowOrgSuggestions(false);
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow={`Step 2 of 5 · ${labelMap[role as string] || "Sponsor"}`} title="Tell us about you" onBack={() => router.back()} step={2} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Rise delay={200}>
            {/* Common identity fields */}
            <Field label="Full Name" required><Input value={fld.fullName} onChangeText={up("fullName")} /></Field>
            {!isPatient && <Field label="Designation" required><Input value={fld.designation} onChangeText={up("designation")} /></Field>}

            {isPatient ? (
              <>
                <Field label="Phone Number" required><PhoneInput value={fld.phone} onChangeText={up("phone")} /></Field>
                <Field label="Email ID" required><Input value={fld.email} onChangeText={up("email")} keyboardType="email-address" autoCapitalize="none" placeholder="patient@example.com" /></Field>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={{ flex: 1 }}>
                    <Field label="Date of Birth" required><Input value={fld.dob} onChangeText={up("dob")} placeholder="YYYY-MM-DD" /></Field>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Field label="Age">
                      <View style={[f.input, { justifyContent: "center", backgroundColor: colors.surface }]}>
                        <Text style={{ color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 15 }}>{ageFromDob(fld.dob) || "—"}</Text>
                      </View>
                    </Field>
                  </View>
                </View>
                <Field label="Gender" required>
                  <Select value={fld.gender} placeholder="Select gender" options={["Female", "Male", "Other", "Prefer not to say"]} onChange={up("gender")} />
                </Field>
                <Field label="Preferred Language">
                  <Select value={fld.language} placeholder="Select language" options={["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"]} onChange={up("language")} />
                </Field>
              </>
            ) : (
              <>
                <Field label="Email ID" required><Input value={fld.email} onChangeText={up("email")} keyboardType="email-address" autoCapitalize="none" /></Field>
                <Field label="Phone Number" required><PhoneInput value={fld.phone} onChangeText={up("phone")} /></Field>

                <SectionRow title="Organization" />
                <Field label={variant === "smo" ? "SMO Name" : "Organization Name"} required>
                  <Input value={fld.orgName} onChangeText={onOrgNameChange} onFocus={() => setShowOrgSuggestions(true)} placeholder={`Search or type your ${orgNoun} name`} />
                  {showOrgSuggestions && (fld.orgName || "").trim().length >= 2 && !matchedOrg && (orgSearching || orgMatches.length > 0) && (
                    <View style={f.suggestBox}>
                      {orgSearching && orgMatches.length === 0 ? (
                        <View style={f.suggestRow}>
                          <ActivityIndicator size="small" color={colors.primary} />
                          <Small style={{ marginLeft: 8 }}>Searching…</Small>
                        </View>
                      ) : (
                        orgMatches.slice(0, 6).map((o) => (
                          <Pressable key={o.id} onPress={() => pickOrg(o)} style={f.suggestRow}>
                            <View style={f.suggestIcon}><Building2 size={15} color={colors.primary} /></View>
                            <View style={{ flex: 1 }}>
                              <Body weight="700" style={{ fontSize: 14 }}>{o.name}</Body>
                              {o.address ? <Small numberOfLines={1}>{o.address}</Small> : null}
                            </View>
                          </Pressable>
                        ))
                      )}
                    </View>
                  )}
                </Field>
                <Field label={variant === "smo" ? "SMO Address" : "Organization Address"} required>
                  <Input value={fld.orgAddress} onChangeText={up("orgAddress")} multiline placeholder="Building / Street, City, State, PIN" style={{ height: 64, textAlignVertical: "top" }} />
                </Field>

                {variant === "site" && (
                  <>
                    <Field label="Hospital Type" required><SegmentToggle options={["Private", "Government"]} value={fld.hospitalType} onChange={up("hospitalType")} /></Field>
                    <Field label="Role" required><SegmentToggle options={["PI", "Research Team"]} value={fld.role} onChange={up("role")} /></Field>
                    {fld.role === "PI" && <Field label="Department"><Input value={fld.department} onChangeText={up("department")} placeholder="e.g. Oncology, Cardiology" /></Field>}
                  </>
                )}
              </>
            )}

            {/* Declaration checkbox */}
            <Pressable onPress={() => (declarationAccepted ? setDeclarationAccepted(false) : setShowDeclaration(true))} style={f.checkRow}>
              <View style={[f.checkbox, declarationAccepted && { backgroundColor: colors.primary, borderColor: colors.primary }]}>
                {declarationAccepted && <Check size={12} color={colors.primaryFg} strokeWidth={3} />}
              </View>
              <Body color={colors.mutedFg} style={{ flex: 1, fontSize: 14, lineHeight: 20 }}>
                I have read and accept the <Text style={{ color: colors.primary, fontFamily: fonts.semibold, textDecorationLine: "underline" }}>Declaration</Text>
              </Body>
            </Pressable>

            {/* Terms checkbox */}
            <Pressable onPress={() => (agreedToTerms ? (setAgreedToTerms(false), setTermsScrolled(false)) : setShowTerms(true))} style={f.checkRow}>
              <View style={[f.checkbox, agreedToTerms && { backgroundColor: colors.primary, borderColor: colors.primary }]}>
                {agreedToTerms && <Check size={12} color={colors.primaryFg} strokeWidth={3} />}
              </View>
              <Body color={colors.mutedFg} style={{ flex: 1, fontSize: 14, lineHeight: 20 }}>
                I have read and agree to the <Text style={{ color: colors.primary, fontFamily: fonts.semibold, textDecorationLine: "underline" }}>Terms & Conditions</Text> and <Text style={{ color: colors.primary, fontFamily: fonts.semibold, textDecorationLine: "underline" }}>Privacy Policy</Text>
              </Body>
            </Pressable>

            {err ? <Small color={colors.destructive} style={{ marginTop: 8 }}>{err}</Small> : null}
          </Rise>
        </ScrollView>

        {/* Footer */}
        <View style={f.footer}>
          <Springy testID="register-submit-button" onPress={handleContinue} disabled={!canContinue || checkingOrg} style={[f.cta, canContinue ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            {checkingOrg ? (
              <ActivityIndicator color={colors.primaryFg} />
            ) : (
              <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: canContinue ? colors.primaryFg : colors.mutedFg }}>Continue</Text>
            )}
          </Springy>
        </View>
      </KeyboardAvoidingView>

      {/* Responsibilities — shown on entry for organizations */}
      <ModalCard visible={showInstructions} onClose={() => setShowInstructions(false)}>
        <ModalHead
          icon={<ClipboardCheck size={24} color={colors.primary} />}
          eyebrow="Before you register"
          title="Your responsibilities"
          subtitle="Please read the points below. You are registering on behalf of your organization."
        />
        <View style={{ paddingHorizontal: spacing.lg }}>
          {registrationInstructions.map((text, i) => (
            <View key={i} style={{ flexDirection: "row", gap: 12, marginBottom: 12 }}>
              <View style={f.numBadge}><Text style={{ fontFamily: fonts.heading, fontSize: 13, color: colors.primary }}>{i + 1}</Text></View>
              <Small style={{ flex: 1, lineHeight: 20 }}>{text}</Small>
            </View>
          ))}
        </View>
        <View style={{ padding: spacing.lg }}>
          <Pressable onPress={() => setShowInstructions(false)} style={[f.cta, { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>Accept & Continue</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Declaration */}
      <ModalCard visible={showDeclaration} onClose={() => setShowDeclaration(false)}>
        <ModalHead icon={<ShieldCheck size={24} color={colors.primary} />} eyebrow="Please confirm" title="Declaration" subtitle="Read and confirm the statement below to continue." />
        <View style={{ paddingHorizontal: spacing.lg }}>
          <View style={f.quote}><Small color={colors.foreground} style={{ lineHeight: 21 }}>{declarationText}</Small></View>
        </View>
        <View style={{ padding: spacing.lg }}>
          <Pressable onPress={() => { setDeclarationAccepted(true); setShowDeclaration(false); }} style={[f.cta, { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>I Agree & Confirm</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Terms & Conditions */}
      <ModalCard visible={showTerms} onClose={() => setShowTerms(false)}>
        <ModalHead icon={<FileText size={24} color={colors.primary} />} eyebrow="Before you continue" title="Terms & Conditions" subtitle="Please scroll through and read all the terms below before accepting." />
        <View style={{ paddingHorizontal: spacing.lg, marginBottom: 8 }}>
          <View style={[f.banner, termsScrolled ? { backgroundColor: colors.success + "1A" } : { backgroundColor: colors.accent + "14" }]}>
            <Small color={termsScrolled ? colors.success : colors.accent} style={{ fontFamily: fonts.semibold, textAlign: "center" }}>
              {termsScrolled ? "✓ You have read all terms — you may now accept" : "↓ Scroll through all the terms to enable Accept"}
            </Small>
          </View>
        </View>
        <ScrollView style={{ maxHeight: 280 }} onScroll={onTermsScroll} scrollEventThrottle={16} onContentSizeChange={(_w, h) => { if (!termsLoading && h <= 280) setTermsScrolled(true); }} nestedScrollEnabled showsVerticalScrollIndicator persistentScrollbar keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: spacing.lg, gap: spacing.md, paddingBottom: 8 }}>
          {termsLoading ? (
            <View style={{ alignItems: "center", paddingVertical: spacing.lg, gap: 8 }}>
              <ActivityIndicator color={colors.primary} />
              <Small>Loading the latest terms…</Small>
            </View>
          ) : termsError ? (
            <Small color={colors.destructive} style={{ lineHeight: 20 }}>{termsError}</Small>
          ) : (
            (termsBlocks || []).map((blk, i) => (
              <View key={i} style={{ gap: 4 }}>
                <Text style={{ fontFamily: fonts.heading, fontSize: 15, color: colors.foreground }}>{blk.heading}</Text>
                <Small style={{ lineHeight: 20 }}>{blk.body}</Small>
              </View>
            ))
          )}
        </ScrollView>
        <View style={{ padding: spacing.lg }}>
          <Pressable disabled={!termsScrolled} onPress={() => { setAgreedToTerms(true); setShowTerms(false); }} style={[f.cta, termsScrolled ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: termsScrolled ? colors.primaryFg : colors.mutedFg }}>{termsScrolled ? "Accept & Continue" : "Scroll to read all terms"}</Text>
          </Pressable>
        </View>
      </ModalCard>

      {/* Org-existence prompt */}
      <ModalCard visible={orgCheck !== null} onClose={() => setOrgCheck(null)}>
        {orgCheck === "exists" ? (
          <>
            <ModalHead icon={<Building2 size={24} color={colors.primary} />} eyebrow="Already registered" title={`This ${orgNoun} is already on MTB`} subtitle={`${(fld.orgName || "").trim()} is already registered. Please contact its admin below to be added — they can send you an invite to join.`} />
            {matchedOrg && (
              <View style={{ paddingHorizontal: spacing.lg }}>
                <View style={f.adminCard}>
                  <Eyebrow color={colors.mutedFg} style={{ marginBottom: 10 }}>Registered {orgNoun}</Eyebrow>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={f.adminAvatar}><Text style={{ fontFamily: fonts.bold, color: colors.primary, fontSize: 14 }}>{adminInitials(matchedOrg.name)}</Text></View>
                    <View style={{ flex: 1 }}>
                      <Body weight="700" style={{ fontSize: 14 }}>{matchedOrg.name}</Body>
                      <Small>{orgTypeLabels[matchedOrg.type] || matchedOrg.type}</Small>
                    </View>
                  </View>
                  {(matchedOrg.email || matchedOrg.contact) && (
                    <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 }}>
                      {matchedOrg.email ? <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Mail size={15} color={colors.mutedFg} /><Small>{matchedOrg.email}</Small></View> : null}
                      {matchedOrg.contact ? <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Phone size={15} color={colors.mutedFg} /><Small>{matchedOrg.contact}</Small></View> : null}
                    </View>
                  )}
                </View>
              </View>
            )}
            <View style={{ padding: spacing.lg, gap: 8 }}>
              <Pressable onPress={() => setOrgCheck(null)} style={[f.cta, { backgroundColor: colors.primary }]}><Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>Okay, got it</Text></Pressable>
              <Pressable onPress={() => setOrgCheck(null)} style={f.ghostBtn}><Text style={{ fontFamily: fonts.semibold, fontSize: 14, color: colors.mutedFg }}>Use a different name</Text></Pressable>
            </View>
          </>
        ) : (
          <>
            <ModalHead icon={<UserPlus size={24} color={colors.primary} />} eyebrow={`New ${orgNoun}`} title={`Create this ${orgNoun}?`} subtitle={`${(fld.orgName || "").trim()} isn't in our system yet. Do you want to create it and join as its admin?`} />
            <View style={{ padding: spacing.lg, gap: 8 }}>
              <Pressable onPress={() => { setOrgCheck(null); proceed(); }} style={[f.cta, { backgroundColor: colors.primary }]}><Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>Create & join as admin</Text></Pressable>
              <Pressable onPress={() => setOrgCheck(null)} style={f.ghostBtn}><Text style={{ fontFamily: fonts.semibold, fontSize: 14, color: colors.mutedFg }}>Go back</Text></Pressable>
            </View>
          </>
        )}
      </ModalCard>
    </SafeAreaView>
  );
}

const f = StyleSheet.create({
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  prefix: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  segment: { flexDirection: "row", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, borderRadius: radii.md, padding: 4, gap: 4 },
  suggestBox: { marginTop: 6, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.card, overflow: "hidden" },
  suggestRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  suggestIcon: { width: 30, height: 30, borderRadius: 8, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  segmentBtn: { flex: 1, paddingVertical: 8, borderRadius: radii.sm, alignItems: "center", justifyContent: "center" },
  checkRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, marginTop: spacing.sm },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center", marginTop: 2 },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  ghostBtn: { paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  // Select dropdown
  selectOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", justifyContent: "center", paddingHorizontal: spacing.xl },
  selectSheet: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, overflow: "hidden", paddingVertical: 4 },
  selectItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 14 },
  // Centered modal card
  cardOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "80", alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.lg },
  card: { backgroundColor: colors.background, width: "100%", maxWidth: 400, borderRadius: 28, overflow: "hidden", ...({ shadowColor: "#2E1B33", shadowOpacity: 0.2, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 12 }) },
  iconBadge: { width: 48, height: 48, borderRadius: 16, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  numBadge: { width: 24, height: 24, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  quote: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  banner: { paddingHorizontal: spacing.md, paddingVertical: 8, borderRadius: radii.md },
  adminCard: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: 14 },
  adminAvatar: { width: 40, height: 40, borderRadius: 999, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
});
