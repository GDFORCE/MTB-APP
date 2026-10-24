import React, { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ArrowLeft, ArrowRight, Info, Minus, Plus } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import { patchKitModel } from "./api";
import { FOCUS } from "./focus";
import type { KitModel, KitQuestion } from "./index";

export type KitConfirmed = {
  model: KitModel; reviewAnswers: Record<string, string>; siteAnswers: Record<string, string>;
};

type Props = {
  visible: boolean;
  baseModel: KitModel;
  model: KitModel;
  reviewAnswers: Record<string, string>;
  siteAnswers: Record<string, string>;
  onConfirm: (confirmed: KitConfirmed) => void;
  /** Close without confirming. Only offered when answers were confirmed before. */
  onDismiss?: () => void;
};

// Quiet greens on white, so the one question on screen gets all the attention.
const G = FOCUS;

type Step = {
  id: string; kind: "review" | "site"; eyebrow: string; heading: string; context: string; why: string;
  options: { value: string; label: string }[]; proposed: string | null;
};

// "The protocol does not define windows. What deviation is acceptable?" -> the question as the
// heading, the sentences before it as the short line underneath.
function splitQuestion(text: string) {
  const sentences = text.trim().split(/(?<=[.!?])\s+/);
  if (sentences.length < 2) return { heading: text.trim(), context: "" };
  const at = sentences.findIndex((part) => part.endsWith("?"));
  const index = at >= 0 ? at : sentences.length - 1;
  return { heading: sentences[index], context: sentences.filter((_, i) => i !== index).join(" ") };
}

const optionsOf = (q: KitQuestion) =>
  q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options;

/**
 * The questions the protocol analysis could not settle on its own, one per screen: how to read
 * open points in the protocol, then the site settings. Nothing is preselected the first time;
 * the analysis's choice is marked "Proposed answer". Patient questions belong to Add Patient.
 */
export function KitQuestionsModal({ visible, baseModel, model, reviewAnswers, siteAnswers, onConfirm, onDismiss }: Props) {
  // Answers were confirmed before when the screen can be dismissed: start from them then.
  const confirmedBefore = !!onDismiss;
  const [review, setReview] = useState<Record<string, string>>({});
  const [site, setSite] = useState<Record<string, string>>({});
  const [step, setStep] = useState(0);
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const restart = () => {
    setReview(confirmedBefore ? reviewAnswers : {});
    setSite(confirmedBefore ? siteAnswers : {});
    setStep(0);
    setWhy(false);
    setError("");
  };
  useEffect(() => { if (visible) restart(); }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const steps: Step[] = [
    ...model.review_questions.map((rq) => ({
      id: rq.id, kind: "review" as const, eyebrow: "Protocol reading", ...splitQuestion(rq.text), why: rq.why,
      options: rq.options, proposed: rq.recommended,
    })),
    ...model.questions.filter((q) => q.scope === "study").map((q) => ({
      id: q.id, kind: "site" as const, eyebrow: "Site setting", heading: q.text, context: "", why: q.help,
      options: optionsOf(q), proposed: q.default != null ? String(q.default) : null,
    })),
  ];
  const current = steps[step];
  const answers = current?.kind === "site" ? site : review;
  const chosen = current ? answers[current.id] : undefined;
  const last = step === steps.length - 1;

  const choose = (value: string) => {
    if (!current) return;
    (current.kind === "site" ? setSite : setReview)((prev) => ({ ...prev, [current.id]: value }));
  };
  const go = (to: number) => { setStep(to); setWhy(false); setError(""); };
  const back = () => (step > 0 ? go(step - 1) : onDismiss?.());

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const changed = Object.keys(review).some((id) => review[id] !== reviewAnswers[id]);
      onConfirm({ model: changed ? await patchKitModel(baseModel, review) : model, reviewAnswers: review, siteAnswers: site });
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Could not apply your answers. Try again.");
    } finally { setBusy(false); }
  };

  if (!current) return null;
  return <Modal visible={visible} animationType="slide" onRequestClose={back}>
    <SafeAreaView style={s.screen} edges={["top", "bottom"]}>
      <View style={s.header}>
        <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={back} hitSlop={12}
          disabled={step === 0 && !onDismiss} style={[s.headerSide, step === 0 && !onDismiss && { opacity: 0 }]}>
          <ArrowLeft size={22} color={G.muted} />
        </Pressable>
        <Text style={s.headerTitle}>Protocol review</Text>
        <Pressable accessibilityRole="button" onPress={restart} hitSlop={12} style={[s.headerSide, { alignItems: "flex-end" }]}>
          <Text style={s.restart}>Restart</Text>
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={s.body}>
        <Text style={s.count}>Question {step + 1} of {steps.length}</Text>
        <View style={s.track}><View style={[s.trackFill, { width: `${((step + 1) / steps.length) * 100}%` }]} /></View>

        <Text style={s.eyebrow}>{current.eyebrow}</Text>
        <Text style={s.heading}>{current.heading}</Text>
        {!!current.context && <Text style={s.context}>{current.context}</Text>}

        <View style={s.options}>
          {current.options.map((o) => {
            const selected = chosen === o.value;
            return <Pressable key={o.value} testID={`kit-option-${current.id}-${o.value}`} accessibilityRole="radio"
              accessibilityState={{ selected }} onPress={() => choose(o.value)} style={[s.option, selected && s.optionSelected]}>
              <View style={s.optionText}>
                <Text style={s.optionLabel}>{o.label}</Text>
                {current.proposed === o.value && <View style={s.tag}><Text style={s.tagText}>Proposed answer</Text></View>}
              </View>
              <View style={[s.radio, selected && s.radioOn]}>{selected && <View style={s.radioDot} />}</View>
            </Pressable>;
          })}
        </View>

        {!!current.why && <Pressable accessibilityRole="button" accessibilityState={{ expanded: why }} onPress={() => setWhy((v) => !v)} style={s.why}>
          <Info size={16} color={G.accent} />
          <Text style={s.whyLabel}>Why am I being asked?</Text>
          {why ? <Minus size={16} color={G.accent} /> : <Plus size={16} color={G.accent} />}
        </Pressable>}
        {why && <Text style={s.whyText}>{current.why}</Text>}
      </ScrollView>

      <View style={s.footer}>
        {!!error && <Text style={s.error}>{error}</Text>}
        {chosen
          ? <Pressable testID={last ? "kit-questions-confirm" : "kit-questions-next"} accessibilityRole="button" disabled={busy}
              onPress={() => (last ? confirm() : go(step + 1))} style={s.next}>
              {busy ? <ActivityIndicator color="#FFFFFF" /> : <>
                <Text style={s.nextText}>{last ? "Confirm answers" : "Continue"}</Text>
                <ArrowRight size={18} color="#FFFFFF" />
              </>}
            </Pressable>
          : <View style={s.hint}>
              <ArrowRight size={16} color={G.muted} />
              <Text style={s.hintText}>Choose an answer to continue</Text>
            </View>}
      </View>
    </SafeAreaView>
  </Modal>;
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: G.bg },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: 20, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: G.line },
  headerSide: { width: 64 },
  headerTitle: { flex: 1, textAlign: "center", color: G.ink, fontFamily: fonts.semibold, fontSize: 16 },
  restart: { color: G.muted, fontFamily: fonts.medium, fontSize: 14 },
  body: { paddingHorizontal: 24, paddingTop: 24, paddingBottom: 32 },
  count: { color: G.accent, fontFamily: fonts.medium, fontSize: 13 },
  track: { height: 3, borderRadius: 2, backgroundColor: G.track, marginTop: 12, marginBottom: 36, overflow: "hidden" },
  trackFill: { height: "100%", backgroundColor: G.accent },
  eyebrow: { color: G.accent, fontFamily: fonts.semibold, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase" },
  heading: { color: G.ink, fontFamily: fonts.semibold, fontSize: 28, lineHeight: 35, letterSpacing: -0.4, marginTop: 12 },
  context: { color: G.muted, fontFamily: fonts.regular, fontSize: 15, lineHeight: 22, marginTop: 12 },
  options: { gap: 12, marginTop: 28 },
  option: { flexDirection: "row", alignItems: "center", gap: 14, padding: 18, borderRadius: 14, borderWidth: 1, borderColor: G.line, backgroundColor: G.bg },
  optionSelected: { borderColor: G.accent, backgroundColor: G.selected },
  optionText: { flex: 1, gap: 8, alignItems: "flex-start" },
  optionLabel: { color: G.ink, fontFamily: fonts.medium, fontSize: 16, lineHeight: 22 },
  tag: { backgroundColor: G.tag, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4 },
  tagText: { color: G.tagInk, fontFamily: fonts.medium, fontSize: 11 },
  radio: { width: 24, height: 24, borderRadius: 12, borderWidth: 1.5, borderColor: G.muted + "99", alignItems: "center", justifyContent: "center" },
  radioOn: { borderColor: G.accent },
  radioDot: { width: 12, height: 12, borderRadius: 6, backgroundColor: G.accent },
  why: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 28, paddingVertical: 6 },
  whyLabel: { flex: 1, color: G.muted, fontFamily: fonts.regular, fontSize: 14 },
  whyText: { color: G.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, marginTop: 6, paddingLeft: 26 },
  footer: { borderTopWidth: 1, borderTopColor: G.line, paddingHorizontal: 24, paddingVertical: 16, gap: 8 },
  hint: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10, minHeight: 48 },
  hintText: { color: G.muted, fontFamily: fonts.regular, fontSize: 14 },
  next: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, minHeight: 50, borderRadius: 14, backgroundColor: G.accent },
  nextText: { color: "#FFFFFF", fontFamily: fonts.semibold, fontSize: 15 },
  error: { color: "#B42318", fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
