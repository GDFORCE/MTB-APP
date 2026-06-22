import React, { useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { Mail, Link as LinkIcon, FileText, Check } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";

export default function ShareSchedule() {
  const router = useRouter();
  const [emails, setEmails] = useState("");
  const [via, setVia] = useState<"email" | "link" | "pdf">("email");
  const [sent, setSent] = useState(false);
  const onSend = () => { setSent(true); setTimeout(() => router.back(), 1500); };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Distribute" title="Share Schedule" />
      {sent ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg }}>
          <View style={s.ok}><Check size={36} color={colors.success} /></View>
          <Body weight="700" style={{ marginTop: spacing.md, fontSize: 18 }}>Shared successfully</Body>
          <Small style={{ marginTop: 4 }}>Recipients will receive the schedule shortly.</Small>
        </View>
      ) : (
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }} keyboardShouldPersistTaps="handled">
            <Eyebrow>Share via</Eyebrow>
            <View style={{ gap: 8 }}>
              {([
                { id: "email", icon: Mail, label: "Email", desc: "Send to one or more email addresses" },
                { id: "link", icon: LinkIcon, label: "Secure link", desc: "Generate a shareable link (expires in 7 days)" },
                { id: "pdf", icon: FileText, label: "PDF export", desc: "Download a printable PDF" },
              ] as const).map(opt => (
                <Pressable key={opt.id} testID={`share-${opt.id}`} onPress={() => setVia(opt.id)}>
                  <Card style={{ borderColor: via === opt.id ? colors.primary : colors.border, borderWidth: via === opt.id ? 2 : 1, marginBottom: 0 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                      <View style={s.iconBox}><opt.icon size={18} color={colors.primary} /></View>
                      <View style={{ flex: 1 }}>
                        <Body weight="700">{opt.label}</Body>
                        <Small style={{ marginTop: 2 }}>{opt.desc}</Small>
                      </View>
                      {via === opt.id && <View style={s.check}><Check size={12} color={colors.primaryFg} /></View>}
                    </View>
                  </Card>
                </Pressable>
              ))}
            </View>
            {via === "email" && (
              <View>
                <Small color={colors.foreground} style={{ fontWeight: "600" as any, marginBottom: 6 }}>Recipient emails (comma separated)</Small>
                <TextInput testID="share-emails" value={emails} onChangeText={setEmails} placeholder="pi@aiims.org, crc@aiims.org" placeholderTextColor={colors.mutedFg} multiline style={s.input} />
              </View>
            )}
          </ScrollView>
          <View style={{ padding: spacing.md }}><Button testID="share-send" onPress={onSend}>{via === "pdf" ? "Download PDF" : via === "link" ? "Generate link" : "Send"}</Button></View>
        </KeyboardAvoidingView>
      )}
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  ok: { width: 80, height: 80, borderRadius: 40, backgroundColor: colors.success + "1A", alignItems: "center", justifyContent: "center" },
  iconBox: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  check: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, minHeight: 80, textAlignVertical: "top" },
});
