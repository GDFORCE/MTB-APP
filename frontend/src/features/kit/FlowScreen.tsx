import React from "react";
import {
  ActivityIndicator, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ArrowLeft, ArrowRight, X } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import { FOCUS as G } from "./focus";

/**
 * One step of a guided flow: header, "Step n of m" with a progress bar, a short eyebrow and heading,
 * the step's content and a footer for its one main action.
 */
export function FlowScreen({ title, step, steps, eyebrow, heading, sub, onBack, right, footer, children, scrollRef }: {
  title: string; step?: number; steps?: number; eyebrow?: string; heading?: string; sub?: string;
  onBack?: () => void; right?: React.ReactNode; footer?: React.ReactNode; children?: React.ReactNode;
  scrollRef?: React.RefObject<ScrollView | null>;
}) {
  return <SafeAreaView style={s.screen} edges={["top", "bottom"]}>
    <View style={s.header}>
      <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={onBack} disabled={!onBack} hitSlop={12}
        style={[s.side, right ? { width: 96 } : null, !onBack && { opacity: 0 }]}>
        <ArrowLeft size={22} color={G.muted} />
      </Pressable>
      <Text style={s.title} numberOfLines={1}>{title}</Text>
      <View style={[s.side, right ? s.sideRight : null]}>{right}</View>
    </View>
    <KeyboardAvoidingView style={s.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView ref={scrollRef} contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        {!!step && !!steps && <>
          <Text style={s.count}>Step {step} of {steps}</Text>
          <View style={s.track}><View style={[s.fill, { width: `${(step / steps) * 100}%` }]} /></View>
        </>}
        {!!eyebrow && <Text style={[s.eyebrow, !step && { marginTop: 0 }]}>{eyebrow}</Text>}
        {!!heading && <Text style={[s.heading, !step && !eyebrow && { marginTop: 0 }]}>{heading}</Text>}
        {!!sub && <Text style={s.sub}>{sub}</Text>}
        {children}
      </ScrollView>
      {!!footer && <View style={s.footer}>{footer}</View>}
    </KeyboardAvoidingView>
  </SafeAreaView>;
}

export function FlowButton({ label, onPress, disabled, loading, arrow, variant = "primary", testID }: {
  label: string; onPress: () => void; disabled?: boolean; loading?: boolean; arrow?: boolean;
  variant?: "primary" | "secondary"; testID?: string;
}) {
  const primary = variant === "primary";
  return <Pressable testID={testID} accessibilityRole="button" accessibilityState={{ disabled: !!disabled || !!loading, busy: !!loading }}
    disabled={disabled || loading} onPress={onPress}
    style={({ pressed }) => [s.button, primary ? s.primary : s.secondary, (disabled || loading) && { opacity: 0.45 }, pressed && { opacity: 0.85 }]}>
    {loading ? <ActivityIndicator color={primary ? "#FFFFFF" : G.accent} /> : <>
      <Text style={[s.buttonText, !primary && { color: G.ink }]}>{label}</Text>
      {arrow && <ArrowRight size={18} color={primary ? "#FFFFFF" : G.ink} />}
    </>}
  </Pressable>;
}

/** A bottom sheet in the Focus style. */
export function FlowSheet({ visible, title, onClose, children }: {
  visible: boolean; title: string; onClose: () => void; children: React.ReactNode;
}) {
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
    <KeyboardAvoidingView style={s.backdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Close" />
      <View style={s.sheet}>
        <View style={s.grabber} />
        <View style={s.sheetHead}>
          <Text style={s.sheetTitle}>{title}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} hitSlop={12}><X size={20} color={G.muted} /></Pressable>
        </View>
        <ScrollView contentContainerStyle={s.sheetBody} keyboardShouldPersistTaps="handled">{children}</ScrollView>
      </View>
    </KeyboardAvoidingView>
  </Modal>;
}

export const flowStyles = StyleSheet.create({
  label: { color: G.ink, fontFamily: fonts.semibold, fontSize: 13 },
  input: { minHeight: 50, borderWidth: 1, borderColor: G.line, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, color: G.ink, fontFamily: fonts.regular, fontSize: 15, backgroundColor: "#FFFFFF" },
  hint: { color: G.muted, fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  error: { color: G.error, fontFamily: fonts.medium, fontSize: 13, lineHeight: 18 },
  sectionTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 18 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { minHeight: 40, paddingHorizontal: 14, justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: G.line, backgroundColor: "#FFFFFF" },
  chipOn: { borderColor: G.accent, backgroundColor: G.accent },
  chipText: { color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
  chipTextOn: { color: "#FFFFFF", fontFamily: fonts.semibold },
});

const s = StyleSheet.create({
  flex: { flex: 1 },
  screen: { flex: 1, backgroundColor: G.bg },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: 20, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: G.line },
  side: { width: 44 },
  sideRight: { width: 96, alignItems: "flex-end" },
  title: { flex: 1, textAlign: "center", color: G.ink, fontFamily: fonts.semibold, fontSize: 16 },
  body: { paddingHorizontal: 24, paddingTop: 24, paddingBottom: 32 },
  count: { color: G.accent, fontFamily: fonts.medium, fontSize: 13 },
  track: { height: 3, borderRadius: 2, backgroundColor: G.track, marginTop: 12, overflow: "hidden" },
  fill: { height: "100%", backgroundColor: G.accent },
  eyebrow: { color: G.accent, fontFamily: fonts.semibold, fontSize: 12, letterSpacing: 1.6, textTransform: "uppercase", marginTop: 32 },
  heading: { color: G.ink, fontFamily: fonts.semibold, fontSize: 28, lineHeight: 35, letterSpacing: -0.4, marginTop: 12 },
  sub: { color: G.muted, fontFamily: fonts.regular, fontSize: 15, lineHeight: 22, marginTop: 12 },
  footer: { borderTopWidth: 1, borderTopColor: G.line, paddingHorizontal: 24, paddingTop: 16, paddingBottom: 16, gap: 10 },
  button: { minHeight: 50, borderRadius: 14, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingHorizontal: 16 },
  primary: { backgroundColor: G.accent },
  secondary: { borderWidth: 1, borderColor: G.line, backgroundColor: "#FFFFFF" },
  buttonText: { color: "#FFFFFF", fontFamily: fonts.semibold, fontSize: 15 },
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(22,53,42,0.45)" },
  sheet: { backgroundColor: "#FFFFFF", borderTopLeftRadius: 22, borderTopRightRadius: 22, paddingHorizontal: 24, paddingTop: 10, paddingBottom: 24, maxHeight: "90%" },
  grabber: { width: 40, height: 4, borderRadius: 2, backgroundColor: G.line, alignSelf: "center" },
  sheetHead: { flexDirection: "row", alignItems: "center", marginTop: 14, marginBottom: 8 },
  sheetTitle: { flex: 1, color: G.ink, fontFamily: fonts.semibold, fontSize: 20 },
  sheetBody: { gap: 16, paddingBottom: 8 },
});
