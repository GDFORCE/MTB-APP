import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { Check, Copy, Pencil, RefreshCw, Users } from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { fonts } from "@/src/theme/tokens";
import { getKitModel, type SavedKitModel } from "@/src/features/kit/api";
import type { KitQuestion } from "@/src/features/kit";
import { FlowButton, FlowScreen, flowStyles as f } from "@/src/features/kit/FlowScreen";
import { FOCUS as G } from "@/src/features/kit/focus";
import { PatientTimeline, usePatientTimeline } from "@/src/features/kit/PatientTimeline";
import {
  anchorDateProblems, anchorRangeWarnings, countedFromText, dmyToIso, enrolmentAnchors, enterableAnchors, rangeText,
} from "@/src/features/kit/participant";
import { sanitizeName } from "@/src/lib/validators";

type Trial = { id: string; title?: string; protocol_id?: string; condition?: string; phase?: string };
type PiOption = { id: string; full_name?: string; email?: string; role?: string };
type Step = "trial" | "patient" | "contact" | "schedule" | "review" | "sent";
const STEPS: Step[] = ["trial", "patient", "contact", "schedule", "review"];

const GENDERS = ["Male", "Female", "Other", "Prefer not to say"];
const LANGUAGES = ["English", "Hindi", "Tamil", "Telugu"];

// Accepts "5 May 2025", "2025-05-05" or "05/05/2025" → Date (or null).
function parseDate(s: string): Date | null {
  const t = s.trim();
  const dmy = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (dmy) {
    const day = +dmy[1], month = +dmy[2], year = +dmy[3];
    const d = new Date(year, month - 1, day);
    // Date() rolls an out-of-range day/month into a later date; confirm it is what was typed.
    return d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day ? d : null;
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
// A validation error detail can be our own string or a list of Pydantic errors; only show strings.
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
const initialsFromName = (value: string) => value.trim().split(/\s+/).filter(Boolean).slice(0, 2)
  .map((part) => part[0]?.toUpperCase() || "").join("");

/**
 * Add a patient as a guided flow in the same style as Add Trial: pick the trial, enter who the
 * patient is and how to reach them, enter the protocol dates and per-patient answers, check the
 * resulting visit dates, then send the invitation.
 */
export default function AddPatient() {
  const router = useRouter();
  const { trialId: requestedTrialId } = useLocalSearchParams<{ trialId?: string }>();
  const { user } = useAuth();
  const needsPiSelection = user?.role === "smo" || user?.role === "site";
  const scrollRef = useRef<ScrollView>(null);
  const [step, setStep] = useState<Step>("trial");
  const [subjectId, setSubjectId] = useState("");
  const [initials, setInitials] = useState("");
  const [initialsEdited, setInitialsEdited] = useState(false);
  const [fullName, setFullName] = useState("");
  const [dob, setDob] = useState("");
  const [gender, setGender] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [lang, setLang] = useState("English");
  const [trials, setTrials] = useState<Trial[]>([]);
  const [trialsLoading, setTrialsLoading] = useState(true);
  const [trialId, setTrialId] = useState<string | null>(null);
  const [pis, setPis] = useState<PiOption[]>([]);
  const [piId, setPiId] = useState<string | null>(null);
  const [piLoading, setPiLoading] = useState(needsPiSelection);
  const [piLoadError, setPiLoadError] = useState<string | null>(null);
  const [piPermissionDenied, setPiPermissionDenied] = useState(false);
  // The trial's reviewed protocol schedule (from Add Trial). The patient's dates are computed from
  // it, their start date, their other protocol dates and their answers.
  const [kit, setKit] = useState<SavedKitModel | null>(null);
  const [kitState, setKitState] = useState<"loading" | "ready" | "none" | "error">("loading");
  const [kitAnswers, setKitAnswers] = useState<Record<string, string>>({});
  const [startDate, setStartDate] = useState("");
  // Other protocol dates (randomisation / Day 1, run-in, ...), DD/MM/YYYY by anchor id.
  const [keyDates, setKeyDates] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subjectDuplicate, setSubjectDuplicate] = useState<string | null>(null);
  const [emailDuplicate, setEmailDuplicate] = useState<string | null>(null);
  const [sent, setSent] = useState<{ code: string; byEmail: boolean; name: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const go = (next: Step) => { setError(null); setStep(next); scrollRef.current?.scrollTo({ y: 0, animated: false }); };

  const loadPis = useCallback(async () => {
    if (!needsPiSelection) { setPiLoading(false); return; }
    setPiLoading(true);
    setPiLoadError(null);
    setPiPermissionDenied(false);
    try {
      const response = await api.get("/team");
      const available = (Array.isArray(response.data) ? response.data : []).filter((m: PiOption) => m.role === "pi");
      setPis(available);
      setPiId((current) => current && available.some((pi: PiOption) => pi.id === current) ? current : available[0]?.id || null);
    } catch (e: any) {
      const status = e?.response?.status;
      setPis([]);
      setPiId(null);
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
      } catch { if (alive) setError("Couldn't load trials. Go back and try again."); }
      finally { if (alive) setTrialsLoading(false); }
    })();
    return () => { alive = false; };
  }, [requestedTrialId]);

  useEffect(() => { void loadPis(); }, [loadPis]);

  useEffect(() => {
    let alive = true;
    setKit(null);
    setKitAnswers({});
    setKeyDates({});
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
  // Dates the protocol places at or before Day 1 (run-in, randomisation) are asked here. Dates that
  // fall between later visits (surgery, ...) are added on the patient's schedule once they happen.
  const otherDates = kit ? enrolmentAnchors(kit.model) : [];
  const laterDates = kit ? enterableAnchors(kit.model).filter((a) => !otherDates.includes(a)) : [];
  const badDates = otherDates.filter((a) => (keyDates[a.id] || "").trim() && !dmyToIso(keyDates[a.id]));
  const answersWithDates = useMemo(() => {
    const next = { ...kitAnswers };
    for (const [id, value] of Object.entries(keyDates)) { const iso = dmyToIso(value); if (iso) next["@" + id] = iso; }
    return next;
  }, [kitAnswers, keyDates]);
  const dateProblems: Record<string, string> = kit && startAnchor ? anchorDateProblems(kit.model, startAnchor.id, startIso, answersWithDates) : {};
  const firstDateProblem = Object.values(dateProblems)[0];
  // Outside the protocol's range is a deviation the site may need to record: warned, not refused.
  const rangeWarnings: Record<string, string> = kit && startAnchor ? anchorRangeWarnings(kit.model, startAnchor.id, startIso, answersWithDates) : {};
  const participant = useMemo(() => (kit && startAnchor && startIso
    ? { startAnchor: startAnchor.id, startDate: startIso, answers: answersWithDates } : null), [kit, startAnchor, startIso, answersWithDates]);
  const preview = usePatientTimeline(kit?.model, kit?.site_answers, participant);

  const selectedTrial = trials.find((t) => t.id === trialId);
  const selectedPi = pis.find((pi) => pi.id === piId);
  const suggestedInitials = initialsFromName(fullName);
  const phoneDigits = phone.replace(/\D/g, "");
  const normalizedEmail = email.trim().toLowerCase();
  const optionalEmailValid = !normalizedEmail || /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(normalizedEmail);

  // What each step still needs (null = ready to continue).
  const trialNeeds = !trialId ? "Choose a trial"
    : needsPiSelection && !piId ? "Choose the responsible PI"
    : kitState === "loading" ? "Loading the visit schedule…"
    : kitState === "none" ? "This trial has no visit schedule yet"
    : kitState === "error" ? "Couldn't load this trial's visit schedule" : null;
  const patientNeeds = !fullName.trim() ? "Enter the full name"
    : !subjectId.trim() ? "Enter the subject number"
    : !parseDate(dob) ? "Enter the date of birth as DD/MM/YYYY"
    : subjectDuplicate ? "Use a different subject number" : null;
  const contactNeeds = phoneDigits.length !== 10 ? "Enter a 10-digit phone number"
    : !optionalEmailValid ? "Enter a valid email or leave it blank"
    : emailDuplicate ? "Use a different email" : null;
  const scheduleNeeds = !startIso ? `Enter the ${(startAnchor?.label || "start").toLowerCase()} date`
    : badDates.length ? `Enter the ${badDates[0].label.toLowerCase()} date as DD/MM/YYYY`
    : firstDateProblem ? firstDateProblem
    : unanswered.length ? `Answer: ${unanswered.map((q) => q.label).join(", ")}`
    : !preview ? "The schedule could not be calculated" : null;
  const canSubmit = !trialNeeds && !patientNeeds && !contactNeeds && !scheduleNeeds && !!participant && !saving;

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

  const checkAvailability = async (field: "subject" | "email") => {
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
      (field === "subject" ? setSubjectDuplicate : setEmailDuplicate)(result?.available === false ? result.message : null);
    } catch {
      // Final validation stays server-side when the invitation is sent.
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
        trial_id: trialId,
        pi_id: needsPiSelection ? piId : undefined,
        kit: participant,
        subject_id: subjectId ? `SUBJ-${subjectId}` : undefined,
        dob: parsedDob ? toISO(parsedDob) : (dob || undefined),
        gender: gender || undefined,
        language: lang || undefined,
        avatar_initials: initials || suggestedInitials || undefined,
        enrolled_date: new Date().toISOString().slice(0, 10),
      });
      setSent({ code: String(response.data?.token || ""), byEmail: !!normalizedEmail, name: fullName.trim() });
      setCopied(false);
      go("sent");
    } catch (e: any) {
      if (e?.response?.status === 422 && typeof e?.response?.data?.detail === "string") setError(e.response.data.detail);
      else if (e?.response?.status === 409) setError(errorMessage(e, `SUBJ-${subjectId} already exists in this trial.`));
      else setError("Couldn't add the patient. Check the details and try again.");
    } finally { setSaving(false); }
  };

  // Start the next patient on the same trial.
  const addAnother = () => {
    setSubjectId(""); setInitials(""); setInitialsEdited(false); setFullName(""); setDob(""); setGender("");
    setPhone(""); setEmail(""); setLang("English"); setKitAnswers({}); setStartDate(""); setKeyDates({});
    setSubjectDuplicate(null); setEmailDuplicate(null); setSent(null);
    go("patient");
  };

  const stepNumber = STEPS.indexOf(step) + 1;
  const back = () => step === "trial" ? router.back() : go(STEPS[stepNumber - 2]);
  const footer = (needs: string | null, label: string, next: () => void, testID?: string) => <>
    {!!needs && <Text style={[f.hint, { textAlign: "center" }]}>{needs}</Text>}
    <FlowButton testID={testID} label={label} arrow disabled={!!needs} onPress={next} />
  </>;
  const common = { title: "Add patient", step: stepNumber, steps: STEPS.length, onBack: back, scrollRef };
  const answerLabel = (q: KitQuestion) => {
    const value = kitAnswers[q.id];
    if (q.kind === "boolean") return value === "yes" ? "Yes" : value === "no" ? "No" : "";
    return q.options.find((o) => o.value === value)?.label || value || "";
  };

  return <>
    {step === "trial" && <FlowScreen {...common} eyebrow="Trial" heading="Which trial is the patient joining?"
      sub="Their visit dates come from this trial's reviewed protocol schedule."
      footer={footer(trialNeeds, "Continue", () => go("patient"), "trial-continue")}>
      <View style={s.list}>
        {trialsLoading && <ActivityIndicator color={G.accent} style={{ marginTop: 12 }} />}
        {!trialsLoading && !trials.length && <Text style={f.hint}>No trials are available to you yet.</Text>}
        {trials.map((t) => {
          const on = t.id === trialId;
          return <Pressable key={t.id} testID={`trial-opt-${t.id}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
            onPress={() => setTrialId(t.id)} style={[s.option, on && s.optionOn]}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.optionTitle} numberOfLines={1}>{t.protocol_id || t.title || "Trial"}</Text>
              {!!(t.title && t.protocol_id) && <Text style={s.optionSub} numberOfLines={2}>{t.title}</Text>}
              {!!(t.condition || t.phase) && <Text style={s.optionSub} numberOfLines={1}>{[t.phase, t.condition].filter(Boolean).join(" · ")}</Text>}
            </View>
            <View style={[s.radio, on && s.radioOn]}>{on && <Check size={14} color="#FFFFFF" strokeWidth={3} />}</View>
          </Pressable>;
        })}
      </View>
      {!!trialId && kitState === "loading" && <Text style={[f.hint, { marginTop: 14 }]}>Loading the visit schedule…</Text>}
      {!!trialId && kitState === "none" && <View style={s.notice}>
        <Text style={s.noticeText}>This trial has no visit schedule yet. Add its protocol in Add Trial first.</Text>
      </View>}
      {!!trialId && kitState === "error" && <Text style={[f.error, { marginTop: 14 }]}>Couldn&apos;t load this trial&apos;s visit schedule.</Text>}

      {needsPiSelection && <View style={{ marginTop: 28, gap: 10 }}>
        <Text style={f.sectionTitle}>Responsible PI</Text>
        {piLoading && <View testID="pi-loading" style={s.inline}><ActivityIndicator color={G.accent} /><Text style={f.hint}>Loading Principal Investigators…</Text></View>}
        {!piLoading && piLoadError && <View testID={piPermissionDenied ? "pi-permission-error" : "pi-load-error"} style={s.notice}>
          <Text style={[s.noticeText, { color: G.redInk }]}>{piLoadError}</Text>
          {!piPermissionDenied && <Pressable testID="pi-retry" onPress={() => void loadPis()} style={s.link}>
            <RefreshCw size={14} color={G.accent} /><Text style={s.linkText}>Retry</Text>
          </Pressable>}
        </View>}
        {!piLoading && !piLoadError && !pis.length && <View testID="pi-empty" style={s.notice}>
          <Text style={s.noticeText}>No Principal Investigator is available. Add one to your organization first.</Text>
          <Pressable testID="pi-open-team" onPress={() => router.push("/(app)/clinical/team")} style={s.link}>
            <Users size={14} color={G.accent} /><Text style={s.linkText}>Open organization members</Text>
          </Pressable>
        </View>}
        {pis.map((pi) => {
          const on = pi.id === piId;
          return <Pressable key={pi.id} testID={`pi-opt-${pi.id}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
            onPress={() => setPiId(pi.id)} style={[s.option, on && s.optionOn]}>
            <Text style={[s.optionTitle, { flex: 1 }]}>{pi.full_name || pi.email || "Principal Investigator"}</Text>
            <View style={[s.radio, on && s.radioOn]}>{on && <Check size={14} color="#FFFFFF" strokeWidth={3} />}</View>
          </Pressable>;
        })}
      </View>}
      {!!error && <Text style={[f.error, { marginTop: 12 }]}>{error}</Text>}
    </FlowScreen>}

    {step === "patient" && <FlowScreen {...common} eyebrow="Patient" heading="Who is the patient?"
      sub={selectedTrial ? `Joining ${selectedTrial.protocol_id || selectedTrial.title}.` : undefined}
      footer={footer(patientNeeds, "Continue", () => go("contact"), "patient-continue")}>
      <View style={s.fields}>
        <Field label="Full name">
          <TextInput testID="full-name" value={fullName} onChangeText={updateFullName} placeholder="Patient's full name"
            placeholderTextColor={G.muted} autoCapitalize="words" style={f.input} />
        </Field>
        <Field label="Subject number" hint="Unique within this trial.">
          <View style={s.pair}>
            <View style={s.prefix}><Text style={s.prefixText}>SUBJ-</Text></View>
            <TextInput testID="subject-id" value={subjectId}
              onChangeText={(value) => { setSubjectId(value.replace(/^SUBJ-/i, "").replace(/\s/g, "").toUpperCase()); setSubjectDuplicate(null); }}
              onBlur={() => void checkAvailability("subject")} placeholder="0001" placeholderTextColor={G.muted}
              autoCapitalize="characters" style={[f.input, { flex: 1 }, !!subjectDuplicate && s.inputError]} />
          </View>
          {!!subjectDuplicate && <Text style={f.error}>{subjectDuplicate}</Text>}
        </Field>
        <Field label="Initials" hint={initialsEdited && suggestedInitials ? undefined : "Filled in from the name. You can change them."}
          action={initialsEdited && suggestedInitials ? <Pressable onPress={() => { setInitials(suggestedInitials); setInitialsEdited(false); }} hitSlop={8}>
            <Text style={s.linkText}>Use {suggestedInitials}</Text>
          </Pressable> : undefined}>
          <TextInput testID="initials" value={initials} onChangeText={updateInitials} placeholder="Auto" placeholderTextColor={G.muted}
            autoCapitalize="characters" maxLength={4} style={f.input} />
        </Field>
        <Field label="Date of birth">
          <TextInput testID="dob" value={dob} onChangeText={(value) => setDob(formatDateInput(value))} placeholder="DD/MM/YYYY"
            placeholderTextColor={G.muted} keyboardType="number-pad" maxLength={10} style={f.input} />
        </Field>
        <Field label="Gender (optional)">
          <Chips testID="gender" options={GENDERS} value={gender} onChange={(value) => setGender(value === gender ? "" : value)} />
        </Field>
      </View>
    </FlowScreen>}

    {step === "contact" && <FlowScreen {...common} eyebrow="Contact" heading="How do we reach them?"
      sub="The invitation goes to this phone number, and to the email if you add one."
      footer={footer(contactNeeds, "Continue", () => go("schedule"), "contact-continue")}>
      <View style={s.fields}>
        <Field label="Phone">
          <View style={s.pair}>
            <View style={s.prefix}><Text style={s.prefixText}>+91</Text></View>
            <TextInput testID="phone" value={phone} onChangeText={(value) => setPhone(value.replace(/\D/g, "").slice(0, 10))}
              placeholder="10-digit mobile number" placeholderTextColor={G.muted} keyboardType="phone-pad" maxLength={10} style={[f.input, { flex: 1 }]} />
          </View>
        </Field>
        <Field label="Email (optional)">
          <TextInput testID="email" value={email} onChangeText={(value) => { setEmail(value); setEmailDuplicate(null); }}
            onBlur={() => void checkAvailability("email")} placeholder="patient@example.com" placeholderTextColor={G.muted}
            keyboardType="email-address" autoCapitalize="none" autoCorrect={false} style={[f.input, !!emailDuplicate && s.inputError]} />
          {!!emailDuplicate && <Text style={f.error}>{emailDuplicate}</Text>}
        </Field>
        <Field label="Preferred language">
          <Chips testID="lang" options={LANGUAGES} value={lang} onChange={setLang} />
        </Field>
      </View>
    </FlowScreen>}

    {step === "schedule" && <FlowScreen {...common} eyebrow="Visit schedule" heading="When does their schedule start?"
      sub="The patient's visit dates are worked out from these dates and answers."
      footer={footer(scheduleNeeds, "Review", () => go("review"), "schedule-continue")}>
      {!kit ? <ActivityIndicator color={G.accent} style={{ marginTop: 24 }} /> : <View style={s.fields}>
        <Field label={`${startAnchor?.label || "Start"} date`}>
          <TextInput testID="kit-start-date" value={startDate} onChangeText={(value) => setStartDate(formatDateInput(value))}
            placeholder="DD/MM/YYYY" placeholderTextColor={G.muted} keyboardType="number-pad" maxLength={10} style={f.input} />
        </Field>
        {otherDates.map((a) => {
          const typed = (keyDates[a.id] || "").trim();
          const problem = dateProblems[a.id];
          const warning = rangeWarnings[a.id];
          const hint = problem || warning
            || [rangeText(kit.model, a), typed ? null : countedFromText(kit.model, a)
              || "Leave it empty if not known yet and add it on the patient's schedule later."].filter(Boolean).join(" ");
          return <Field key={a.id} label={`${a.label} date${a.is_day1 ? " (Day 1)" : ""}`}>
            <TextInput testID={`kit-date-${a.id}`} value={keyDates[a.id] || ""}
              onChangeText={(value) => setKeyDates((current) => ({ ...current, [a.id]: formatDateInput(value) }))}
              placeholder="DD/MM/YYYY" placeholderTextColor={G.muted} keyboardType="number-pad" maxLength={10}
              style={[f.input, !!problem && s.inputError]} />
            {!!hint && <Text style={problem ? f.error : warning ? [f.hint, { color: G.amberText }] : f.hint}>{hint}</Text>}
          </Field>;
        })}
        {!!laterDates.length && <Text style={f.hint}>
          Added on the patient&apos;s schedule when they happen: {laterDates.map((a) => a.label).join(", ")}.
        </Text>}
        {kitQuestions.map((q) => <Field key={q.id} label={q.text || q.label} hint={q.help || undefined}>
          {q.kind === "number"
            ? <TextInput value={kitAnswers[q.id] || ""} keyboardType="number-pad" placeholder="Number" placeholderTextColor={G.muted}
                onChangeText={(value) => setKitAnswers((current) => ({ ...current, [q.id]: value.replace(/[^0-9.]/g, "") }))} style={f.input} />
            : <View style={f.chips}>
                {(q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options).map((o) => {
                  const on = kitAnswers[q.id] === o.value;
                  return <Pressable key={o.value} testID={`kit-q-${q.id}-${o.value}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
                    onPress={() => setKitAnswers((current) => ({ ...current, [q.id]: o.value }))} style={[f.chip, on && f.chipOn]}>
                    <Text style={[f.chipText, on && f.chipTextOn]}>{o.label}</Text>
                  </Pressable>;
                })}
              </View>}
        </Field>)}
        {preview && !unanswered.length ? <View style={{ gap: 8 }}>
          <Text style={f.sectionTitle}>Their visit dates</Text>
          <PatientTimeline data={preview} maxRows={5} />
        </View> : <Text style={f.hint}>Enter the date{kitQuestions.length ? " and answers" : ""} to see the visit dates.</Text>}
      </View>}
    </FlowScreen>}

    {step === "review" && <FlowScreen {...common} eyebrow="Review" heading="Send the invitation?"
      sub="Check the details. The patient's account and schedule are created when they accept the invitation."
      footer={<>
        {!!error && <Text testID="add-patient-error" style={[f.error, { textAlign: "center" }]}>{error}</Text>}
        <FlowButton testID="add-patient-submit" label="Send invitation" loading={saving} disabled={!canSubmit} onPress={submit} />
      </>}>
      <Summary title="Trial" onEdit={() => go("trial")} rows={[
        ["Trial", selectedTrial ? selectedTrial.protocol_id || selectedTrial.title || "" : ""],
        ...(needsPiSelection ? [["PI", selectedPi?.full_name || selectedPi?.email || ""] as [string, string]] : []),
      ]} />
      <Summary title="Patient" onEdit={() => go("patient")} rows={[
        ["Name", fullName.trim()], ["Subject", subjectId ? `SUBJ-${subjectId}` : ""],
        ["Initials", initials || suggestedInitials], ["Date of birth", dob], ["Gender", gender || "Not given"],
      ]} />
      <Summary title="Contact" onEdit={() => go("contact")} rows={[
        ["Phone", phoneDigits ? `+91 ${phoneDigits}` : ""], ["Email", normalizedEmail || "Not given"], ["Language", lang],
      ]} />
      <Summary title="Visit schedule" onEdit={() => go("schedule")} rows={[
        [`${startAnchor?.label || "Start"} date`, startDate],
        ...otherDates.map((a) => [`${a.label} date`, keyDates[a.id] || "Enter later"] as [string, string]),
        ...kitQuestions.map((q) => [q.label, answerLabel(q)] as [string, string]),
      ]} />
      {preview && <View style={{ marginTop: 24, gap: 8 }}>
        <Text style={f.sectionTitle}>First visits</Text>
        <PatientTimeline data={preview} maxRows={3} />
      </View>}
    </FlowScreen>}

    {step === "sent" && sent && <FlowScreen title="Add patient" eyebrow="Invitation sent"
      heading={`${sent.name || "The patient"} is invited`}
      sub={sent.byEmail
        ? "They'll get an email invitation. Their account and visit schedule are created after they accept and register."
        : "Share this code with the patient. They verify their phone number when they join."}
      footer={<>
        <FlowButton label="Add another patient" onPress={addAnother} />
        <FlowButton label="Done" variant="secondary" onPress={() => router.back()} />
      </>}>
      {!sent.byEmail && !!sent.code && <View style={s.codeCard}>
        <Text style={f.hint}>Invitation code</Text>
        <Text testID="invite-code" selectable style={s.code}>{sent.code}</Text>
        <Pressable accessibilityRole="button" onPress={() => void Clipboard.setStringAsync(sent.code).then(() => setCopied(true))} style={s.copy}>
          {copied ? <Check size={16} color={G.accent} /> : <Copy size={16} color={G.accent} />}
          <Text style={s.linkText}>{copied ? "Copied" : "Copy code"}</Text>
        </Pressable>
      </View>}
    </FlowScreen>}
  </>;
}

function Field({ label, hint, action, children }: { label: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <View style={{ gap: 6 }}>
    <View style={s.labelRow}><Text style={f.label}>{label}</Text>{action}</View>
    {children}
    {!!hint && <Text style={f.hint}>{hint}</Text>}
  </View>;
}

function Chips({ options, value, onChange, testID }: { options: string[]; value: string; onChange: (value: string) => void; testID: string }) {
  return <View style={f.chips}>
    {options.map((option) => {
      const on = value === option;
      return <Pressable key={option} testID={`${testID}-${option}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
        onPress={() => onChange(option)} style={[f.chip, on && f.chipOn]}>
        <Text style={[f.chipText, on && f.chipTextOn]}>{option}</Text>
      </Pressable>;
    })}
  </View>;
}

function Summary({ title, rows, onEdit }: { title: string; rows: [string, string][]; onEdit: () => void }) {
  return <View style={s.summary}>
    <View style={s.summaryHead}>
      <Text style={s.summaryTitle}>{title}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel={`Edit ${title.toLowerCase()}`} onPress={onEdit} hitSlop={10} style={s.link}>
        <Pencil size={14} color={G.accent} /><Text style={s.linkText}>Edit</Text>
      </Pressable>
    </View>
    {rows.map(([label, value]) => <View key={label} style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={s.rowValue}>{value || "—"}</Text>
    </View>)}
  </View>;
}

const s = StyleSheet.create({
  list: { marginTop: 24, gap: 10 },
  fields: { marginTop: 28, gap: 22 },
  option: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 60, paddingHorizontal: 16, paddingVertical: 12, borderWidth: 1, borderColor: G.line, borderRadius: 14, backgroundColor: "#FFFFFF" },
  optionOn: { borderColor: G.accent, borderWidth: 2, backgroundColor: G.selected },
  optionTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 15 },
  optionSub: { color: G.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  radio: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: G.line, alignItems: "center", justifyContent: "center" },
  radioOn: { borderColor: G.accent, backgroundColor: G.accent },
  notice: { marginTop: 14, gap: 10, padding: 14, borderRadius: 12, backgroundColor: G.amberBg },
  noticeText: { color: G.amberInk, fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 },
  inline: { flexDirection: "row", alignItems: "center", gap: 10 },
  link: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "flex-start" },
  linkText: { color: G.accent, fontFamily: fonts.semibold, fontSize: 14 },
  labelRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  pair: { flexDirection: "row", gap: 8 },
  prefix: { minWidth: 64, minHeight: 50, paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: G.line, backgroundColor: G.selected, alignItems: "center", justifyContent: "center" },
  prefixText: { color: G.ink, fontFamily: fonts.semibold, fontSize: 14 },
  inputError: { borderColor: G.error, borderWidth: 2 },
  summary: { marginTop: 24, borderWidth: 1, borderColor: G.line, borderRadius: 14, paddingHorizontal: 16, paddingBottom: 4 },
  summaryHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingTop: 14, paddingBottom: 6 },
  summaryTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 16 },
  row: { flexDirection: "row", gap: 12, paddingVertical: 10, borderTopWidth: 1, borderTopColor: G.softLine },
  rowLabel: { width: 112, color: G.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 20 },
  rowValue: { flex: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 14, lineHeight: 20, textAlign: "right" },
  codeCard: { marginTop: 28, alignItems: "center", gap: 10, padding: 20, borderRadius: 14, backgroundColor: G.selected, borderWidth: 1, borderColor: G.line },
  code: { color: G.ink, fontFamily: fonts.bold, fontSize: 28, letterSpacing: 3 },
  copy: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 6 },
});
