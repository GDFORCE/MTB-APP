import React, { useEffect, useState } from "react";
import { ActivityIndicator, Text } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { downloadProtocolFile, getKitModel, type SavedKitModel } from "@/src/features/kit/api";
import { FlowButton, FlowScreen, flowStyles as f } from "@/src/features/kit/FlowScreen";
import { ProtocolTimeline, useProtocolTimeline } from "@/src/features/kit/ProtocolTimeline";
import { FOCUS as G } from "@/src/features/kit/focus";

// The reviewed visit schedule saved for a trial by the protocol upload in Add Trial.
export default function KitScheduleScreen() {
  const router = useRouter();
  const { id, trialName } = useLocalSearchParams<{ id: string; trialName?: string }>();
  const [saved, setSaved] = useState<SavedKitModel | null>(null);
  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const timeline = useProtocolTimeline(saved?.model, saved?.site_answers);

  useEffect(() => {
    let cancelled = false;
    getKitModel(String(id))
      .then((value) => { if (!cancelled) setSaved(value); })
      .catch((e: any) => {
        if (!cancelled) setError(e?.response?.data?.detail || "Could not load the saved visit schedule.");
      });
    return () => { cancelled = true; };
  }, [id]);

  return <FlowScreen title="Visit schedule" onBack={() => router.back()} eyebrow={trialName || "Trial"}
    heading={timeline?.title || (saved ? "Visit schedule" : undefined)}
    sub={timeline ? `Each patient's dates count from their own ${timeline.startLabel}.${saved?.file_name ? ` From ${saved.file_name}.` : ""}` : undefined}
    footer={saved ? <FlowButton label="Open trial" variant="secondary" onPress={() => router.replace({
      pathname: "/(app)/clinical/trial-summary", params: { id: String(id) } })} /> : undefined}>
    {!saved && !error && <ActivityIndicator color={G.accent} style={{ marginTop: 32 }} />}
    {!!error && <Text style={[f.error, { marginTop: 16 }]}>{error}</Text>}
    {saved && !timeline && <Text style={[f.error, { marginTop: 16 }]}>This schedule could not be laid out.</Text>}
    {saved?.file_stored && <Text accessibilityRole="button" style={[f.hint, { color: G.accent, marginTop: 8 }]}
      onPress={downloading ? undefined : async () => {
        setDownloading(true); setFileError("");
        try { await downloadProtocolFile(String(id), saved.file_name || "protocol.pdf"); }
        catch (e: any) { setFileError(e?.response?.status === 404 ? "The protocol file was not stored." : "Couldn't download the protocol file."); }
        finally { setDownloading(false); }
      }}>{downloading ? "Downloading…" : `Download the protocol (${saved.file_name || "PDF"})`}{saved.version ? ` · model version ${saved.version}` : ""}</Text>}
    {!!fileError && <Text style={f.error}>{fileError}</Text>}
    {timeline && <ProtocolTimeline data={timeline} />}
  </FlowScreen>;
}
