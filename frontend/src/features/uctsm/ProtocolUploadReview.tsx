import React, { useState } from "react";
import { Platform, View } from "react-native";
import { useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { api } from "@/src/api/client";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors } from "@/src/theme/tokens";
import { buildCanonicalSchedule } from "./api";
import { ExtractionPreview, type ExtractionPreviewData } from "./ExtractionPreview";
import { ProtocolIdentityNotice } from "./ProtocolIdentityNotice";
import {
  identityBlockFrom, savingDisabledReason, type IdentityBlock, type ProtocolIdentity,
} from "./protocolIdentity";

type Prepared = { extraction_id: string; preview: ExtractionPreviewData };

export function ProtocolUploadReview({ trialId, onSaved }: { trialId: string; onSaved: () => void }) {
  const router = useRouter();
  const [prepared, setPrepared] = useState<Prepared[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [block, setBlock] = useState<IdentityBlock | null>(null);
  const [identity, setIdentity] = useState<ProtocolIdentity | null>(null);
  const upload = async () => {
    const picked = await DocumentPicker.getDocumentAsync({ type: ["application/pdf", "text/plain"], copyToCacheDirectory: true });
    if (picked.canceled) return;
    const asset = picked.assets[0];
    setBusy("extract"); setError(""); setPrepared([]); setBlock(null); setIdentity(null);
    try {
      const form = new FormData();
      // The server derives the expected identity from this trial's persisted record.
      form.append("trial_id", trialId);
      if (Platform.OS === "web") form.append("file", asset.file || await (await fetch(asset.uri)).blob(), asset.name);
      else form.append("file", { uri: asset.uri, name: asset.name, type: asset.mimeType || "application/pdf" } as any);
      const response = await api.post("/protocols/extract", form, { timeout: 1800000 });
      setIdentity(response.data.identity ?? null);
      const extracted: Prepared[] = response.data.extractions || [response.data];
      if (extracted.some((item) => !item.preview)) throw new Error("No schedule preview was returned. Retry the upload.");
      setPrepared(extracted);
    } catch (cause: any) {
      const blocked = identityBlockFrom(cause);
      if (blocked) { setBlock(blocked); return; }
      setError(String(cause?.response?.data?.detail?.message || cause?.response?.data?.detail || cause?.message || "Extraction failed."));
    } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save"); setError("");
    try {
      // Preserve document option order, also when retrying a partially saved batch.
      for (const item of prepared) await api.post(`/trials/${trialId}/protocol-extractions/${item.extraction_id}/consume`);
      await buildCanonicalSchedule(trialId);
      setPrepared([]); onSaved();
    } catch (cause: any) {
      setError(String(cause?.response?.data?.detail || cause?.message || "Could not save the extracted drafts."));
    } finally { setBusy(""); }
  };
  return <Card>
    <Body weight="700">Upload or re-extract a protocol</Body>
    <Small>Inspect the extracted tables before saving new draft versions. Existing patients keep their approved schedule assignments.</Small>
    {!!error && <Small color={colors.destructive}>{error}</Small>}
    <ProtocolIdentityNotice block={block} identity={identity} onOpenReview={(identityId) => router.push({
      pathname: "/(app)/sponsor/protocol-identity-review", params: { id: identityId },
    })} />
    <View style={{ marginVertical: 12 }}>
      <Button onPress={upload} disabled={!!busy} loading={busy === "extract"} variant="secondary">Upload PDF or text</Button>
    </View>
    {busy === "extract" && <Small>Reading the protocol and checking its schedules. This can take several minutes.</Small>}
    {!!prepared.length && <>
      <ExtractionPreview previews={prepared.map((item) => item.preview)} />
      {!!savingDisabledReason(identity, block) && <Small>{savingDisabledReason(identity, block)}</Small>}
      <Button onPress={save} loading={busy === "save"}
        disabled={!!busy || !!savingDisabledReason(identity, block)
          || prepared.some((item) => !item.preview.schedule?.events.length)}>Save extracted drafts</Button>
    </>}
  </Card>;
}
