import React, { useState, useEffect, useMemo, useRef } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform, ActivityIndicator, NativeSyntheticEvent, NativeScrollEvent } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, ClipboardCheck, ShieldCheck, FileText, Building2, Mail, Phone, ChevronDown, Paperclip, X } from "lucide-react-native";
import * as DocumentPicker from "expo-document-picker";
import { api } from "@/src/api/client";
import { setPendingVerificationDoc, peekPendingVerificationDoc, PickedAsset } from "@/src/lib/upload";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import {
  RegistrationErrors,
  RegistrationVariant,
  validateRegistration,
} from "@/src/features/auth/registration-validation";

// ── Role → header label + which form variant to render ──────────────────────
const labelMap: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO",
  site: "Site / Hospital", pi: "Site / Hospital", crc: "Site / Hospital",
  patient: "Patient",
};
const departmentOptions = [
  "Emergency Medicine", "Internal Medicine", "General Surgery", "Critical Care / Intensive Care",
  "Cardiology", "Gastroenterology", "Pulmonology", "Nephrology", "Neurology", "Endocrinology",
  "Haematology", "Infectious Diseases", "Allergy & Immunology", "Clinical Pharmacology", "Medical Genetics",
  "Orthopaedic Surgery", "Neurosurgery", "Cardiothoracic Surgery", "Urology", "Otolaryngology (ENT)",
  "Vascular Surgery", "Plastic & Reconstructive Surgery", "Colorectal Surgery", "Transplant Surgery", "Paediatric Surgery",
  "Paediatrics", "Obstetrics & Gynaecology (OB/GYN)", "Geriatrics", "Family Medicine",
  "Oncology", "Dermatology", "Psychiatry", "Rheumatology", "Ophthalmology", "Physical Medicine & Rehabilitation",
  "Radiology", "Nuclear Medicine", "Pathology", "Anaesthesiology", "Pain Medicine", "Palliative Care", "Sleep Medicine",
];
function variantFor(role?: string): RegistrationVariant {
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

// An organization from GET /api/organizations. Continue checks whether the typed
// name matches an onboarded org; existing orgs surface their platform contact,
// while new org registrations proceed directly.
interface PlatformContact { name: string; designation?: string; email?: string; phone?: string }
interface Org { id: string; name: string; type: string; address?: string; contact?: string; email?: string; website?: string; status?: string; platform_contact?: PlatformContact }
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
// ── Shared primitives ───────────────────────────────────────────────────────
function Field({ label, required, error, children }: { label: string; required?: boolean; error?: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={f.label}>{label}{required && <Text style={{ color: colors.accent }}> *</Text>}</Text>
      {children}
      {!!error && <Small color={colors.destructive} style={f.fieldError}>{error}</Small>}
    </View>
  );
}
function Input(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput placeholderTextColor={colors.mutedFg + "99"} {...props} style={[f.input, props.style]} />;
}
function PhoneInput({ value, onChangeText, error }: { value: string; onChangeText: (v: string) => void; error?: boolean }) {
  return (
    <View style={{ flexDirection: "row", gap: 8 }}>
      <View style={f.prefix}><Text style={{ color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 15 }}>+91</Text></View>
      <Input value={value} onChangeText={onChangeText} keyboardType="phone-pad" placeholder="98XXXXXXXX" style={[{ flex: 1 }, error && f.inputError]} />
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
            <ScrollView style={f.selectOptions} showsVerticalScrollIndicator={false} nestedScrollEnabled>
              {options.map((o) => {
              const on = o === value;
              return (
                <Pressable key={o} onPress={() => { onChange(o); setOpen(false); }} style={[f.selectItem, on && { backgroundColor: colors.primary }]}>
                  <Text style={{ fontSize: 15, color: on ? colors.primaryFg : colors.foreground, fontFamily: on ? fonts.semibold : fonts.regular }}>{o}</Text>
                  {on && <Check size={16} color={colors.primaryFg} strokeWidth={3} />}
                </Pressable>
              );
              })}
            </ScrollView>
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
  const {
    role,
    org: orgParam,
    email: emailParam,
    fullName: fullNameParam,
    designation: designationParam,
    phone: phoneParam,
    inviteToken,
  } = useLocalSearchParams<{
    role: string;
    org?: string;
    email?: string;
    fullName?: string;
    designation?: string;
    phone?: string;
    inviteToken?: string;
  }>();
  const variant = variantFor(role);
  const isPatient = variant === "patient";
  const orgNoun = variant === "site" ? "site" : variant === "smo" ? "SMO" : "organization";
  const entityLabel = labelMap[role as string] || "Sponsor";

  const [fld, setFld] = useState<Record<string, string>>(() => {
    const base = initFields(variant);
    // Invite prefills are real user data (not demo defaults) — always apply them.
    if (orgParam) base.orgName = String(orgParam);
    if (emailParam) base.email = String(emailParam);
    if (fullNameParam) base.fullName = String(fullNameParam);
    if (designationParam) base.designation = String(designationParam);
    if (phoneParam) base.phone = String(phoneParam);
    return base;
  });
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const up = (k: string) => (v: string) => {
    setFld((s) => ({ ...s, [k]: v }));
    setTouched((current) => ({ ...current, [k]: true }));
  };

  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [declarationAccepted, setDeclarationAccepted] = useState(false);
  const [showInstructions, setShowInstructions] = useState(() => !isPatient);
  const [showDeclaration, setShowDeclaration] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [termsScrolled, setTermsScrolled] = useState(false);
  const [orgCheck, setOrgCheck] = useState<"exists" | null>(null);
  // True while Continue performs the authoritative org/contact lookup.
  const [checkingOrg, setCheckingOrg] = useState(false);
  const [err, setErr] = useState("");

  // Verification doc: selected here (pre-auth) and held in a module store so it
  // survives the multi-screen flow, then uploaded from set-password once a token
  // exists (POST /files needs auth — see task 5.2 report).
  const [verificationDoc, setVerificationDoc] = useState<PickedAsset | null>(() => peekPendingVerificationDoc());
  const [docErr, setDocErr] = useState("");
  const pickVerificationDoc = async () => {
    setDocErr("");
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "image/png", "image/jpeg", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        copyToCacheDirectory: true, multiple: false,
      });
      if (res.canceled || !res.assets?.length) return;
      const a = res.assets[0];
      const picked: PickedAsset = { uri: a.uri, name: a.name || "verification", mimeType: a.mimeType, file: (a as any).file };
      setVerificationDoc(picked);
      setPendingVerificationDoc(picked);
    } catch { setDocErr("Couldn't open the file picker."); }
  };
  const clearVerificationDoc = () => { setVerificationDoc(null); setPendingVerificationDoc(null); };

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

  const validation = useMemo(
    () => validateRegistration(variant, {
      ...fld,
      verificationDoc: verificationDoc?.name || "",
    }),
    [fld, variant, verificationDoc],
  );
  const canContinue = agreedToTerms && declarationAccepted && validation.valid;
  const fieldError = (key: keyof RegistrationErrors) =>
    submitted || touched[key] ? validation.errors[key] : undefined;
  const matchedOrg = orgMatches.find((o) => o.name.trim().toLowerCase() === (fld.orgName || "").trim().toLowerCase()) || null;
  const declarationText = isPatient
    ? "I confirm that the information I have provided is true, accurate and complete, and I agree to comply with the platform's Terms of Use and Privacy Policy."
    : "I confirm that I am authorized to register and represent this organization, that the information provided is accurate, and I agree to comply with the platform's Terms of Use and Privacy Policy.";

  const onTermsScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
    if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 8) setTermsScrolled(true);
  };

  const proceed = () => {
    router.push({
      pathname: "/(auth)/security-questions",
      params: {
        role: role || "patient",
        variant,
        payload: JSON.stringify({
          ...validation.normalized,
          inviteToken: inviteToken || "",
        }),
      },
    });
  };

  const handleContinue = async () => {
    setSubmitted(true);
    setErr("");
    if (!canContinue || checkingOrg) return;
    if (isPatient) { proceed(); return; }
    setShowOrgSuggestions(false);
    const q = (fld.orgName || "").trim();

    // Continue performs an authoritative directory lookup, then fetches the
    // public admin contact only for the exact organization selected/entered.
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
      let fresh = list.find((o) => o.name.trim().toLowerCase() === q.toLowerCase()) || null;
      if (fresh) {
        try {
          const contactResponse = await api.get(`/organizations/${fresh.id}/platform-contact`);
          fresh = { ...fresh, platform_contact: contactResponse.data?.platform_contact || undefined };
          setOrgMatches(current => current.map(org => org.id === fresh?.id ? fresh : org));
        } catch {
          // The modal still identifies the organization and provides MTB support
          // fallback copy when no public organization contact is available.
        }
        setOrgCheck("exists");
      } else {
        proceed();
      }
    } catch {
      setErr("We couldn't check this organization right now. Please try again.");
    } finally {
      setCheckingOrg(false);
    }
  };

  const onOrgNameChange = (v: string) => {
    setFld((s) => ({ ...s, orgName: v }));
    setTouched((current) => ({ ...current, orgName: true }));
    setShowOrgSuggestions(true);
  };
  const pickOrg = (o: Org) => {
    setFld((s) => ({ ...s, orgName: o.name, orgAddress: o.address || s.orgAddress }));
    setTouched((current) => ({ ...current, orgName: true, orgAddress: true }));
    setShowOrgSuggestions(false);
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow="Step 2 of 5" title="Tell us about you" onBack={() => router.back()} step={2} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Rise delay={150}>
            <View style={f.entityBadge}>
              <View style={f.entityIcon}><Building2 size={18} color={colors.accent} /></View>
              <View>
                <Text style={f.entityKicker}>Registering as</Text>
                <Text style={f.entityLabel}>{entityLabel}</Text>
              </View>
            </View>
          </Rise>

          <Rise delay={200}>
            {/* Common identity fields */}
            <Field label="Full Name" required error={fieldError("fullName")}><Input value={fld.fullName} onChangeText={up("fullName")} style={fieldError("fullName") && f.inputError} /></Field>
            {!isPatient && <Field label="Designation" required error={fieldError("designation")}><Input value={fld.designation} onChangeText={up("designation")} style={fieldError("designation") && f.inputError} /></Field>}

            {isPatient ? (
              <>
                <Field label="Phone Number" required error={fieldError("phone")}><PhoneInput value={fld.phone} onChangeText={up("phone")} error={!!fieldError("phone")} /></Field>
                <Field label="Email ID" required error={fieldError("email")}><Input value={fld.email} onChangeText={up("email")} keyboardType="email-address" autoCapitalize="none" placeholder="patient@example.com" style={fieldError("email") && f.inputError} /></Field>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={{ flex: 1 }}>
                    <Field label="Date of Birth" required error={fieldError("dob")}><Input value={fld.dob} onChangeText={up("dob")} keyboardType="numbers-and-punctuation" placeholder="YYYY-MM-DD" style={fieldError("dob") && f.inputError} /></Field>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Field label="Age">
                      <View style={[f.input, { justifyContent: "center", backgroundColor: colors.surface }]}>
                        <Text style={{ color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 15 }}>{validation.age === null ? "—" : `${validation.age} yrs`}</Text>
                      </View>
                    </Field>
                  </View>
                </View>
                <Field label="Gender" required error={fieldError("gender")}>
                  <Select value={fld.gender} placeholder="Select gender" options={["Female", "Male", "Other", "Prefer not to say"]} onChange={up("gender")} />
                </Field>
                <Field label="Preferred Language">
                  <Select value={fld.language} placeholder="Select language" options={["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"]} onChange={up("language")} />
                </Field>
              </>
            ) : (
              <>
                <Field label="Email ID" required error={fieldError("email")}><Input value={fld.email} onChangeText={up("email")} keyboardType="email-address" autoCapitalize="none" style={fieldError("email") && f.inputError} /></Field>
                <Field label="Phone Number" required error={fieldError("phone")}><PhoneInput value={fld.phone} onChangeText={up("phone")} error={!!fieldError("phone")} /></Field>

                <SectionRow title="Organization" />
                <Field label={variant === "smo" ? "SMO Name" : "Organization Name"} required error={fieldError("orgName")}>
                  <Input value={fld.orgName} onChangeText={onOrgNameChange} onFocus={() => setShowOrgSuggestions(true)} placeholder={`Search or type your ${orgNoun} name`} style={fieldError("orgName") && f.inputError} />
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
                <Field label={variant === "smo" ? "SMO Address" : "Organization Address"} required error={fieldError("orgAddress")}>
                  <Input value={fld.orgAddress} onChangeText={up("orgAddress")} multiline placeholder="Building / Street, City, State, PIN" style={[{ height: 64, textAlignVertical: "top" }, fieldError("orgAddress") && f.inputError]} />
                </Field>

                {variant === "site" && (
                  <>
                    <Field label="Hospital Type" required error={fieldError("hospitalType")}><Select value={fld.hospitalType} placeholder="Select hospital type" options={["Private", "Government"]} onChange={up("hospitalType")} /></Field>
                    <Field label="Role" required error={fieldError("role")}><Select value={fld.role} placeholder="Select role" options={["PI", "Research Team"]} onChange={up("role")} /></Field>
                    {fld.role === "PI" && <Field label="Department"><Select value={fld.department} placeholder="Select department" options={departmentOptions} onChange={up("department")} /></Field>}
                  </>
                )}

                <Field label="Verification Document" required error={fieldError("verificationDoc")}>
                  {verificationDoc ? (
                    <View style={f.docChip}>
                      <View style={f.docIcon}><FileText size={16} color={colors.primary} /></View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Body weight="700" numberOfLines={1} style={{ fontSize: 14 }}>{verificationDoc.name}</Body>
                        <Small color={colors.mutedFg}>Uploaded right after your account is created.</Small>
                      </View>
                      <Pressable onPress={clearVerificationDoc} hitSlop={8} style={f.docRemove}><X size={15} color={colors.mutedFg} /></Pressable>
                    </View>
                  ) : (
                    <Pressable onPress={pickVerificationDoc} style={f.docPick}>
                      <Paperclip size={16} color={colors.primary} />
                      <Small color={colors.primary} weight="700">Attach self-attested document</Small>
                    </Pressable>
                  )}
                  <Small color={colors.mutedFg} style={{ marginTop: 6, fontSize: 12 }}>Required for staff and organization registrations — PDF, PNG, JPG or DOCX (max 10 MB).</Small>
                  {docErr ? <Small color={colors.destructive} style={{ marginTop: 4 }}>{docErr}</Small> : null}
                </Field>
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
        {orgCheck === "exists" && matchedOrg ? (
          <>
            <ModalHead icon={<Building2 size={24} color={colors.primary} />} eyebrow="Already registered" title={`This ${orgNoun} is already on MTB`} subtitle={`${matchedOrg.name} is already registered. Contact the platform admin below to request an invitation.`} />
            <View style={{ paddingHorizontal: spacing.lg }}>
              <View style={f.adminCard}>
                <Eyebrow color={colors.mutedFg} style={{ marginBottom: 10 }}>Platform Contact Admin</Eyebrow>
                {matchedOrg.platform_contact ? (
                  <>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                      <View style={f.adminAvatar}><Text style={{ fontFamily: fonts.bold, color: colors.primary, fontSize: 14 }}>{adminInitials(matchedOrg.platform_contact.name)}</Text></View>
                      <View style={{ flex: 1 }}>
                        <Body weight="700" style={{ fontSize: 14 }}>{matchedOrg.platform_contact.name}</Body>
                        <Small>{matchedOrg.platform_contact.designation || "Organization Admin"}</Small>
                      </View>
                    </View>
                    {(matchedOrg.platform_contact.email || matchedOrg.platform_contact.phone) && (
                      <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 }}>
                        {matchedOrg.platform_contact.email ? <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Mail size={15} color={colors.mutedFg} /><Small>{matchedOrg.platform_contact.email}</Small></View> : null}
                        {matchedOrg.platform_contact.phone ? <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><Phone size={15} color={colors.mutedFg} /><Small>{matchedOrg.platform_contact.phone}</Small></View> : null}
                      </View>
                    )}
                  </>
                ) : (
                  <Small style={{ lineHeight: 20 }}>Admin contact details are not available yet. Please contact MTB support for help joining this organization.</Small>
                )}
                <View style={{ marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border }}>
                  <Small color={colors.mutedFg}>{matchedOrg.name} · {orgTypeLabels[matchedOrg.type] || matchedOrg.type}</Small>
                </View>
              </View>
            </View>
            <View style={{ padding: spacing.lg, gap: 8 }}>
              <Pressable onPress={() => setOrgCheck(null)} style={[f.cta, { backgroundColor: colors.primary }]}><Text style={{ fontFamily: fonts.bold, fontSize: 14, color: colors.primaryFg }}>Okay, got it</Text></Pressable>
              <Pressable onPress={() => setOrgCheck(null)} style={f.ghostBtn}><Text style={{ fontFamily: fonts.semibold, fontSize: 14, color: colors.mutedFg }}>Use a different name</Text></Pressable>
            </View>
          </>
        ) : null}
      </ModalCard>
    </SafeAreaView>
  );
}

const f = StyleSheet.create({
  entityBadge: { flexDirection: "row", alignItems: "center", gap: 12, borderWidth: 1, borderColor: colors.accent + "40", backgroundColor: colors.secondary + "88", borderRadius: radii.md, paddingHorizontal: spacing.md, paddingVertical: 12, marginBottom: spacing.md },
  entityIcon: { width: 36, height: 36, borderRadius: 999, backgroundColor: colors.accent + "1F", alignItems: "center", justifyContent: "center" },
  entityKicker: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.2, textTransform: "uppercase", color: colors.mutedFg },
  entityLabel: { marginTop: 1, fontFamily: fonts.heading, fontSize: 17, color: colors.foreground },
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  inputError: { borderColor: colors.destructive, backgroundColor: colors.destructive + "08" },
  fieldError: { marginTop: 5, lineHeight: 17 },
  prefix: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  suggestBox: { marginTop: 6, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.card, overflow: "hidden" },
  suggestRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  suggestIcon: { width: 30, height: 30, borderRadius: 8, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  checkRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, marginTop: spacing.sm },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center", marginTop: 2 },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  ghostBtn: { paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  // Select dropdown
  selectOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", justifyContent: "center", paddingHorizontal: spacing.xl },
  selectSheet: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, padding: 6 },
  selectOptions: { maxHeight: 420 },
  selectItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 14, borderRadius: radii.md },
  // Centered modal card
  cardOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "80", alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.lg },
  card: { backgroundColor: colors.background, width: "100%", maxWidth: 400, borderRadius: 28, overflow: "hidden", ...({ shadowColor: "#2E1B33", shadowOpacity: 0.2, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 12 }) },
  iconBadge: { width: 48, height: 48, borderRadius: 16, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  numBadge: { width: 24, height: 24, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  quote: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  banner: { paddingHorizontal: spacing.md, paddingVertical: 8, borderRadius: radii.md },
  adminCard: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: 14 },
  adminAvatar: { width: 40, height: 40, borderRadius: 999, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  docPick: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 12, borderRadius: radii.md, borderWidth: 1, borderStyle: "dashed", borderColor: colors.primary + "66", backgroundColor: colors.primary + "0A" },
  docChip: { flexDirection: "row", alignItems: "center", gap: 10, padding: 10, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  docIcon: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  docRemove: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
});
