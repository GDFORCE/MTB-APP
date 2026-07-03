import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform, NativeSyntheticEvent, NativeScrollEvent } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, X, ClipboardCheck, ShieldCheck, FileText, Building2, UserPlus, Mail, Phone, ChevronDown } from "lucide-react-native";
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
function initFields(variant: string): Record<string, string> {
  switch (variant) {
    case "site": return { fullName: "Dr. Rajesh Kumar", designation: "Principal Investigator", email: "r.kumar@apollo.com", phone: "98100 12345", orgName: "Apollo Hospitals Mumbai", orgAddress: "", hospitalType: "Private", role: "PI", department: "" };
    case "smo": return { fullName: "Dr. Rajesh Kumar", designation: "SMO Manager", email: "r.kumar@smo.com", phone: "98100 12345", orgName: "MedSites SMO Pvt Ltd", orgAddress: "" };
    case "patient": return { fullName: "Priya Kapoor", phone: "98765 43210", email: "", dob: "1985-06-15", gender: "", language: "English" };
    default: return { fullName: "John Doe", designation: "Clinical Research Manager", email: "john.doe@pharmaco.com", phone: "98765 43210", orgName: "PharmaCo Ltd", orgAddress: "21 Business Park, Mumbai 400001" };
  }
}

// Responsibilities shown before an organization fills the form.
const registrationInstructions = [
  "The Authorized Signatory / Responsible person of the organization should fill this form.",
  "All fields marked with an asterisk (*) are mandatory.",
  "After submitting, check your registered email for verification before signing in.",
  "Uploaded documents must be self-attested with the company stamp and seal.",
  "You are responsible for the accuracy and authenticity of all information provided.",
];

// Stand-in for the backend org directory. On Continue we check whether the org (by
// name) is already onboarded; if so we surface its admin, else offer to create it.
interface OrgAdmin { name: string; role: string; email: string; phone: string }
interface DirectoryEntry { name: string; admin: OrgAdmin }
const ORG_DIRECTORY: Record<string, DirectoryEntry[]> = {
  sponsor: [
    { name: "BioGen Research", admin: { name: "Dr. Anita Menon", role: "Sponsor Admin", email: "anita.menon@biogen.com", phone: "+91 98450 11223" } },
    { name: "Novartis India", admin: { name: "Mr. Rohan Mehta", role: "Sponsor Admin", email: "rohan.mehta@novartis.com", phone: "+91 98670 33445" } },
    { name: "Medpace CRO", admin: { name: "Ms. Kavya Reddy", role: "CRO Admin", email: "kavya.reddy@medpace.com", phone: "+91 99000 55678" } },
  ],
  site: [
    { name: "Fortis Bangalore", admin: { name: "Dr. Anand Krishnan", role: "Site Admin", email: "a.krishnan@fortishealthcare.com", phone: "+91 98300 34567" } },
    { name: "Max Healthcare Delhi", admin: { name: "Dr. Sunita Rao", role: "Site Admin", email: "s.rao@maxhealthcare.com", phone: "+91 98200 23456" } },
    { name: "Manipal Hospital", admin: { name: "Dr. Vikram Shetty", role: "Site Admin", email: "v.shetty@manipalhospitals.com", phone: "+91 98860 77889" } },
  ],
  smo: [
    { name: "ClinOps SMO", admin: { name: "Mr. Sanjay Gupta", role: "SMO Admin", email: "sanjay.gupta@clinops.com", phone: "+91 99100 22334" } },
    { name: "TrialConnect SMO", admin: { name: "Ms. Deepa Nair", role: "SMO Admin", email: "deepa.nair@trialconnect.com", phone: "+91 99720 44556" } },
  ],
};
ORG_DIRECTORY.cro = ORG_DIRECTORY.sponsor;
function findOrg(entityType: string | undefined, name: string): DirectoryEntry | null {
  const list = ORG_DIRECTORY[entityType || ""] || [];
  const n = name.trim().toLowerCase();
  return n ? list.find((o) => o.name.toLowerCase() === n) || null : null;
}
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
  const { role } = useLocalSearchParams<{ role: string }>();
  const variant = variantFor(role);
  const isPatient = variant === "patient";
  const orgNoun = variant === "site" ? "site" : variant === "smo" ? "SMO" : "organization";

  const [fld, setFld] = useState<Record<string, string>>(() => initFields(variant));
  const up = (k: string) => (v: string) => setFld((s) => ({ ...s, [k]: v }));

  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [declarationAccepted, setDeclarationAccepted] = useState(false);
  const [showInstructions, setShowInstructions] = useState(() => !isPatient);
  const [showDeclaration, setShowDeclaration] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [termsScrolled, setTermsScrolled] = useState(false);
  const [orgCheck, setOrgCheck] = useState<"exists" | "create" | null>(null);
  const [err, setErr] = useState("");

  const canContinue = agreedToTerms && declarationAccepted;
  const matchedOrg = findOrg(role, fld.orgName || "");
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

  const handleContinue = () => {
    if (!canContinue || !validate()) return;
    if (isPatient) { proceed(); return; }
    setOrgCheck(findOrg(role, fld.orgName || "") ? "exists" : "create");
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
                <Field label={variant === "smo" ? "SMO Name" : "Organization Name"} required><Input value={fld.orgName} onChangeText={up("orgName")} /></Field>
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
          <Springy testID="register-submit-button" onPress={handleContinue} disabled={!canContinue} style={[f.cta, canContinue ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: canContinue ? colors.primaryFg : colors.mutedFg }}>Continue</Text>
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
        <ScrollView style={{ maxHeight: 280 }} onScroll={onTermsScroll} scrollEventThrottle={16} nestedScrollEnabled showsVerticalScrollIndicator persistentScrollbar keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: spacing.lg, gap: spacing.md, paddingBottom: 8 }}>
          {TERMS.map(([h, b]) => (
            <View key={h} style={{ gap: 4 }}>
              <Text style={{ fontFamily: fonts.heading, fontSize: 15, color: colors.foreground }}>{h}</Text>
              <Small style={{ lineHeight: 20 }}>{b}</Small>
            </View>
          ))}
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
                  <Eyebrow color={colors.mutedFg} style={{ marginBottom: 10 }}>{orgNoun} admin</Eyebrow>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={f.adminAvatar}><Text style={{ fontFamily: fonts.bold, color: colors.primary, fontSize: 14 }}>{adminInitials(matchedOrg.admin.name)}</Text></View>
                    <View style={{ flex: 1 }}>
                      <Body weight="700" style={{ fontSize: 14 }}>{matchedOrg.admin.name}</Body>
                      <Small>{matchedOrg.admin.role}</Small>
                    </View>
                  </View>
                  <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Mail size={15} color={colors.mutedFg} /><Small>{matchedOrg.admin.email}</Small></View>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Phone size={15} color={colors.mutedFg} /><Small>{matchedOrg.admin.phone}</Small></View>
                  </View>
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

const TERMS: [string, string][] = [
  ["1. Acceptance of Terms", "By registering, you agree to be bound by these Terms and Conditions and our Privacy Policy. If you do not agree, do not proceed with registration."],
  ["2. Data Privacy & PDPA Compliance", "All personal and clinical data collected is handled in accordance with applicable data protection laws. Your data will be used solely for the purposes of clinical trial management and communications related to your participation."],
  ["3. Data Security", "We employ industry-standard security measures including encryption at rest and in transit. You are responsible for maintaining the confidentiality of your account credentials."],
  ["4. Use of Platform", "Access is granted strictly for clinical trial management purposes. Any misuse, sharing of credentials, or unauthorized access is prohibited and may result in immediate account termination."],
  ["5. Audit & Compliance", "All actions performed on the platform are logged for audit and regulatory compliance purposes. These logs may be shared with authorized regulators upon request."],
  ["6. Consent for Communications", "By registering, you consent to receive communications related to your trial participation including visit reminders, medication alerts, and important protocol updates."],
  ["7. Contact & Support", "For any questions regarding these terms, contact support@mtb-pvs.com. By scrolling through and tapping Accept, you confirm you have read and understood all terms above in full."],
];

const f = StyleSheet.create({
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  prefix: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  segment: { flexDirection: "row", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, borderRadius: radii.md, padding: 4, gap: 4 },
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
