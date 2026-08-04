import React, { useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Eye, EyeOff, Check, ChevronDown } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";

// Three questions, each from a distinct pool so the same one can't be picked twice.
const QUESTION_POOLS: string[][] = [
  ["What is the name of your first pet?", "What was the name of your first school?", "What was your childhood nickname?"],
  ["What city were you born in?", "What is your favourite book?", "What was the make of your first car?"],
  ["What is your mother's maiden name?", "What is the name of your closest childhood friend?", "In what town did your parents meet?"],
];

const CORE = new Set(["fullName", "email", "phone", "phoneCountry", "orgName", "inviteToken"]);

function QuestionSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} style={[s.input, { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
        <Text style={{ flex: 1, fontSize: 14, color: value ? colors.foreground : colors.mutedFg + "99", fontFamily: fonts.regular }} numberOfLines={1}>
          {value || "Please select a question"}
        </Text>
        <ChevronDown size={18} color={colors.mutedFg} />
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={s.selectOverlay} onPress={() => setOpen(false)}>
          <View style={s.selectSheet}>
            {options.map((o) => {
              const on = o === value;
              return (
                <Pressable key={o} onPress={() => { onChange(o); setOpen(false); }} style={[s.selectItem, on && { backgroundColor: colors.secondary + "55" }]}>
                  <Text style={{ flex: 1, fontSize: 14, color: on ? colors.primary : colors.foreground, fontFamily: on ? fonts.semibold : fonts.regular }}>{o}</Text>
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

export default function SecurityQuestions() {
  const router = useRouter();
  const { role, payload } = useLocalSearchParams<{ role: string; variant: string; payload: string }>();
  const fld = (() => { try { return JSON.parse(payload || "{}"); } catch { return {}; } })();

  const [questions, setQuestions] = useState<string[]>(["", "", ""]);
  const [answers, setAnswers] = useState<string[]>(["", "", ""]);
  const [revealed, setRevealed] = useState<boolean[]>([false, false, false]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  // Avoid creating two competing OTP sessions when Continue is tapped quickly.
  const startingRef = useRef(false);

  const setQ = (i: number, v: string) => setQuestions((p) => p.map((x, idx) => (idx === i ? v : x)));
  const setA = (i: number, v: string) => setAnswers((p) => p.map((x, idx) => (idx === i ? v : x)));
  const toggle = (i: number) => setRevealed((p) => p.map((x, idx) => (idx === i ? !x : x)));

  const allComplete = questions.every((q) => q) && answers.every((a) => a.trim().length > 0) && !loading;

  // Leaving this step sends the OTP: we start the pending registration (no password yet —
  // the password is set at step 5 via /register/complete after OTP is verified).
  const submit = async () => {
    if (!allComplete || startingRef.current) return;
    startingRef.current = true;
    setLoading(true); setErr("");
    try {
      const profile: Record<string, any> = {};
      Object.keys(fld).forEach((k) => { if (!CORE.has(k) && fld[k]) profile[k] = fld[k]; });
      // Step 2 already normalized the number to E.164 against the chosen country,
      // so pass it through untouched rather than re-guessing a calling code.
      const phone = fld.phone ? String(fld.phone).trim() : undefined;
      const security_questions = questions.map((q, i) => ({ question: q, answer: answers[i] }));
      const body: any = {
        full_name: fld.fullName,
        role: role || "patient",
        phone,
        organization: fld.orgName || undefined,
        profile,
        security_questions,
      };
      if (fld.email) body.email = String(fld.email).trim().toLowerCase();
      if (fld.inviteToken) body.invite_token = String(fld.inviteToken);

      const { data } = await api.post("/auth/register/start", body);
      router.push({
        pathname: "/(auth)/verify-otp",
        params: {
          registration_id: data.registration_id,
          channels: JSON.stringify(data.channels),
          email: data.email || "",
          phone: data.phone || "",
          role: role || "patient",
          invited: fld.inviteToken ? "1" : "",
        },
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Could not start verification. Please try again.");
    } finally {
      startingRef.current = false;
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow="Step 3 of 5" title="Only you would know" subtitle="Three security questions help us verify it's you and recover your account if it's ever locked." onBack={() => router.back()} step={3} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {questions.map((selected, i) => (
            <Rise key={i} delay={200 + i * 90}>
              <View style={s.card}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.accent, fontVariant: ["tabular-nums"] }}>{String(i + 1).padStart(2, "0")}</Text>
                  <Eyebrow color={colors.mutedFg}>Security question</Eyebrow>
                </View>
                <QuestionSelect value={selected} options={QUESTION_POOLS[i]} onChange={(v) => setQ(i, v)} />
                <View style={{ position: "relative", marginTop: 12 }}>
                  <TextInput
                    value={answers[i]}
                    onChangeText={(v) => setA(i, v)}
                    secureTextEntry={!revealed[i]}
                    placeholder="Your answer"
                    placeholderTextColor={colors.mutedFg + "8C"}
                    autoCapitalize="none"
                    style={[s.input, { paddingRight: 44 }]}
                  />
                  <Pressable onPress={() => toggle(i)} hitSlop={8} style={s.eye}>
                    {revealed[i] ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}
                  </Pressable>
                </View>
              </View>
            </Rise>
          ))}

          <Small style={{ textAlign: "center", marginTop: spacing.md, opacity: 0.8, lineHeight: 19 }}>
            Choose answers that don’t change over time and that only you would know.
          </Small>
          {err ? <Small color={colors.destructive} style={{ marginTop: 12, textAlign: "center" }}>{err}</Small> : null}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={submit} disabled={!allComplete} style={[s.cta, allComplete ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: allComplete ? colors.primaryFg : colors.mutedFg }}>{loading ? "Sending codes…" : "Continue"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  card: { borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md, marginBottom: spacing.md },
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 14, color: colors.foreground, fontFamily: fonts.regular },
  eye: { position: "absolute", right: 12, top: 0, bottom: 0, justifyContent: "center" },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  selectOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", justifyContent: "center", paddingHorizontal: spacing.lg },
  selectSheet: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, overflow: "hidden", paddingVertical: 4 },
  selectItem: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 14 },
});
