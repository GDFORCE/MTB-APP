import React, { useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import {
  ArrowRight,
  CheckCircle2,
  FileText,
  LockKeyhole,
  UploadCloud,
  X,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Button, Card, Small } from "@/src/components/ui";
import {
  colors,
  fonts,
  radii,
  shadows,
  spacing,
} from "@/src/theme/tokens";

type Source = "registry" | "organization" | "upload";
type TrialStatus = "active" | "completed" | "terminated";
type Details = {
  ctri_number: string;
  title: string;
  phase: string;
  indications: string[];
  drug: string;
  duration: string;
  target_enrollment: string;
  total_visits: string;
  status: TrialStatus;
};

const EMPTY: Details = {
  ctri_number: "",
  title: "",
  phase: "",
  indications: [],
  drug: "",
  duration: "",
  target_enrollment: "",
  total_visits: "",
  status: "active",
};
const PHASES = ["Phase I", "Phase II", "Phase III", "Phase IV"];
const STATUSES: { value: TrialStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "completed", label: "Completed" },
  { value: "terminated", label: "Terminated" },
];

function normalized(raw: any): Details {
  return {
    ctri_number: raw?.ctri_number || "",
    title: raw?.title || "",
    phase: raw?.phase || "",
    indications: Array.isArray(raw?.indications) ? raw.indications.filter(Boolean) : [],
    drug: raw?.drug || "",
    duration: raw?.duration || "",
    target_enrollment: raw?.target_enrollment == null ? "" : String(raw.target_enrollment),
    total_visits: raw?.total_visits == null ? "" : String(raw.total_visits),
    status: ["active", "completed", "terminated"].includes(raw?.status)
      ? raw.status
      : "active",
  };
}

export default function AddTrial() {
  const router = useRouter();
  const progressTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [protocolId, setProtocolId] = useState("");
  const [details, setDetails] = useState<Details>(EMPTY);
  const [source, setSource] = useState<Source | null>(null);
  const [resolved, setResolved] = useState(false);
  const [lookingUp, setLookingUp] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const update = (patch: Partial<Details>) =>
    setDetails((current) => ({ ...current, ...patch }));

  const resetResolution = (value: string) => {
    setProtocolId(value);
    setResolved(false);
    setSource(null);
    setDetails(EMPTY);
    setErr("");
  };

  const lookup = async () => {
    const id = protocolId.trim();
    if (!id) return;
    setLookingUp(true);
    setErr("");
    try {
      const response = await api.get(`/protocols/lookup/${encodeURIComponent(id)}`);
      if (response.data?.found) {
        setProtocolId(response.data.protocol_id || id);
        setDetails(normalized(response.data.details));
        setSource(response.data.source === "organization" ? "organization" : "registry");
        setResolved(true);
      } else {
        setShowUpload(true);
      }
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "Could not look up this protocol. Try again.");
    } finally {
      setLookingUp(false);
    }
  };

  const extract = async () => {
    const picked = await DocumentPicker.getDocumentAsync({
      type: "application/pdf",
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    const asset = picked.assets[0];
    setExtracting(true);
    setProgress(10);
    setErr("");
    progressTimer.current = setInterval(
      () => setProgress((value) => Math.min(88, value + 7)),
      280,
    );
    try {
      const form = new FormData();
      if (Platform.OS === "web") {
        const file = asset.file || await (await fetch(asset.uri)).blob();
        form.append("file", file, asset.name || "protocol.pdf");
      } else {
        form.append("file", {
          uri: asset.uri,
          name: asset.name || "protocol.pdf",
          type: asset.mimeType || "application/pdf",
        } as any);
      }
      const response = await api.post("/protocols/extract-details", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setProgress(100);
      setDetails(normalized(response.data?.details));
      setSource("upload");
      setResolved(true);
      setTimeout(() => setShowUpload(false), 250);
    } catch (error: any) {
      const status = error?.response?.status;
      setErr(
        error?.response?.data?.detail ||
        (status === 503
          ? "Protocol extraction is not configured on the server yet."
          : "We could not extract this PDF. Check the file and try again."),
      );
    } finally {
      if (progressTimer.current) clearInterval(progressTimer.current);
      progressTimer.current = null;
      setExtracting(false);
    }
  };

  const submit = async () => {
    const indications = details.indications.map((item) => item.trim()).filter(Boolean);
    if (
      !resolved || !protocolId.trim() || !details.ctri_number.trim() ||
      !details.title.trim() || !details.phase || !indications.length ||
      !details.duration.trim() || !details.target_enrollment || !details.total_visits
    ) {
      setErr("Complete all required trial details before saving.");
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const response = await api.post("/trials", {
        title: details.title.trim(),
        protocol_id: protocolId.trim(),
        phase: details.phase,
        condition: indications.join(", "),
        indications,
        description: "",
        sponsor_name: "",
        drug: details.drug.trim(),
        duration: details.duration.trim(),
        target_enrollment: Number(details.target_enrollment),
        total_visits: Number(details.total_visits),
        ctri_number: details.ctri_number.trim(),
        status: details.status,
        recruitment_status: details.status === "active" ? "recruiting" : "closed",
      });
      router.replace({
        pathname: "/(app)/sponsor/visit-schedule",
        params: { id: response.data.id },
      });
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "Could not save this trial.");
    } finally {
      setLoading(false);
    }
  };

  const locked = !resolved;
  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="New study" title="Add New Trial" />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={s.flex}
      >
        <ScrollView
          contentContainerStyle={s.content}
          keyboardShouldPersistTaps="handled"
        >
          <View style={s.stepRow}>
            <View style={s.stepNumber}><Text style={s.stepNumberText}>1</Text></View>
            <View style={s.flex}>
              <Text style={s.sectionTitle}>Find your protocol</Text>
              <Small>We&apos;ll securely auto-fill the trial details when possible.</Small>
            </View>
          </View>

          <Label text="Protocol ID *" />
          <View style={s.lookupRow}>
            <TextInput
              testID="trial-proto"
              value={protocolId}
              onChangeText={resetResolution}
              onSubmitEditing={lookup}
              placeholder="Enter protocol ID"
              placeholderTextColor={colors.mutedFg}
              autoCapitalize="characters"
              style={[s.input, s.lookupInput]}
            />
            <Pressable
              testID="protocol-lookup"
              accessibilityLabel="Look up protocol"
              disabled={!protocolId.trim() || lookingUp}
              onPress={lookup}
              style={({ pressed }) => [
                s.lookupButton,
                (!protocolId.trim() || lookingUp) && s.disabled,
                pressed && { transform: [{ scale: 0.95 }] },
              ]}
            >
              {lookingUp
                ? <ActivityIndicator color={colors.primaryFg} />
                : <ArrowRight size={21} color={colors.primaryFg} />}
            </Pressable>
          </View>
          <Small style={s.helper}>
            If the ID isn&apos;t on record, upload the protocol PDF for extraction.
          </Small>

          {resolved && (
            <View style={s.successBanner}>
              <CheckCircle2 size={20} color={colors.success} />
              <Text style={s.successText}>
                {source === "upload"
                  ? "Protocol details extracted. Review them before saving."
                  : "Protocol found. Details have been auto-filled for review."}
              </Text>
            </View>
          )}

          <View style={s.stepRow}>
            <View style={[s.stepNumber, locked && s.lockedNumber]}>
              {locked
                ? <LockKeyhole size={14} color={colors.mutedFg} />
                : <Text style={s.stepNumberText}>2</Text>}
            </View>
            <View style={s.flex}>
              <Text style={s.sectionTitle}>Trial details</Text>
              <Small>{locked ? "Complete lookup or extraction to unlock." : "Review and correct any extracted value."}</Small>
            </View>
          </View>

          <View
            accessibilityState={{ disabled: locked }}
            style={[s.details, locked && s.detailsLocked]}
          >
            <Field testID="trial-ctri" label="CTRI Number *" value={details.ctri_number} onChange={(value) => update({ ctri_number: value })} disabled={locked} placeholder="Enter CTRI number" />
            <Field testID="trial-title" label="Study Title *" value={details.title} onChange={(value) => update({ title: value })} disabled={locked} placeholder="Enter official study title" multiline />

            <Label text="Phase *" />
            <View style={s.choiceWrap}>
              {PHASES.map((phase) => (
                <Choice key={phase} label={phase} active={details.phase === phase} disabled={locked} onPress={() => update({ phase })} />
              ))}
            </View>

            <Field
              testID="trial-condition"
              label="Disease / Indication *"
              value={details.indications.join(", ")}
              onChange={(value) => update({ indications: value.split(",").map((item) => item.trimStart()) })}
              disabled={locked}
              placeholder="e.g. Diabetes, Hypertension"
              hint="Separate multiple indications with commas."
            />
            <Field testID="trial-drug" label="Study Drug Name" value={details.drug} onChange={(value) => update({ drug: value })} disabled={locked} placeholder="Enter investigational drug" />
            <Field testID="trial-duration" label="Duration of the Trial *" value={details.duration} onChange={(value) => update({ duration: value })} disabled={locked} placeholder="e.g. 18 months" />
            <View style={s.twoCol}>
              <View style={s.flex}>
                <Field testID="trial-sample-size" label="Sample Size *" value={details.target_enrollment} onChange={(value) => update({ target_enrollment: value.replace(/\D/g, "") })} disabled={locked} placeholder="100" keyboardType="number-pad" />
              </View>
              <View style={s.flex}>
                <Field testID="trial-total-visits" label="Total Visits *" value={details.total_visits} onChange={(value) => update({ total_visits: value.replace(/\D/g, "") })} disabled={locked} placeholder="18" keyboardType="number-pad" />
              </View>
            </View>

            <Label text="Status of Trial" />
            <View style={s.choiceWrap}>
              {STATUSES.map((status) => (
                <Choice key={status.value} label={status.label} active={details.status === status.value} disabled={locked} onPress={() => update({ status: status.value })} />
              ))}
            </View>
          </View>

          {!!err && <Text style={s.error}>{err}</Text>}
          <Button
            testID="add-trial-submit"
            onPress={submit}
            loading={loading}
            disabled={locked}
            style={s.submit}
          >
            Save & build visit schedule
          </Button>
        </ScrollView>
      </KeyboardAvoidingView>

      <Modal
        visible={showUpload}
        transparent
        animationType="fade"
        onRequestClose={() => !extracting && setShowUpload(false)}
      >
        <View style={s.modalBackdrop}>
          <Card style={s.modalCard}>
            {!extracting ? (
              <>
                <View style={s.modalHeader}>
                  <View style={s.uploadIcon}><FileText size={23} color={colors.primary} /></View>
                  <Pressable testID="close-protocol-upload" hitSlop={12} onPress={() => setShowUpload(false)}>
                    <X size={21} color={colors.mutedFg} />
                  </Pressable>
                </View>
                <Text style={s.modalTitle}>Protocol not on record</Text>
                <Small style={s.modalCopy}>
                  We couldn&apos;t find {protocolId.trim()}. Upload its protocol PDF and we&apos;ll extract the trial details before anything is created.
                </Small>
                <Pressable testID="upload-protocol" onPress={extract} style={s.dropZone}>
                  <View style={s.uploadIcon}><UploadCloud size={24} color={colors.primary} /></View>
                  <Text style={s.dropTitle}>Choose protocol PDF</Text>
                  <Small>PDF up to 25 MB</Small>
                </Pressable>
              </>
            ) : (
              <View style={s.extracting}>
                <View style={s.uploadIcon}><FileText size={23} color={colors.primary} /></View>
                <Text style={s.modalTitle}>Reading your protocol…</Text>
                <Small style={s.modalCopy}>Extracting study details and visit count. Keep this screen open.</Small>
                <View style={s.progressTrack}>
                  <View style={[s.progressFill, { width: `${progress}%` }]} />
                </View>
                <Text style={s.progressText}>{progress}%</Text>
              </View>
            )}
          </Card>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

function Label({ text }: { text: string }) {
  return <Text style={s.label}>{text}</Text>;
}

type FieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  testID: string;
  placeholder: string;
  hint?: string;
  multiline?: boolean;
  keyboardType?: "default" | "number-pad";
};

function Field({
  label,
  value,
  onChange,
  disabled,
  testID,
  placeholder,
  hint,
  multiline,
  keyboardType,
}: FieldProps) {
  return (
    <View style={s.field}>
      <Label text={label} />
      <TextInput
        testID={testID}
        value={value}
        onChangeText={onChange}
        editable={!disabled}
        placeholder={placeholder}
        placeholderTextColor={colors.mutedFg}
        multiline={multiline}
        keyboardType={keyboardType}
        style={[s.input, multiline && s.multiline]}
      />
      {!!hint && <Small style={s.fieldHint}>{hint}</Small>}
    </View>
  );
}

function Choice({
  label,
  active,
  disabled,
  onPress,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      disabled={disabled}
      onPress={onPress}
      style={[s.choice, active && s.choiceActive]}
    >
      <Text style={[s.choiceText, active && s.choiceTextActive]}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  stepRow: { flexDirection: "row", alignItems: "center", gap: 11, marginTop: 4, marginBottom: 16 },
  stepNumber: { width: 28, height: 28, borderRadius: 14, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  lockedNumber: { backgroundColor: colors.surface },
  stepNumberText: { color: colors.primary, fontFamily: fonts.bold, fontSize: 12 },
  sectionTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 18 },
  label: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 12, marginBottom: 7 },
  lookupRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  lookupInput: { flex: 1, fontFamily: fonts.mono },
  lookupButton: { width: 48, height: 48, borderRadius: 24, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center", ...shadows.sm },
  disabled: { opacity: 0.4 },
  helper: { marginTop: 7, marginBottom: 16, lineHeight: 17 },
  successBanner: { flexDirection: "row", alignItems: "center", gap: 10, borderRadius: radii.lg, backgroundColor: "rgba(92,154,110,0.12)", padding: 13, marginBottom: 18 },
  successText: { flex: 1, color: colors.foreground, fontFamily: fonts.medium, fontSize: 13, lineHeight: 18 },
  details: { marginBottom: 4 },
  detailsLocked: { opacity: 0.42 },
  field: { marginBottom: 15 },
  input: { minHeight: 48, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.card, paddingHorizontal: 14, paddingVertical: 12, color: colors.foreground, fontFamily: fonts.regular, fontSize: 14 },
  multiline: { minHeight: 82, textAlignVertical: "top" },
  fieldHint: { marginTop: 5 },
  choiceWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 16 },
  choice: { minHeight: 38, borderWidth: 1, borderColor: colors.border, borderRadius: radii.pill, backgroundColor: colors.card, paddingHorizontal: 14, alignItems: "center", justifyContent: "center" },
  choiceActive: { borderColor: colors.primary, backgroundColor: colors.secondary },
  choiceText: { color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 12 },
  choiceTextActive: { color: colors.primary, fontFamily: fonts.semibold },
  twoCol: { flexDirection: "row", gap: 11 },
  error: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12, lineHeight: 17, marginBottom: 12 },
  submit: { marginTop: 4 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(46,27,51,0.48)", justifyContent: "center", padding: 24 },
  modalCard: { width: "100%", maxWidth: 390, alignSelf: "center", padding: 20 },
  modalHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  modalTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 20, marginBottom: 7 },
  modalCopy: { lineHeight: 18, marginBottom: 17 },
  uploadIcon: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  dropZone: { borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.primary + "77", borderRadius: radii.lg, backgroundColor: colors.background, padding: 24, alignItems: "center", gap: 7 },
  dropTitle: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 14, marginTop: 3 },
  extracting: { alignItems: "center", paddingVertical: 12 },
  progressTrack: { width: "100%", height: 8, borderRadius: 4, backgroundColor: colors.secondary, overflow: "hidden", marginTop: 2 },
  progressFill: { height: "100%", borderRadius: 4, backgroundColor: colors.primary },
  progressText: { color: colors.primary, fontFamily: fonts.mono, fontSize: 12, marginTop: 8 },
});
