import React, { useCallback, useEffect, useState } from "react";
import { Alert, View, ScrollView, TextInput, Pressable, StyleSheet, KeyboardAvoidingView, Platform, StatusBar, Text, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ChevronLeft, ChevronRight, Calendar as CalIcon, Sparkles, AlertTriangle, RefreshCw, Users } from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { formatIsoCalendarDate } from "@/src/lib/visit-timing";

const C = {
  surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryFg: "#FFFFFF",
  accent: "#E69B5C", info: "#7B6BB8", destructive: "#C0392B",
};

type Trial = { id: string; title?: string; protocol_id?: string; condition?: string; phase?: string };
type PiOption = { id: string; full_name?: string; email?: string; role?: string };
type ScheduleVisit = { visit_template_id?: string; visit_number?: number; name: string; scheduled_date?: string; status: string; manual_review_reason?: string };

// Accepts "5 May 2025", "2025-05-05" or "05/05/2025" → Date (or null).
function parseDate(s: string): Date | null {
  const t = s.trim();
  const dmy = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (dmy) { const d = new Date(+dmy[3], +dmy[2] - 1, +dmy[1]); return isNaN(d.getTime()) ? null : d; }
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
const formatDateInput = (value: string) => {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
};

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
  const [baseline, setBaseline] = useState("5 May 2025");
  const [scheduleGenerated, setScheduleGenerated] = useState(false);
  const [scheduleVisits, setScheduleVisits] = useState<ScheduleVisit[]>([]);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [showAll, setShowAll] = useState(false);
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

  const selectedTrial = trials.find(t => t.id === trialId);
  const selectedPi = pis.find(pi => pi.id === piId);
  const visible = showAll ? scheduleVisits : scheduleVisits.slice(0, 5);
  const phoneDigits = phone.replace(/\D/g, "");
  const canSubmit = !!subjectId.trim()
    && !!fullName.trim()
    && !!parseDate(dob)
    && phoneDigits.length === 10
    && !!email.trim()
    && !!trialId
    && scheduleGenerated
    && scheduleVisits.length > 0
    && (!needsPiSelection || !!piId)
    && !subjectDuplicate
    && !emailDuplicate
    && !saving;

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

  const generateSchedule = async () => {
    const parsedBaseline = parseDate(baseline);
    if (!parsedBaseline || !trialId) {
      setError("Select a trial and enter a valid baseline date before generating the schedule.");
      return;
    }
    setScheduleLoading(true);
    setError(null);
    setShowAll(false);
    try {
      const response = await api.post(`/trials/${trialId}/schedule-preview`, {
        baseline_date: toISO(parsedBaseline),
      });
      setScheduleVisits(response.data?.visits || []);
      setScheduleGenerated(true);
    } catch (e: any) {
      setScheduleVisits([]);
      setScheduleGenerated(false);
      setError(e?.response?.data?.detail || "We couldn't generate this trial's schedule. Please review the protocol visit templates.");
    } finally {
      setScheduleLoading(false);
    }
  };

  const submit = async () => {
    // The schedule preview is deliberately generated from the selected
    // protocol's templates; the form never falls back to demo offsets.
    if (!canSubmit || !trialId) return;
    setError(null);
    setSaving(true);
    try {
      const parsedBaseline = parseDate(baseline);
      const parsedDob = parseDate(dob);
      await api.post("/patients/invite", {
        full_name: fullName.trim(),
        email: email.trim().toLowerCase(),
        phone: `+91${phoneDigits}`,
        trial_id: trialId,                                  // the SELECTED trial
        pi_id: needsPiSelection ? piId : undefined,
        subject_id: subjectId ? `SUBJ-${subjectId}` : undefined,
        dob: parsedDob ? toISO(parsedDob) : (dob || undefined),
        gender: gender || undefined,
        language: lang || undefined,
        baseline_date: parsedBaseline ? toISO(parsedBaseline) : undefined,
        enrolled_date: new Date().toISOString().slice(0, 10),
      });
      Alert.alert(
        "Invitation sent",
        "The patient will receive an email invitation. Their account, trial enrollment, and visit schedule will be created after they accept and complete registration.",
        [{ text: "Done", onPress: () => router.back() }],
      );
    } catch (e: any) {
      if (e?.response?.status === 409) {
        setError(e.response.data?.detail || `SUBJ-${subjectId} already exists in this trial.`);
      } else {
        setError("Couldn't add patient. Please check the details and try again.");
      }
    } finally { setSaving(false); }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.surface }}>
      <StatusBar barStyle="dark-content" backgroundColor={C.surface} />
      <SafeAreaView edges={["top"]} style={{ backgroundColor: C.surface }}>
        <View style={s.appBar}>
          <Pressable testID="back" onPress={() => router.back()} hitSlop={10} style={s.backBtn}>
            <ChevronLeft size={24} color={C.fg} />
          </Pressable>
          <Text style={s.appBarTitle}>Add Patient</Text>
          <View style={{ width: 40 }} />
        </View>
      </SafeAreaView>

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32, gap: 16 }} keyboardShouldPersistTaps="handled">
          {/* Subject ID with duplicate detection */}
          <Field label="Subject Number/ID *">
            <View style={{ flexDirection: "row", gap: 8 }}>
              <View style={[s.prefix]}><Text style={{ fontFamily: "monospace" as any, fontSize: 14, color: C.muted }}>SUBJ-</Text></View>
              <TextInput
                testID="subject-id"
                value={subjectId}
                onChangeText={t => {
                  setSubjectId(t);
                  setSubjectDuplicate(null);
                  if (error) setError(null);
                }}
                onBlur={() => void checkInvitationAvailability("subject")}
                style={[s.input, { flex: 1, fontFamily: "monospace" as any }, subjectDuplicate && s.duplicateInput]}
              />
            </View>
            {subjectDuplicate ? <InlineDuplicate message={subjectDuplicate} /> : null}
          </Field>

          <Field label="Subject Initials">
            <TextInput testID="initials" value={initials} onChangeText={setInitials} style={s.input} />
          </Field>

          <Field label="Full Name *">
            <TextInput testID="full-name" value={fullName} onChangeText={setFullName} style={s.input} />
          </Field>

          <View style={{ flexDirection: "row", gap: 12 }}>
            <View style={{ flex: 1 }}>
              <Field label="Date of Birth *">
                <TextInput testID="dob" value={dob} onChangeText={(value) => setDob(formatDateInput(value))} keyboardType="number-pad" maxLength={10} style={s.input} />
              </Field>
            </View>
            <View style={{ flex: 1 }}>
              <Field label="Gender" active={genderOpen}>
                <Pressable testID="gender-toggle" onPress={() => setGenderOpen(o => !o)} style={[s.input, { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }]}>
                  <Text numberOfLines={1} style={{ color: gender ? C.fg : C.muted, fontSize: 14, lineHeight: 20, flex: 1, paddingRight: 12 }}>{gender || "Select"}</Text>
                  <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: "90deg" }] }} />
                </Pressable>
                {genderOpen && (
                  <View style={s.dropdown}>
                    {["Male", "Female", "Other", "Prefer not to say"].map(g => (
                      <Pressable key={g} testID={`gender-${g}`} onPress={() => { setGender(g); setGenderOpen(false); }} style={s.dropdownRow}>
                        <Text style={{ color: C.fg }}>{g}</Text>
                      </Pressable>
                    ))}
                  </View>
                )}
              </Field>
            </View>
          </View>

          <Field label="Phone *">
            <View style={{ flexDirection: "row", gap: 8 }}>
              <View style={s.prefix}><Text style={{ color: C.muted, fontSize: 14 }}>+91</Text></View>
              <TextInput testID="phone" value={phone} onChangeText={setPhone} keyboardType="phone-pad" style={[s.input, { flex: 1 }]} />
            </View>
          </Field>

          <Field label="Email *">
            <TextInput
              testID="email"
              value={email}
              onChangeText={(value) => { setEmail(value); setEmailDuplicate(null); }}
              onBlur={() => void checkInvitationAvailability("email")}
              keyboardType="email-address"
              autoCapitalize="none"
              style={[s.input, emailDuplicate && s.duplicateInput]}
            />
            {emailDuplicate ? <InlineDuplicate message={emailDuplicate} /> : null}
          </Field>

          <Field label="Preferred Language" active={langOpen}>
            <Pressable testID="lang-toggle" onPress={() => setLangOpen(o => !o)} style={[s.input, { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }]}>
              <Text numberOfLines={1} style={{ color: C.fg, fontSize: 14, lineHeight: 20, flex: 1, paddingRight: 12 }}>{lang}</Text>
              <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: "90deg" }] }} />
            </Pressable>
            {langOpen && (
              <View style={s.dropdown}>
                {["English", "Hindi", "Tamil", "Telugu"].map(l => (
                  <Pressable key={l} testID={`lang-${l}`} onPress={() => { setLang(l); setLangOpen(false); }} style={s.dropdownRow}>
                    <Text style={{ color: C.fg }}>{l}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </Field>

          <Field label="Assign to Trial *" active={trialOpen}>
            <Pressable testID="trial-toggle" disabled={trialsLoading || !trials.length} onPress={() => setTrialOpen(o => !o)} style={[s.input, { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }]}>
              {trialsLoading ? (
                <ActivityIndicator size="small" color={C.primary} />
              ) : (
                <Text style={{ color: selectedTrial ? C.fg : C.muted, fontSize: 14, lineHeight: 20, flex: 1, paddingRight: 12 }} numberOfLines={1}>
                  {selectedTrial ? trialLabel(selectedTrial) : "No trials available"}
                </Text>
              )}
              <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: "90deg" }] }} />
            </Pressable>
            {trialOpen && trials.length > 0 && (
              <View style={s.dropdown}>
                {trials.map(t => (
                  <Pressable key={t.id} testID={`trial-opt-${t.id}`} onPress={() => { setTrialId(t.id); setTrialOpen(false); setScheduleGenerated(false); setScheduleVisits([]); }} style={s.dropdownRow}>
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
                style={[s.input, { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }]}
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
                    <ChevronRight size={16} color={C.muted} style={{ transform: [{ rotate: "90deg" }] }} />
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
                      <Text style={s.stateActionText}>Open Team</Text>
                    </Pressable>
                  </View>
                </View>
              ) : null}
            </Field>
          )}

          {/* Patient Access Note */}
          <View style={s.infoNote}>
            <Sparkles size={16} color={C.info} />
            <Text style={{ fontSize: 12, color: C.info, flex: 1 }}>
              The patient will receive an invitation to access the app using the profile created here. Their login is linked to this subject record.
            </Text>
          </View>

          {/* Divider */}
          <View style={{ flexDirection: "row", alignItems: "center", gap: 16, paddingVertical: 8 }}>
            <View style={{ flex: 1, height: 1, backgroundColor: "#D9D2C7" }} />
            <Text style={{ color: C.muted, fontSize: 13, fontWeight: "600" }}>Visit Dates</Text>
            <View style={{ flex: 1, height: 1, backgroundColor: "#D9D2C7" }} />
          </View>

          <Field label="Baseline Date *">
            <View style={{ position: "relative" }}>
              <TextInput
                testID="baseline"
                value={baseline}
                onChangeText={(value) => {
                  setBaseline(/^[0-9/]*$/.test(value) ? formatDateInput(value) : value);
                  setScheduleGenerated(false);
                  setScheduleVisits([]);
                }}
                style={[s.input, { paddingRight: 48, borderWidth: 2, borderColor: C.primary }]}
              />
              <CalIcon size={20} color={C.primary} style={{ position: "absolute", right: 16, top: 14 }} />
            </View>
          </Field>

          <Pressable
            testID="generate-schedule"
            onPress={() => void generateSchedule()}
            disabled={scheduleLoading}
            style={s.generateSchedule}
          >
            <Sparkles size={16} color={C.primary} />
            <Text style={s.generateScheduleText}>{scheduleLoading ? "Generating…" : "Generate Schedule"}</Text>
          </Pressable>

          {/* Auto-calculated dates */}
          {scheduleGenerated ? <View style={s.autoCalc}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Sparkles size={20} color={C.primary} />
              <Text style={{ color: C.fg, fontWeight: "600", fontSize: 15 }}>Auto-calculated Dates</Text>
            </View>
            <View style={{ gap: 8 }}>
              {visible.map((v, index) => (
                <View key={v.visit_template_id || `${v.name}-${index}`} style={{ flexDirection: "row", justifyContent: "space-between", gap: 12 }}>
                  <Text style={{ color: C.muted, fontSize: 13, flex: 1 }} numberOfLines={1}>{v.name || `Visit ${v.visit_number || index + 1}`}</Text>
                  <Text style={{ color: v.status === "manual_review" ? C.destructive : C.fg, fontWeight: "600", fontSize: 13 }}>
                    {v.scheduled_date ? formatIsoCalendarDate(v.scheduled_date) : "Needs review"}
                  </Text>
                </View>
              ))}
            </View>
            <Pressable testID="toggle-all-visits" onPress={() => setShowAll(a => !a)} style={{ marginTop: 12, flexDirection: "row", alignItems: "center", gap: 4 }}>
              <Text style={{ color: C.accent, fontWeight: "600", fontSize: 13 }}>{showAll ? "Show Less" : `View All ${scheduleVisits.length} Visits`}</Text>
              <ChevronRight size={16} color={C.accent} style={{ transform: [{ rotate: showAll ? "-90deg" : "90deg" }] }} />
            </Pressable>
          </View> : null}

          {/* Error banner (server duplicate 409 or generic failure) */}
          {error && (
            <View testID="add-patient-error" style={s.dupWarn}>
              <AlertTriangle size={16} color={C.destructive} />
              <Text style={{ fontSize: 12, fontWeight: "600", color: C.destructive, flex: 1 }}>{error}</Text>
            </View>
          )}

          {/* Submit */}
          <Pressable
            testID="add-patient-submit"
            onPress={submit}
            disabled={!canSubmit}
            style={[s.submit, !canSubmit && { backgroundColor: C.border }]}
          >
            <Text style={{ color: !canSubmit ? C.muted : C.primaryFg, fontSize: 15, fontWeight: "700" }}>
              {saving ? "Adding…" : "Add Patient"}
            </Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function Field({ label, children, active = false }: any) {
  return (
    <View style={{ position: "relative", zIndex: active ? 100 : 1, elevation: active ? 100 : 0 }}>
      <Text style={{ fontSize: 13, fontWeight: "500", color: "rgba(46,27,51,0.80)", marginBottom: 6 }}>{label}</Text>
      {children}
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
  appBar: { flexDirection: "row", alignItems: "center", paddingHorizontal: 8, paddingVertical: 8 },
  backBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  appBarTitle: { flex: 1, fontSize: 18, fontWeight: "700", color: C.fg, textAlign: "center" },
  input: { paddingHorizontal: 16, paddingVertical: 14, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, color: C.fg, fontSize: 14 },
  prefix: { paddingHorizontal: 16, paddingVertical: 14, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  dupWarn: { marginTop: 8, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.05)", borderWidth: 1, borderColor: "rgba(192,57,43,0.20)" },
  duplicateInput: { borderColor: C.destructive, borderWidth: 2 },
  inlineDuplicate: { marginTop: 6, flexDirection: "row", alignItems: "flex-start", gap: 6 },
  inlineDuplicateText: { flex: 1, color: C.destructive, fontSize: 12, lineHeight: 17 },
  dropdown: { position: "absolute", top: "100%", left: 0, right: 0, marginTop: 4, zIndex: 30, elevation: 30, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, overflow: "hidden" },
  dropdownRow: { paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: C.border },
  inlineState: { flexDirection: "row", alignItems: "center", gap: 8 },
  piStateCard: { marginTop: 8, flexDirection: "row", alignItems: "flex-start", gap: 10, padding: 12, borderRadius: 14, backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  piStateText: { color: C.destructive, fontSize: 12, lineHeight: 17 },
  stateAction: { alignSelf: "flex-start", minHeight: 32, flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: "rgba(166,33,63,0.08)" },
  stateActionText: { color: C.primary, fontSize: 12, fontWeight: "700" },
  infoNote: { flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 12, borderRadius: 14, backgroundColor: "rgba(123,107,184,0.05)", borderWidth: 1, borderColor: "rgba(123,107,184,0.20)" },
  autoCalc: { backgroundColor: "rgba(123,107,184,0.05)", borderRadius: 16, padding: 16 },
  generateSchedule: { minHeight: 46, borderRadius: 14, borderWidth: 1, borderColor: "rgba(166,33,63,0.35)", backgroundColor: "rgba(166,33,63,0.06)", flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 8 },
  generateScheduleText: { color: C.primary, fontSize: 14, fontWeight: "700" },
  submit: { paddingVertical: 16, borderRadius: 999, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
});
