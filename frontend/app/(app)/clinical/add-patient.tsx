import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, View, ScrollView, TextInput, Pressable, StyleSheet, KeyboardAvoidingView, Platform, StatusBar, Text, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { ChevronLeft, ChevronRight, Calendar as CalIcon, Sparkles, AlertTriangle, RefreshCw, Users, UserRound, Phone as PhoneIcon, ClipboardList } from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { getKitModel, type SavedKitModel } from "@/src/features/kit/api";
import type { KitQuestion } from "@/src/features/kit";
import { PatientTimeline, usePatientTimeline } from "@/src/features/kit/PatientTimeline";
import { dmyToIso } from "@/src/features/kit/participant";
import { sanitizeName } from "@/src/lib/validators";

const C = {
  surface: "#EDF1F6", card: "#FFFFFF", fg: "#0F1B2D", muted: "#556275", border: "#DDE3EA",
  primary: "#1B5FC1", primaryFg: "#FFFFFF",
  accent: "#0B8074", info: "#2D6CB5", destructive: "#C23030",
};

type Trial = { id: string; title?: string; protocol_id?: string; condition?: string; phase?: string };
type PiOption = { id: string; full_name?: string; email?: string; role?: string };

// Accepts "5 May 2025", "2025-05-05" or "05/05/2025" → Date (or null).
function parseDate(s: string): Date | null {
  const t = s.trim();
  const dmy = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (dmy) {
    const day = +dmy[1], month = +dmy[2], year = +dmy[3];
    const d = new Date(year, month - 1, day);
    // Date() silently rolls an out-of-range month/day into a later date
    // instead of rejecting it (month 82 -> +6 years) — confirm the
    // constructed date actually reflects what was typed rather than
    // trusting isNaN alone, which a rolled-over date always passes.
    return d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day
      ? d : null;
  }
  const named = t.match(/^(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})$/);
  if (named) {
    const month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
      .indexOf(named[2].slice(0, 3).toLowerCase());
    if (month >= 0) {
      const d = new Date(+named[3], month, +named[1]);
      return d.getMonth() === month && d.getDate() === +named[1] ? d : null;
    }
  }
  const d = new Date(t);
  return isNaN(d.getTime()) ? null : d;
}
const toISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
// The backend's validation-error detail can be a string (our own raised
// errors) or an array of Pydantic error objects (422s) — only the former is
// safe to render as text.
const errorMessage = (e: any, fallback: string): string => {
  const detail = e?.response?.data?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
};
const formatDateInput = (value: string) => {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
};

const initialsFromName = (value: string) => value
  .trim()
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map(part => part[0]?.toUpperCase() || "")
  .join("");

function trialLabel(t: Trial) {
  const head = t.protocol_id || t.title || "Trial";
  return t.condition ? `${head} — ${t.condition}` : head;
}

export default function AddPatient() {
  const router = useRouter();
  const { trialId: requestedTrialId } = useLocalSearchParams<{ trialId?: string }>();
  const { user } = useAuth();
  const needsPiSelection = user?.role === "smo" || user?.role === "site";
  const [subjectId, setSubjectId] = useState("");
  const [initials, setInitials] = useState("");
  const [initialsEdited, setInitialsEdited] = useState(false);
  const [fullName, setFullName] = useState("");
  const [dob, setDob] = useState("");
  const [gender, setGender] = useState("");
  const [genderOpen, setGenderOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [lang, setLang] = useState("English");
  const [langOpen, setLangOpen] = useState(false);
  const [trials, setTrials] = useState<Trial[]>([]);
  const [trialsLoading, setTrialsLoading] = useState(true);
  const [trialId, setTrialId] = useState<string | null>(null);
  const [trialOpen, setTrialOpen] = useState(false);
  const [pis, setPis] = useState<PiOption[]>([]);
  const [piId, setPiId] = useState<string | null>(null);
  const [piOpen, setPiOpen] = useState(false);
  const [piLoading, setPiLoading] = useState(needsPiSelection);
  const [piLoadError, setPiLoadError] = useState<string | null>(null);
  const [piPermissionDenied, setPiPermissionDenied] = useState(false);
  // The trial's reviewed protocol schedule (from the Add Trial upload). The
  // patient's dates are computed from it, their start date and their answers.
  const [kit, setKit] = useState<SavedKitModel | null>(null);
  const [kitState, setKitState] = useState<"loading" | "ready" | "none" | "error">("loading");
  const [kitAnswers, setKitAnswers] = useState<Record<string, string>>({});
  const [startDate, setStartDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subjectDuplicate, setSubjectDuplicate] = useState<string | null>(null);
  const [emailDuplicate, setEmailDuplicate] = useState<string | null>(null);

  const loadPis = useCallback(async () => {
    if (!needsPiSelection) {
      setPiLoading(false);
      return;
    }
    setPiLoading(true);
    setPiLoadError(null);
    setPiPermissionDenied(false);
    try {
      const response = await api.get("/team");
      const availablePis = (Array.isArray(response.data) ? response.data : [])
        .filter((member: PiOption) => member.role === "pi");
      setPis(availablePis);
      setPiId(current => (
        current && availablePis.some((pi: PiOption) => pi.id === current)
          ? current
          : availablePis[0]?.id || null
      ));
    } catch (e: any) {
      const status = e?.response?.status;
      setPis([]);
      setPiId(null);
      setPiOpen(false);
      if (status === 401 || status === 403) {
        setPiPermissionDenied(true);
        setPiLoadError("You don't have permission to view Principal Investigators for this organization.");
      } else {
        setPiLoadError("We couldn't load Principal Investigators. Check your connection and try again.");
      }
    } finally {
      setPiLoading(false);
    }
  }, [needsPiSelection]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await api.get("/trials");
        if (!alive) return;
        setTrials(r.data);
        if (r.data.length) {
          const requested = r.data.find((trial: Trial) => trial.id === requestedTrialId);
          setTrialId(requested?.id || r.data[0].id);
        }
      } catch { if (alive) setError("Couldn't load trials. Pull back and retry."); }
      finally { if (alive) setTrialsLoading(false); }
    })();
    return () => { alive = false; };
  }, [requestedTrialId]);

  useEffect(() => {
    void loadPis();
  }, [loadPis]);

  useEffect(() => {
    let alive = true;
    setKit(null);
    setKitAnswers({});
    if (!trialId) { setKitState("none"); return () => { alive = false; }; }
    setKitState("loading");
    getKitModel(trialId)
      .then((saved) => { if (alive) { setKit(saved); setKitState("ready"); } })
      .catch((e: any) => { if (alive) setKitState(e?.response?.status === 404 ? "none" : "error"); });
    return () => { alive = false; };
  }, [trialId]);

  const startAnchor = kit?.model.anchors.find((anchor) => anchor.kind === "start");
  const kitQuestions: KitQuestion[] = kit?.model.questions.filter((q) => q.scope === "participant") || [];
  const unanswered = kitQuestions.filter((q) => !kitAnswers[q.id]);
  const startIso = dmyToIso(startDate);
  const participant = useMemo(() => (kit && startAnchor && startIso
    ? { startAnchor: startAnchor.id, startDate: startIso, answers: kitAnswers } : null), [kit, startAnchor, startIso, kitAnswers]);
  const preview = usePatientTimeline(kit?.model, kit?.site_answers, participant);

  const selectedTrial = trials.find(t => t.id === trialId);
  const selectedPi = pis.find(pi => pi.id === piId);
  const suggestedInitials = initialsFromName(fullName);
  const phoneDigits = phone.replace(/\D/g, "");
  const normalizedEmail = email.trim().toLowerCase();
  const optionalEmailValid = !normalizedEmail || /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(normalizedEmail);
  const canSubmit = !!subjectId.trim()
    && !!fullName.trim()
    && !!parseDate(dob)
    && phoneDigits.length === 10
    && optionalEmailValid
    && !!trialId
    && !!preview
    && !unanswered.length
    && (!needsPiSelection || !!piId)
    && !subjectDuplicate
    && !emailDuplicate
    && !saving;
  const submitHint = !subjectId.trim() || !fullName.trim() || !parseDate(dob) || phoneDigits.length !== 10
    ? "Complete the required patient details"
    : !optionalEmailValid
      ? "Enter a valid email address or leave it blank"
      : !trialId
        ? "Select a trial to continue"
        : needsPiSelection && !piId
          ? "Select the responsible PI"
          : kitState === "none"
            ? "This trial has no protocol schedule yet"
            : !startIso
              ? `Enter the ${startAnchor?.label.toLowerCase() || "start"} date`
              : unanswered.length
                ? `Answer: ${unanswered.map((q) => q.label).join(", ")}`
                : !preview
                  ? "The schedule could not be calculated"
                  : "Ready to send the patient invitation";

  const updateFullName = (raw: string) => {
    const value = sanitizeName(raw);
    setFullName(value);
    if (!initialsEdited) setInitials(initialsFromName(value));
  };

  const updateInitials = (value: string) => {
    const normalized = value.replace(/[^a-z]/gi, "").slice(0, 4).toUpperCase();
    setInitials(normalized);
    setInitialsEdited(normalized !== suggestedInitials);
  };

  const restoreSuggestedInitials = () => {
    setInitials(suggestedInitials);
    setInitialsEdited(false);
  };

  const checkInvitationAvailability = async (field: "subject" | "email") => {
    if (!trialId) return;
    const value = field === "subject" ? subjectId.trim() : email.trim().toLowerCase();
    if (!value) return;
    try {
      const response = await api.get("/patients/invite/check-availability", {
        params: {
          trial_id: trialId,
          subject_id: field === "subject" ? `SUBJ-${value}` : undefined,
          email: field === "email" ? value : undefined,
        },
      });
      const result = field === "subject" ? response.data?.subject_id : response.data?.email;
      const setMessage = field === "subject" ? setSubjectDuplicate : setEmailDuplicate;
      setMessage(result?.available === false ? result.message : null);
    } catch {
      // Final validation remains server-side when the invitation is submitted.
    }
  };

  const submit = async () => {
    if (!canSubmit || !trialId || !participant) return;
    setError(null);
    setSaving(true);
    try {
      const parsedDob = parseDate(dob);
      const response = await api.post("/patients/invite", {
        full_name: fullName.trim(),
        email: normalizedEmail || undefined,
        phone: `+91${phoneDigits}`,
        trial_id: trialId,                                  // the SELECTED trial
        pi_id: needsPiSelection ? piId : undefined,
        kit: participant,
        subject_id: subjectId ? `SUBJ-${subjectId}` : undefined,
        dob: parsedDob ? toISO(parsedDob) : (dob || undefined),
        gender: gender || undefined,
        language: lang || undefined,
        avatar_initials: initials || suggestedInitials || undefined,
        enrolled_date: new Date().toISOString().slice(0, 10),
      });
      if (normalizedEmail) {
        Alert.alert(
          "Invitation sent",
          "The patient will receive an email invitation. Their account, trial enrollment, and visit schedule will be created after they accept and complete registration.",
          [{ text: "Done", onPress: () => router.back() }],
        );
      } else {
        const invitationCode = String(response.data?.token || "");
        Alert.alert(
          "Invitation created",
          `Share this invitation code with the patient:\n\n${invitationCode}\n\nThe patient will verify the registered phone number while joining.`,
          [
            {
              text: "Copy code",
              onPress: () => {
                void Clipboard.setStringAsync(invitationCode).then(() => {
                  Alert.alert("Copied", "Invitation code copied.");
                });
              },
            },
            { text: "Done", onPress: () => router.back() },
          ],
        );
      }
    } catch (e: any) {
      if (e?.response?.status === 422 && typeof e?.response?.data?.detail === "string") {
        setError(e.response.data.detail);
      } else if (e?.response?.status === 409) {
        setError(errorMessage(e, `SUBJ-${subjectId} already exists in this trial.`));
      } else {
        setError("Couldn't add patient. Please check the details and try again.");
      }
    } finally { setSaving(false); }
  };

  return (
    <View style={s.screen}>
      <StatusBar barStyle="dark-content" backgroundColor={C.surface} />
      <SafeAreaView edges={["top"]} style={{ backgroundColor: C.surface }}>
        <View style={s.appBar}>
          <Pressable testID="back" onPress={() => router.back()} hitSlop={10} style={s.backBtn}>
            <ChevronLeft size={24} color={C.fg} />
          </Pressable>
          <View style={s.appBarCopy}>
            <Text style={s.appBarTitle}>Add Patient</Text>
            <Text style={s.appBarSubtitle}>Create and invite a trial participant</Text>
          </View>
          <View style={{ width: 40 }} />
        </View>
      </SafeAreaView>

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <View style={s.patientHero}>
            <View style={s.patientAvatar}>
              <Text style={s.patientAvatarText}>{initials || suggestedInitials || "P"}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={s.patientHeroTitle}>{fullName.trim() || "New participant"}</Text>
              <Text style={s.patientHeroText}>Complete the details below to create the subject record and send an invitation.</Text>
            </View>
          </View>
          <FormSection icon={<UserRound size={18} color={C.primary} />} index="01" title="Patient details" subtitle="Identity and demographics" active={genderOpen}>
            <Field label="Full Name *">
              <TextInput testID="full-name" value={fullName} onChangeText={updateFullName} placeholder="Enter patient's full name" placeholderTextColor={C.muted + "99"} autoCapitalize="words" style={s.input} />
            </Field>

            <Field
              label="Subject Initials"
              hint="Generated from the full name. You can still edit it."
              action={initialsEdited && suggestedInitials ? (
                <Pressable onPress={restoreSuggestedInitials} hitSlop={8} style={s.autoAction}>
                  <Sparkles size={12} color={C.primary} />
                  <Text style={s.autoActionText}>Use {suggestedInitials}</Text>
                </Pressable>
              ) : <Text style={s.autoLabel}>AUTO</Text>}
            >
              <TextInput testID="initials" value={initials} onChangeText={updateInitials} placeholder="Auto-generated" placeholderTextColor={C.muted + "99"} autoCapitalize="characters" maxLength={4} style={s.input} />
            </Field>

            <Field label="Subject Number/ID *" hint="Unique within the selected trial.">
              <View style={s.inputPair}>
                <View style={s.prefix}><Text style={s.monoPrefix}>SUBJ-</Text></View>
                <TextInput
                  testID="subject-id"
                  value={subjectId}
                  onChangeText={value => {
                    setSubjectId(value.replace(/^SUBJ-/i, "").replace(/\s/g, "").toUpperCase());
                    setSubjectDuplicate(null);
                    if (error) setError(null);
                  }}
                  onBlur={() => void checkInvitationAvailability("subject")}
                  placeholder="0001"
                  placeholderTextColor={C.muted + "99"}
                  autoCapitalize="characters"
                  style={[s.input, { flex: 1, fontFamily: "monospace" as any }, subjectDuplicate && s.duplicateInput]}
                />
              </View>
              {subjectDuplicate ? <InlineDuplicate message={subjectDuplicate} /> : null}
            </Field>

            <View style={s.twoColumns}>
              <View style={{ flex: 1 }}>
                <Field label="Date of Birth *">
                  <TextInput testID="dob" value={dob} onChangeText={(value) => setDob(formatDateInput(value))} placeholder="DD/MM/YYYY" placeholderTextColor={C.muted + "99"} keyboardType="number-pad" maxLength={10} style={s.input} />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Gender" active={genderOpen}>
                  <Pressable testID="gender-toggle" onPress={() => setGenderOpen(open => !open)} style={[s.input, s.selectControl]}>
                    <Text numberOfLines={1} style={[s.selectText, !gender && s.placeholderText]}>{gender || "Select"}</Text>
                    <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: genderOpen ? "-90deg" : "90deg" }] }} />
                  </Pressable>
                  {genderOpen && <View style={s.dropdown}>{["Male", "Female", "Other", "Prefer not to say"].map(value => <Pressable key={value} testID={`gender-${value}`} onPress={() => { setGender(value); setGenderOpen(false); }} style={s.dropdownRow}><Text style={s.dropdownText}>{value}</Text></Pressable>)}</View>}
                </Field>
              </View>
            </View>
          </FormSection>

          <FormSection icon={<PhoneIcon size={18} color={C.primary} />} index="02" title="Contact details" subtitle="Used to deliver and verify the invitation" active={langOpen}>
            <Field label="Phone *">
              <View style={s.inputPair}>
                <View style={s.prefix}><Text style={s.prefixText}>+91</Text></View>
                <TextInput testID="phone" value={phone} onChangeText={(value) => setPhone(value.replace(/\D/g, "").slice(0, 10))} placeholder="10-digit mobile number" placeholderTextColor={C.muted + "99"} keyboardType="phone-pad" maxLength={10} style={[s.input, { flex: 1 }]} />
              </View>
            </Field>

            <Field label="Email (Optional)" hint="If provided, the invitation will also be sent by email.">
              <TextInput
                testID="email"
                value={email}
                onChangeText={(value) => { setEmail(value); setEmailDuplicate(null); }}
                onBlur={() => void checkInvitationAvailability("email")}
                placeholder="patient@example.com"
                placeholderTextColor={C.muted + "99"}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                style={[s.input, emailDuplicate && s.duplicateInput]}
              />
              {emailDuplicate ? <InlineDuplicate message={emailDuplicate} /> : null}
            </Field>

            <Field label="Preferred Language" active={langOpen}>
              <Pressable testID="lang-toggle" onPress={() => setLangOpen(open => !open)} style={[s.input, s.selectControl]}>
                <Text numberOfLines={1} style={s.selectText}>{lang}</Text>
                <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: langOpen ? "-90deg" : "90deg" }] }} />
              </Pressable>
              {langOpen && <View style={s.dropdown}>{["English", "Hindi", "Tamil", "Telugu"].map(value => <Pressable key={value} testID={`lang-${value}`} onPress={() => { setLang(value); setLangOpen(false); }} style={s.dropdownRow}><Text style={s.dropdownText}>{value}</Text></Pressable>)}</View>}
            </Field>
          </FormSection>

          <FormSection icon={<ClipboardList size={18} color={C.primary} />} index="03" title="Study assignment" subtitle="Connect the participant to the right trial and team" active={trialOpen || piOpen}>
          <Field label="Assign to Trial *" active={trialOpen}>
            <Pressable testID="trial-toggle" disabled={trialsLoading || !trials.length} onPress={() => setTrialOpen(open => !open)} style={[s.input, s.selectControl]}>
              {trialsLoading ? (
                <ActivityIndicator size="small" color={C.primary} />
              ) : (
                <Text style={{ color: selectedTrial ? C.fg : C.muted, fontSize: 14, lineHeight: 20, flex: 1, paddingRight: 12 }} numberOfLines={1}>
                  {selectedTrial ? trialLabel(selectedTrial) : "No trials available"}
                </Text>
              )}
              <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: trialOpen ? "-90deg" : "90deg" }] }} />
            </Pressable>
            {trialOpen && trials.length > 0 && (
              <View style={s.dropdown}>
                {trials.map(t => (
                  <Pressable key={t.id} testID={`trial-opt-${t.id}`} onPress={() => { setTrialId(t.id); setTrialOpen(false); }} style={s.dropdownRow}>
                    <Text style={{ color: C.fg, fontSize: 14 }}>{trialLabel(t)}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </Field>

          {needsPiSelection && (
            <Field label="Responsible PI *" active={piOpen}>
              <Pressable
                testID="pi-toggle"
                disabled={piLoading || !!piLoadError || !pis.length}
                onPress={() => setPiOpen(open => !open)}
                style={[s.input, s.selectControl]}
              >
                {piLoading ? (
                  <View testID="pi-loading" style={s.inlineState}>
                    <ActivityIndicator size="small" color={C.primary} />
                    <Text style={{ color: C.muted, fontSize: 14 }}>Loading Principal Investigators…</Text>
                  </View>
                ) : (
                  <>
                    <Text style={{ color: selectedPi ? C.fg : C.muted, fontSize: 14, flex: 1 }} numberOfLines={1}>
                      {selectedPi?.full_name || selectedPi?.email || (piLoadError ? "Principal Investigators unavailable" : "No PI available in your organization")}
                    </Text>
                    <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: piOpen ? "-90deg" : "90deg" }] }} />
                  </>
                )}
              </Pressable>
              {piOpen && pis.length > 0 && (
                <View style={s.dropdown}>
                  {pis.map(pi => (
                    <Pressable
                      key={pi.id}
                      testID={`pi-opt-${pi.id}`}
                      onPress={() => { setPiId(pi.id); setPiOpen(false); }}
                      style={s.dropdownRow}
                    >
                      <Text style={{ color: C.fg, fontSize: 14 }}>{pi.full_name || pi.email || "Principal Investigator"}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
              {!piLoading && piLoadError ? (
                <View testID={piPermissionDenied ? "pi-permission-error" : "pi-load-error"} style={s.piStateCard}>
                  <AlertTriangle size={18} color={C.destructive} />
                  <View style={{ flex: 1, gap: 8 }}>
                    <Text style={s.piStateText}>{piLoadError}</Text>
                    {!piPermissionDenied ? (
                      <Pressable testID="pi-retry" onPress={() => void loadPis()} style={s.stateAction}>
                        <RefreshCw size={14} color={C.primary} />
                        <Text style={s.stateActionText}>Retry</Text>
                      </Pressable>
                    ) : null}
                  </View>
                </View>
              ) : null}
              {!piLoading && !piLoadError && !pis.length ? (
                <View testID="pi-empty" style={s.piStateCard}>
                  <Users size={18} color={C.primary} />
                  <View style={{ flex: 1, gap: 8 }}>
                    <Text style={s.piStateText}>
                      No Principal Investigator is available. Add one to your organization before enrolling a patient.
                    </Text>
                    <Pressable testID="pi-open-team" onPress={() => router.push("/(app)/clinical/team")} style={s.stateAction}>
                      <Users size={14} color={C.primary} />
                      <Text style={s.stateActionText}>Open Organization Members</Text>
                    </Pressable>
                  </View>
                </View>
              ) : null}
            </Field>
          )}

          {/* Patient Access Note */}
          <View style={s.infoNote}>
            <Sparkles size={16} color={C.info} />
            <Text style={s.infoNoteText}>
              The patient gets an invitation. Their visit dates come from this trial's protocol schedule.
            </Text>
          </View>
          </FormSection>

          <FormSection icon={<CalIcon size={18} color={C.primary} />} index="04" title="Visit schedule" subtitle="Dates are worked out from the protocol">
          {kitState === "loading" && <ActivityIndicator color={C.primary} />}
          {kitState === "none" && <Text style={s.fieldHint}>This trial has no protocol schedule yet. Upload its protocol in Add Trial first.</Text>}
          {kitState === "error" && <Text style={[s.fieldHint, { color: C.destructive }]}>Couldn't load this trial's protocol schedule.</Text>}
          {kit && <>
            <Field label={`${startAnchor?.label || "Start"} date *`}>
              <View style={{ position: "relative" }}>
                <TextInput testID="kit-start-date" value={startDate} onChangeText={(value) => setStartDate(formatDateInput(value))}
                  placeholder="DD/MM/YYYY" placeholderTextColor={C.muted + "99"} keyboardType="number-pad" maxLength={10}
                  style={[s.input, { paddingRight: 48 }]} />
                <CalIcon size={20} color={C.primary} style={{ position: "absolute", right: 16, top: 14 }} />
              </View>
            </Field>
            {kitQuestions.map((q) => <Field key={q.id} label={`${q.text || q.label} *`}>
              {q.kind === "number"
                ? <TextInput value={kitAnswers[q.id] || ""} keyboardType="number-pad" placeholder="Number" placeholderTextColor={C.muted + "99"}
                    onChangeText={(value) => setKitAnswers((current) => ({ ...current, [q.id]: value.replace(/[^0-9.]/g, "") }))} style={s.input} />
                : <View style={s.chips}>
                    {(q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options).map((o) => {
                      const selected = kitAnswers[q.id] === o.value;
                      return <Pressable key={o.value} testID={`kit-q-${q.id}-${o.value}`} accessibilityRole="radio" accessibilityState={{ selected }}
                        onPress={() => setKitAnswers((current) => ({ ...current, [q.id]: o.value }))}
                        style={[s.chip, selected && s.chipSelected]}>
                        <Text style={[s.chipText, selected && s.chipTextSelected]}>{o.label}</Text>
                      </Pressable>;
                    })}
                  </View>}
            </Field>)}
            {preview && !unanswered.length
              ? <PatientTimeline data={preview} maxRows={5} />
              : <Text style={s.fieldHint}>Enter the date{kitQuestions.length ? " and answers" : ""} above to see the visit dates.</Text>}
          </>}
          </FormSection>

          {/* Error banner (server duplicate 409 or generic failure) */}
          {error && (
            <View testID="add-patient-error" style={s.dupWarn}>
              <AlertTriangle size={16} color={C.destructive} />
              <Text style={{ fontSize: 12, fontWeight: "600", color: C.destructive, flex: 1 }}>{error}</Text>
            </View>
          )}
        </ScrollView>

          {/* Sticky submit action */}
          <View style={s.footer}>
          <Text style={[s.submitHint, canSubmit && { color: C.primary }]}>{submitHint}</Text>
          <Pressable
            testID="add-patient-submit"
            onPress={submit}
            disabled={!canSubmit}
            style={[s.submit, !canSubmit && s.submitDisabled]}
          >
            <Text style={{ color: !canSubmit ? C.muted : C.primaryFg, fontSize: 15, fontWeight: "700" }}>
              {saving ? "Sending…" : "Send Patient Invitation"}
            </Text>
          </Pressable>
          </View>
      </KeyboardAvoidingView>
    </View>
  );
}

function FormSection({ icon, index, title, subtitle, children, active = false }: {
  icon: React.ReactNode;
  index: string;
  title: string;
  subtitle: string;
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <View style={[s.sectionCard, active && s.sectionCardActive]}>
      <View style={s.sectionHeader}>
        <View style={s.sectionIcon}>{icon}</View>
        <View style={{ flex: 1 }}>
          <View style={s.sectionTitleRow}>
            <Text style={s.sectionIndex}>{index}</Text>
            <Text style={s.sectionTitle}>{title}</Text>
          </View>
          <Text style={s.sectionSubtitle}>{subtitle}</Text>
        </View>
      </View>
      <View style={s.sectionFields}>{children}</View>
    </View>
  );
}

function Field({ label, children, active = false, hint, action }: {
  label: string;
  children: React.ReactNode;
  active?: boolean;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <View style={{ position: "relative", zIndex: active ? 100 : 1, elevation: active ? 100 : 0 }}>
      <View style={s.fieldLabelRow}>
        <Text style={s.fieldLabel}>{label}</Text>
        {action}
      </View>
      {children}
      {hint ? <Text style={s.fieldHint}>{hint}</Text> : null}
    </View>
  );
}

function InlineDuplicate({ message }: { message: string }) {
  return (
    <View style={s.inlineDuplicate}>
      <AlertTriangle size={14} color={C.destructive} />
      <Text style={s.inlineDuplicateText}>{message}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { minHeight: 38, paddingHorizontal: 14, justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  chipSelected: { backgroundColor: C.primary, borderColor: C.primary },
  chipText: { color: C.fg, fontSize: 13, fontWeight: "700" },
  chipTextSelected: { color: C.primaryFg },
  screen: { flex: 1, backgroundColor: C.surface },
  appBar: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 10 },
  backBtn: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  appBarCopy: { flex: 1, alignItems: "center" },
  appBarTitle: { fontSize: 18, fontWeight: "800", color: C.fg, textAlign: "center" },
  appBarSubtitle: { marginTop: 2, fontSize: 11, color: C.muted, textAlign: "center" },
  content: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 28, gap: 14 },
  patientHero: { flexDirection: "row", alignItems: "center", gap: 13, padding: 16, borderRadius: 20, backgroundColor: C.primary, shadowColor: C.primary, shadowOpacity: 0.14, shadowRadius: 12, shadowOffset: { width: 0, height: 5 }, elevation: 3 },
  patientAvatar: { width: 52, height: 52, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.16)", borderWidth: 1, borderColor: "rgba(255,255,255,0.25)" },
  patientAvatarText: { color: C.primaryFg, fontSize: 18, fontWeight: "800", letterSpacing: 0.5 },
  patientHeroTitle: { color: C.primaryFg, fontSize: 16, fontWeight: "800" },
  patientHeroText: { marginTop: 4, color: "rgba(255,255,255,0.78)", fontSize: 11, lineHeight: 16 },
  sectionCard: { position: "relative", zIndex: 1, padding: 16, borderRadius: 20, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, shadowColor: C.fg, shadowOpacity: 0.04, shadowRadius: 10, shadowOffset: { width: 0, height: 3 }, elevation: 1 },
  sectionCardActive: { zIndex: 50, elevation: 8 },
  sectionHeader: { flexDirection: "row", alignItems: "center", gap: 11, paddingBottom: 14, marginBottom: 14, borderBottomWidth: 1, borderBottomColor: C.border },
  sectionIcon: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(27,95,193,0.08)" },
  sectionTitleRow: { flexDirection: "row", alignItems: "center", gap: 7 },
  sectionIndex: { color: C.primary, fontSize: 10, fontWeight: "800", letterSpacing: 1 },
  sectionTitle: { color: C.fg, fontSize: 15, fontWeight: "800" },
  sectionSubtitle: { marginTop: 2, color: C.muted, fontSize: 11 },
  sectionFields: { gap: 14 },
  fieldLabelRow: { minHeight: 20, marginBottom: 6, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  fieldLabel: { color: "rgba(15,27,45,0.84)", fontSize: 12, fontWeight: "700" },
  fieldHint: { marginTop: 5, color: C.muted, fontSize: 10, lineHeight: 14 },
  autoLabel: { color: C.primary, fontSize: 9, fontWeight: "800", letterSpacing: 1 },
  autoAction: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(27,95,193,0.08)" },
  autoActionText: { color: C.primary, fontSize: 10, fontWeight: "700" },
  input: { minHeight: 50, paddingHorizontal: 14, paddingVertical: 12, borderRadius: 13, borderWidth: 1, borderColor: C.border, backgroundColor: "#FAFBFD", color: C.fg, fontSize: 14 },
  inputPair: { flexDirection: "row", gap: 8 },
  prefix: { minWidth: 68, minHeight: 50, paddingHorizontal: 12, borderRadius: 13, borderWidth: 1, borderColor: C.border, backgroundColor: "#E8EDF4", alignItems: "center", justifyContent: "center" },
  prefixText: { color: C.fg, fontSize: 14, fontWeight: "700" },
  monoPrefix: { color: C.primary, fontFamily: "monospace" as any, fontSize: 13, fontWeight: "700" },
  twoColumns: { flexDirection: "row", gap: 10 },
  selectControl: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  selectText: { color: C.fg, fontSize: 14, lineHeight: 20, flex: 1 },
  placeholderText: { color: C.muted },
  dupWarn: { marginTop: 2, flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 12, borderRadius: 14, backgroundColor: "rgba(194,48,48,0.05)", borderWidth: 1, borderColor: "rgba(194,48,48,0.20)" },
  duplicateInput: { borderColor: C.destructive, borderWidth: 2 },
  inlineDuplicate: { marginTop: 6, flexDirection: "row", alignItems: "flex-start", gap: 6 },
  inlineDuplicateText: { flex: 1, color: C.destructive, fontSize: 12, lineHeight: 17 },
  dropdown: { position: "absolute", top: "100%", left: 0, right: 0, marginTop: 4, zIndex: 100, elevation: 30, borderRadius: 13, borderWidth: 1, borderColor: C.border, backgroundColor: "#FAFBFD", overflow: "hidden", shadowColor: C.fg, shadowOpacity: 0.14, shadowRadius: 12, shadowOffset: { width: 0, height: 6 } },
  dropdownRow: { minHeight: 44, justifyContent: "center", paddingHorizontal: 14, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: C.border },
  dropdownText: { color: C.fg, fontSize: 13 },
  inlineState: { flexDirection: "row", alignItems: "center", gap: 8 },
  piStateCard: { marginTop: 8, flexDirection: "row", alignItems: "flex-start", gap: 10, padding: 12, borderRadius: 13, backgroundColor: "#FAFBFD", borderWidth: 1, borderColor: C.border },
  piStateText: { color: C.destructive, fontSize: 12, lineHeight: 17 },
  stateAction: { alignSelf: "flex-start", minHeight: 32, flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: "rgba(27,95,193,0.08)" },
  stateActionText: { color: C.primary, fontSize: 12, fontWeight: "700" },
  infoNote: { flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 12, borderRadius: 13, backgroundColor: "rgba(45,108,181,0.06)", borderWidth: 1, borderColor: "rgba(45,108,181,0.18)" },
  infoNoteText: { fontSize: 11, lineHeight: 16, color: C.info, flex: 1 },
  footer: { paddingHorizontal: 16, paddingTop: 10, paddingBottom: Platform.OS === "ios" ? 20 : 12, borderTopWidth: 1, borderTopColor: C.border, backgroundColor: C.card },
  submitHint: { marginBottom: 7, color: C.muted, fontSize: 10, textAlign: "center" },
  submit: { minHeight: 50, borderRadius: 999, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
  submitDisabled: { backgroundColor: C.border },
});
