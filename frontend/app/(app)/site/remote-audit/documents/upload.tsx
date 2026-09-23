// Site-side document upload — spec §10.
//
// Unlike evidence capture, picking an existing file IS the point here: a
// calibration certificate is issued by a third party and legitimately lives
// in the site's files. What this screen must never do is imply the document
// has been checked. It hasn't — it goes into quarantine, the worker examines
// it, and only then does anyone see a verdict. The copy on this screen says
// so plainly rather than showing a green tick on a successful upload.
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CheckCircle2, FileText, Upload } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import {
  createDocument,
  fetchDocument,
  sendDocumentBytes,
  submitDocument,
} from "@/src/features/remote-audit/documentsApi";
import type { DocumentItem } from "@/src/features/remote-audit/types";

const ACCEPTED_TYPES = ["application/pdf", "image/jpeg", "image/png"];
const MAX_BYTES = 50 * 1024 * 1024;

const DOCUMENT_TYPES = [
  { value: "calibration_certificate", label: "Calibration certificate" },
  { value: "preventive_maintenance", label: "Preventive maintenance record" },
  { value: "inspection_certificate", label: "Inspection certificate" },
  { value: "training_record", label: "Training record" },
  { value: "sop", label: "SOP / procedure" },
  { value: "other", label: "Other" },
];

type Picked = { uri: string; name: string; type: string; size: number };

type Step =
  | { kind: "idle" }
  | { kind: "working"; label: string }
  | { kind: "submitted"; document: DocumentItem }
  | { kind: "error"; message: string };

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong. Please try again.";
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentUploadScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    auditId?: string;
    linkedEntityType?: string;
    linkedEntityId?: string;
  }>();
  const auditId = String(params.auditId ?? "");

  const [documentType, setDocumentType] = useState(DOCUMENT_TYPES[0].value);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [statusPoll, setStatusPoll] = useState<DocumentItem | null>(null);

  const pick = useCallback(async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: ACCEPTED_TYPES,
      copyToCacheDirectory: true,
      multiple: false,
    });
    if (result.canceled || !result.assets?.length) return;
    const asset = result.assets[0];
    const size = asset.size ?? 0;
    if (size <= 0) {
      setStep({ kind: "error", message: "That file appears to be empty." });
      return;
    }
    if (size > MAX_BYTES) {
      setStep({ kind: "error", message: `That file is ${humanSize(size)}. The limit is ${humanSize(MAX_BYTES)}.` });
      return;
    }
    const mime = asset.mimeType && ACCEPTED_TYPES.includes(asset.mimeType)
      ? asset.mimeType
      : "application/pdf";
    setPicked({ uri: asset.uri, name: asset.name ?? "document.pdf", type: mime, size });
    setStep({ kind: "idle" });
  }, []);

  const upload = useCallback(async () => {
    if (!picked || !auditId) return;
    try {
      setStep({ kind: "working", label: "Reserving upload…" });
      const target = await createDocument(auditId, {
        documentType,
        fileName: picked.name,
        mimeClaimed: picked.type,
        sizeClaimed: picked.size,
        linkedEntityType: params.linkedEntityType ? String(params.linkedEntityType) : undefined,
        linkedEntityId: params.linkedEntityId ? String(params.linkedEntityId) : undefined,
      });

      setStep({ kind: "working", label: "Uploading…" });
      await sendDocumentBytes(target, { uri: picked.uri, name: picked.name, type: picked.type });

      setStep({ kind: "working", label: "Submitting for checks…" });
      await submitDocument(target.document.id);

      const fresh = await fetchDocument(target.document.id);
      setStep({ kind: "submitted", document: fresh });
      setStatusPoll(fresh);
    } catch (err) {
      setStep({ kind: "error", message: errorMessage(err) });
    }
  }, [picked, auditId, documentType, params.linkedEntityType, params.linkedEntityId]);

  // Processing is asynchronous by design, so the screen polls rather than
  // pretending to know the outcome the moment the upload finishes.
  useEffect(() => {
    if (step.kind !== "submitted") return;
    const id = step.document.id;
    const terminal = ["TECHNICALLY_VALID", "REVIEW_REQUIRED", "ACCEPTED", "REJECTED"];
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const fresh = await fetchDocument(id);
        if (cancelled) return;
        setStatusPoll(fresh);
        if (terminal.includes(fresh.status)) clearInterval(timer);
      } catch {
        // A transient read failure is not worth interrupting the user over;
        // the next tick tries again.
      }
    }, 4000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [step]);

  if (step.kind === "submitted") {
    const current = statusPoll ?? step.document;
    const blocked = current.status === "PROCESSING_BLOCKED";
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Document submitted" />
        <ScrollView contentContainerStyle={s.body}>
          <Card style={{ gap: spacing.sm }}>
            <View style={s.row}>
              <CheckCircle2 size={20} color={colors.success} />
              <Body weight="600">Received and quarantined</Body>
            </View>
            <Small>
              Your file was uploaded and is being scanned and checked. It is not visible to the
              reviewer yet, and nothing about it has been accepted — that happens after the checks
              finish and a reviewer looks at the result.
            </Small>
            <View style={s.kvRow}><Small>File</Small><Small color={colors.foreground}>{current.displayName}</Small></View>
            <View style={s.kvRow}><Small>Status</Small><Small color={colors.foreground}>{current.status.replace(/_/g, " ")}</Small></View>
          </Card>

          {blocked ? (
            <Card style={{ gap: spacing.xs }}>
              <View style={s.row}>
                <AlertTriangle size={18} color={colors.warning} />
                <Body weight="600">Checks can&apos;t finish right now</Body>
              </View>
              <Small>
                The virus scanner is unreachable, so this file stays isolated rather than being
                passed on unscanned. It will be checked automatically once the scanner is back —
                you don&apos;t need to upload it again.
              </Small>
            </Card>
          ) : null}

          <Button onPress={() => { setPicked(null); setStatusPoll(null); setStep({ kind: "idle" }); }}>
            Upload another
          </Button>
          <Button variant="secondary" onPress={() => router.back()}>Done</Button>
        </ScrollView>
      </ScreenContainer>
    );
  }

  const busy = step.kind === "working";

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Upload a document" />
      <ScrollView contentContainerStyle={s.body}>
        <Card style={{ gap: spacing.sm }}>
          <Body weight="700">What is this document?</Body>
          <View style={s.chips}>
            {DOCUMENT_TYPES.map((option) => {
              const active = option.value === documentType;
              return (
                <Pressable
                  key={option.value}
                  onPress={() => setDocumentType(option.value)}
                  style={[s.chip, active && { backgroundColor: colors.primary, borderColor: colors.primary }]}
                >
                  <Small color={active ? colors.primaryFg : colors.foreground}>{option.label}</Small>
                </Pressable>
              );
            })}
          </View>
        </Card>

        <Card style={{ gap: spacing.sm }}>
          <Body weight="700">File</Body>
          {picked ? (
            <>
              <View style={s.row}>
                <FileText size={18} color={colors.primary} />
                <Small color={colors.foreground} style={{ flex: 1 }}>{picked.name}</Small>
              </View>
              <Small>{picked.type} · {humanSize(picked.size)}</Small>
            </>
          ) : (
            <Small>PDF, JPEG or PNG, up to {humanSize(MAX_BYTES)}.</Small>
          )}
          <Button variant="secondary" onPress={() => { void pick(); }} disabled={busy}>
            {picked ? "Choose a different file" : "Choose a file"}
          </Button>
        </Card>

        {step.kind === "error" ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <AlertTriangle size={18} color={colors.destructive} />
              <Body weight="600">Couldn&apos;t upload</Body>
            </View>
            <Small>{step.message}</Small>
          </Card>
        ) : null}

        <Card style={{ gap: spacing.xs }}>
          <Small color={colors.mutedFg}>
            Uploaded files are scanned before anyone can open them. A password-protected PDF can&apos;t
            be checked, so it will be held for a reviewer rather than accepted.
          </Small>
        </Card>

        <Button disabled={!picked || busy} onPress={() => { void upload(); }}>
          {busy ? (
            <View style={s.row}>
              <ActivityIndicator color={colors.primaryFg} />
              <Body color={colors.primaryFg} weight="700">{step.label}</Body>
            </View>
          ) : (
            <View style={s.row}>
              <Upload size={18} color={colors.primaryFg} />
              <Body color={colors.primaryFg} weight="700">Upload</Body>
            </View>
          )}
        </Button>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  body: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  kvRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: {
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card,
  },
});
