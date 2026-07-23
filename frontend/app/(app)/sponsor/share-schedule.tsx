import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  FileText,
  Link as LinkIcon,
  Mail,
  MapPin,
  Search,
  Share2,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api, API_BASE } from "@/src/api/client";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import type { SponsorSite, SponsorTrial } from "@/src/features/sponsor/types";
import { uploadFile } from "@/src/lib/upload";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type Via = "email" | "link" | "pdf";
type TrialDocument = {
  id: string;
  name: string;
  version?: string;
  source: "existing" | "schedule";
  uri?: string;
};

type ShareResult = {
  link?: string;
  pdf?: string;
  recipientCount: number;
  siteNames: string[];
};

const fileName = (value: any) =>
  String(value?.original_name || value?.file_name || value?.filename || value?.name || "Trial document");

export default function ShareSchedule() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id?: string }>();
  const [step, setStep] = useState<1 | 2>(1);
  const [trials, setTrials] = useState<SponsorTrial[]>([]);
  const [sites, setSites] = useState<SponsorSite[]>([]);
  const [documents, setDocuments] = useState<TrialDocument[]>([]);
  const [trialId, setTrialId] = useState(id || "");
  const [selectedDocument, setSelectedDocument] = useState<TrialDocument | null>(null);
  const [selectedSites, setSelectedSites] = useState<Set<string>>(new Set());
  const [extraEmails, setExtraEmails] = useState("");
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [via, setVia] = useState<Via>("email");
  const [loadingData, setLoadingData] = useState(true);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState<ShareResult | null>(null);

  useEffect(() => {
    getSponsorDashboard()
      .then((dashboard) => {
        setTrials(dashboard.trials);
        setSites(dashboard.sites);
        if (!id && dashboard.trials[0]) setTrialId(dashboard.trials[0].id);
      })
      .catch((e: any) => setErr(e?.response?.data?.detail || "Couldn't load trials and sites."))
      .finally(() => setLoadingData(false));
  }, [id]);

  const selectedTrial = trials.find((trial) => trial.id === trialId);

  useEffect(() => {
    if (!trialId) {
      setDocuments([]);
      setSelectedDocument(null);
      return;
    }
    let active = true;
    setLoadingDocuments(true);
    setSelectedDocument(null);
    api
      .get("/files", { params: { scope_type: "trial", scope_id: trialId } })
      .then((response) => {
        if (!active) return;
        const rows = Array.isArray(response.data) ? response.data : response.data?.files || [];
        const existing = rows.map((row: any) => ({
          id: String(row.id || row._id),
          name: fileName(row),
          version: row.version || row.version_label,
          source: "existing" as const,
        }));
        setDocuments(existing);
      })
      .catch(() => {
        if (active) setDocuments([]);
      })
      .finally(() => {
        if (active) setLoadingDocuments(false);
      });
    return () => {
      active = false;
    };
  }, [trialId]);

  const visibleSites = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sites.filter((site) => {
      const belongs = !site.trials.length || site.trials.some((trial) => trial.id === trialId);
      const searchable = [site.name, site.hospital, site.city, site.pi].filter(Boolean).join(" ").toLowerCase();
      return belongs && (!needle || searchable.includes(needle));
    });
  }, [query, sites, trialId]);

  const selectedSiteRows = useMemo(
    () => sites.filter((site) => selectedSites.has(site.id)),
    [selectedSites, sites],
  );
  const pendingSites = useMemo(
    () => selectedSiteRows.filter((site) => !site.piId || !site.piEmail),
    [selectedSiteRows],
  );
  const selectableVisibleIds = visibleSites.map((site) => site.id);
  const allVisibleSelected =
    selectableVisibleIds.length > 0 && selectableVisibleIds.every((siteId) => selectedSites.has(siteId));

  const recipients = useMemo(() => {
    const fromSites = selectedSiteRows.map((site) => site.piEmail).filter(Boolean) as string[];
    const extra = extraEmails
      .split(",")
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean);
    return Array.from(new Set([...fromSites, ...extra]));
  }, [extraEmails, selectedSiteRows]);

  const isDirty =
    selectedSites.size > 0 || !!message.trim() || !!extraEmails.trim() || !!selectedDocument || step === 2;

  const requestClose = () => {
    if (!isDirty) {
      router.back();
      return;
    }
    Alert.alert(
      "Discard this share?",
      "Your selected sites, document and message will be cleared.",
      [
        { text: "Keep editing", style: "cancel" },
        { text: "Discard", style: "destructive", onPress: () => router.back() },
      ],
    );
  };

  const toggleSite = (siteId: string) =>
    setSelectedSites((previous) => {
      const next = new Set(previous);
      if (next.has(siteId)) next.delete(siteId);
      else next.add(siteId);
      return next;
    });

  const toggleAllVisible = () =>
    setSelectedSites((previous) => {
      const next = new Set(previous);
      if (allVisibleSelected) selectableVisibleIds.forEach((siteId) => next.delete(siteId));
      else selectableVisibleIds.forEach((siteId) => next.add(siteId));
      return next;
    });

  const selectTrial = (nextTrialId: string) => {
    if (nextTrialId === trialId) return;
    setTrialId(nextTrialId);
    setSelectedSites(new Set());
    setQuery("");
    setErr("");
  };

  const pickDocument = async () => {
    if (!trialId || uploadingDocument) return;
    setErr("");
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      setUploadingDocument(true);
      const uploaded = await uploadFile(
        {
          uri: asset.uri,
          name: asset.name || "trial-document.pdf",
          mimeType: asset.mimeType,
          file: (asset as any).file,
        },
        { scopeType: "trial", scopeId: trialId },
      );
      const document: TrialDocument = {
        id: uploaded.id,
        name: uploaded.name,
        source: "existing",
        uri: uploaded.url,
      };
      setDocuments((current) => [document, ...current.filter((item) => item.id !== document.id)]);
      setSelectedDocument(document);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Couldn't upload that document. Use a PDF or DOCX up to 10 MB.");
    } finally {
      setUploadingDocument(false);
    }
  };

  const continueToReview = () => {
    if (!trialId) {
      setErr("Select a trial first.");
      return;
    }
    if (!selectedDocument) {
      setErr("Select the visit schedule or a trial document.");
      return;
    }
    if (selectedSites.size === 0) {
      setErr("Select at least one site for review.");
      return;
    }
    setErr("");
    setStep(2);
  };

  const share = async () => {
    if (!trialId || !selectedDocument || selectedSites.size === 0) {
      setStep(1);
      setErr("Review the trial, document and selected sites.");
      return;
    }
    if (via === "email" && recipients.length === 0) {
      setErr("Enter an email or select a site with a PI email.");
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const chosenSites = selectedSiteRows.map((site) => ({
        id: site.id,
        name: site.name,
        reviewer_id: site.piId || null,
      }));
      const response = await api.post("/shares", {
        trial_id: trialId,
        via,
        recipients,
        sites: chosenSites,
        message: message.trim(),
        document_name: selectedDocument.name,
        document_id: selectedDocument.source === "existing" ? selectedDocument.id : null,
        version_note:
          selectedDocument.version ||
          (selectedDocument.source === "schedule" ? "Current approved visit schedule" : "Document shared for PI review"),
      });
      const pdf = response.data?.pdf_link ? `${API_BASE}${response.data.pdf_link}` : undefined;
      setDone({
        link: response.data?.share_link,
        pdf,
        recipientCount: recipients.length,
        siteNames: selectedSiteRows.map((site) => site.name),
      });
      if (via === "pdf" && pdf) Linking.openURL(pdf).catch(() => {
        setErr("The PDF was created, but couldn't be opened on this device.");
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Couldn't share this schedule.");
    } finally {
      setLoading(false);
    }
  };

  const shareAnother = () => {
    setDone(null);
    setStep(1);
    setSelectedSites(new Set());
    setSelectedDocument(null);
    setExtraEmails("");
    setMessage("");
    setQuery("");
    setVia("email");
    setErr("");
  };

  if (done) {
    return (
      <View style={s.page}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <SafeAreaView edges={["top"]} style={s.header}>
          <Pressable onPress={() => router.replace("/(app)/sponsor/dashboard" as never)} hitSlop={10}>
            <ArrowLeft size={20} color={colors.white} />
          </Pressable>
          <Text style={s.headerTitle}>Share Schedule</Text>
          <View style={{ width: 20 }} />
        </SafeAreaView>
        <ScrollView contentContainerStyle={s.successPage}>
          <View style={s.successOrb}>
            <CheckCircle2 size={36} color={colors.success} />
          </View>
          <Text style={s.successTitle}>Sent for PI review</Text>
          <Text style={s.successText}>
            {selectedDocument?.name} was securely shared with the selected trial sites.
          </Text>
          <View style={s.successList}>
            <Text style={s.successListTitle}>SHARED WITH</Text>
            {done.siteNames.map((name) => (
              <View key={name} style={s.successSite}>
                <View style={s.successCheck}><Check size={12} color={colors.white} /></View>
                <Text style={s.successSiteName}>{name}</Text>
                <Text style={s.pendingText}>Pending PI review</Text>
              </View>
            ))}
          </View>
          {!!done.link && (
            <View style={s.linkCard}>
              <Text style={s.eyebrow}>SECURE LINK · EXPIRES IN 7 DAYS</Text>
              <Text selectable numberOfLines={2} style={s.linkText}>{done.link}</Text>
              <Text style={s.copyText}>Press and hold the link to copy it.</Text>
            </View>
          )}
          {!!done.pdf && (
            <Pressable onPress={() => Linking.openURL(done.pdf!)} style={s.secondaryButton}>
              <FileText size={17} color={colors.primary} />
              <Text style={s.secondaryButtonText}>Open Schedule PDF</Text>
            </Pressable>
          )}
          <Pressable onPress={shareAnother} style={s.primaryButton}>
            <Share2 size={16} color={colors.white} />
            <Text style={s.primaryButtonText}>Share Another Document</Text>
          </Pressable>
          <Pressable onPress={() => router.replace("/(app)/sponsor/dashboard" as never)} style={s.textButton}>
            <Text style={s.textButtonLabel}>Back to Dashboard</Text>
          </Pressable>
        </ScrollView>
      </View>
    );
  }

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={step === 2 ? () => setStep(1) : requestClose} hitSlop={10}>
          <ArrowLeft size={20} color={colors.white} />
        </Pressable>
        <Text style={s.headerTitle}>Share Schedule</Text>
        <Pressable onPress={requestClose} hitSlop={10}>
          <X size={19} color={colors.white} />
        </Pressable>
      </SafeAreaView>

      <View style={s.progressWrap}>
        <View style={s.progressTextRow}>
          <Text style={s.progressTitle}>{step === 1 ? "Choose document & sites" : "Review & share"}</Text>
          <Text style={s.progressCount}>STEP {step} OF 2</Text>
        </View>
        <View style={s.progressTrack}>
          <View style={[s.progressFill, { width: step === 1 ? "50%" : "100%" }]} />
        </View>
      </View>

      {loadingData ? (
        <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <>
          <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
            {step === 1 ? (
              <>
                <View>
                  <Text style={s.eyebrow}>TRIAL</Text>
                  <Text style={s.sectionTitle}>Select a trial</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.horizontal}>
                    {trials.map((trial) => {
                      const active = trialId === trial.id;
                      return (
                        <Pressable key={trial.id} onPress={() => selectTrial(trial.id)} style={[s.trialChoice, active && s.trialChoiceActive]}>
                          <Text style={[s.protocol, active && s.lightText]}>{trial.protocolId}</Text>
                          <Text numberOfLines={2} style={[s.trialChoiceTitle, active && s.lightText]}>{trial.title}</Text>
                          <Text style={[s.trialChoiceMeta, active && s.lightMeta]}>{[trial.phase, trial.condition].filter(Boolean).join(" · ")}</Text>
                        </Pressable>
                      );
                    })}
                  </ScrollView>
                </View>

                <View>
                  <Text style={s.eyebrow}>DOCUMENT</Text>
                  <Text style={s.sectionTitle}>Choose what to share</Text>
                  <View style={s.documentList}>
                    <Pressable
                      onPress={() => setSelectedDocument({
                        id: `schedule:${trialId}`,
                        name: `${selectedTrial?.protocolId || "Trial"} Visit Schedule.pdf`,
                        source: "schedule",
                        version: "Current approved visit schedule",
                      })}
                      style={[s.documentRow, selectedDocument?.source === "schedule" && s.documentRowActive]}
                    >
                      <View style={s.documentIcon}><FileText size={18} color={colors.primary} /></View>
                      <View style={s.flex}>
                        <Text style={s.documentName}>Current visit schedule</Text>
                        <Text style={s.documentMeta}>{selectedTrial?.protocolId || "Selected trial"} · Latest saved version</Text>
                      </View>
                      <SelectionMark active={selectedDocument?.source === "schedule"} />
                    </Pressable>
                    {loadingDocuments ? (
                      <ActivityIndicator style={{ marginVertical: 8 }} color={colors.primary} />
                    ) : documents.map((document) => (
                      <Pressable
                        key={document.id}
                        onPress={() => setSelectedDocument(document)}
                        style={[s.documentRow, selectedDocument?.id === document.id && s.documentRowActive]}
                      >
                        <View style={s.documentIcon}><FileText size={18} color={colors.accent} /></View>
                        <View style={s.flex}>
                          <Text numberOfLines={1} style={s.documentName}>{document.name}</Text>
                          <Text style={s.documentMeta}>{document.version || "Existing trial document"}</Text>
                        </View>
                        <SelectionMark active={selectedDocument?.id === document.id} />
                      </Pressable>
                    ))}
                    <Pressable onPress={pickDocument} style={s.uploadButton}>
                      {uploadingDocument
                        ? <ActivityIndicator size="small" color={colors.primary} />
                        : <Upload size={16} color={colors.primary} />}
                      <Text style={s.uploadText}>{uploadingDocument ? "Uploading document..." : "Upload a document from this device"}</Text>
                    </Pressable>
                  </View>
                </View>

                <View>
                  <View style={s.sectionHead}>
                    <View>
                      <Text style={s.eyebrow}>RECIPIENT SITES</Text>
                      <Text style={s.sectionTitle}>Select sites</Text>
                    </View>
                    <Pressable onPress={toggleAllVisible} disabled={!visibleSites.length}>
                      <Text style={s.selectAll}>{allVisibleSelected ? "Deselect all" : "Select all"}</Text>
                    </Pressable>
                  </View>
                  <View style={s.searchBox}>
                    <Search size={16} color={colors.mutedFg} />
                    <TextInput value={query} onChangeText={setQuery} placeholder="Search sites..." placeholderTextColor={colors.mutedFg} style={s.searchInput} />
                  </View>
                  <View style={s.siteList}>
                    {visibleSites.map((site) => {
                      const active = selectedSites.has(site.id);
                      return (
                        <Pressable key={site.id} onPress={() => toggleSite(site.id)} style={[s.siteRow, active && s.siteRowActive]}>
                          <View style={[s.checkbox, active && s.checkboxActive]}>{active && <Check size={13} color={colors.white} />}</View>
                          <View style={s.siteIcon}><MapPin size={16} color={colors.accent} /></View>
                          <View style={s.flex}>
                            <Text style={s.siteName}>{site.name}</Text>
                            <Text style={s.siteMeta}>
                              {site.pi ? `PI · ${site.pi}` : "PI assignment pending"}
                              {site.piEmail ? ` · ${site.piEmail}` : ""}
                            </Text>
                          </View>
                        </Pressable>
                      );
                    })}
                    {!visibleSites.length && <View style={s.empty}><Text style={s.emptyText}>No linked sites are available for this trial yet.</Text></View>}
                  </View>
                  {selectedSiteRows.length > 0 && (
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
                      {selectedSiteRows.map((site) => (
                        <Pressable key={site.id} onPress={() => toggleSite(site.id)} style={s.chip}>
                          <Text numberOfLines={1} style={s.chipText}>{site.name}</Text>
                          <X size={12} color={colors.primary} />
                        </Pressable>
                      ))}
                    </ScrollView>
                  )}
                  {pendingSites.length > 0 && (
                    <View style={s.warning}>
                      <AlertTriangle size={17} color={colors.warning} />
                      <Text style={s.warningText}>
                        {pendingSites.length} selected site{pendingSites.length === 1 ? "" : "s"} still {pendingSites.length === 1 ? "has" : "have"} a pending PI assignment or email. A review task will be created, but email delivery may wait.
                      </Text>
                    </View>
                  )}
                </View>

                <View>
                  <Text style={s.fieldLabel}>ADDITIONAL EMAILS · OPTIONAL</Text>
                  <TextInput
                    value={extraEmails}
                    onChangeText={setExtraEmails}
                    placeholder="pi@hospital.org, crc@hospital.org"
                    placeholderTextColor={colors.mutedFg}
                    autoCapitalize="none"
                    keyboardType="email-address"
                    style={s.emailInput}
                  />
                  <Text style={s.fieldLabel}>MESSAGE TO SITES · OPTIONAL</Text>
                  <TextInput
                    value={message}
                    onChangeText={(value) => setMessage(value.slice(0, 300))}
                    placeholder="Please review the schedule and add any PI notes."
                    placeholderTextColor={colors.mutedFg}
                    multiline
                    textAlignVertical="top"
                    style={s.messageInput}
                  />
                  <Text style={s.characterCount}>{message.length} / 300</Text>
                </View>
              </>
            ) : (
              <>
                <View style={s.reviewHero}>
                  <View style={s.reviewIcon}><ShieldCheck size={25} color={colors.primary} /></View>
                  <View style={s.flex}>
                    <Text style={s.reviewTitle}>Ready for site review</Text>
                    <Text style={s.reviewCopy}>Each selected site receives its own review task. The PI can approve or reject this version without affecting another site&apos;s status.</Text>
                  </View>
                </View>
                <ReviewRow label="Trial" value={`${selectedTrial?.protocolId || "Trial"} · ${selectedTrial?.title || ""}`} />
                <ReviewRow label="Document" value={selectedDocument?.name || "Visit schedule"} />
                <View style={s.reviewCard}>
                  <Text style={s.reviewLabel}>SELECTED SITES · {selectedSiteRows.length}</Text>
                  {selectedSiteRows.map((site) => (
                    <View key={site.id} style={s.reviewSite}>
                      <View style={s.siteIcon}><MapPin size={15} color={colors.accent} /></View>
                      <View style={s.flex}>
                        <Text style={s.siteName}>{site.name}</Text>
                        <Text style={s.siteMeta}>{site.pi ? `PI · ${site.pi}` : "PI assignment pending"}</Text>
                      </View>
                      <Text style={s.pendingText}>Pending</Text>
                    </View>
                  ))}
                </View>
                {!!message.trim() && <ReviewRow label="Message" value={message.trim()} />}
                <View>
                  <Text style={s.eyebrow}>DELIVERY</Text>
                  <Text style={s.sectionTitle}>Choose a secure format</Text>
                  <View style={s.deliveryList}>
                    {([
                      { id: "email", icon: Mail, title: "Email recipients", text: "Send a secure schedule link to PI recipients." },
                      { id: "link", icon: LinkIcon, title: "Generate secure link", text: "Create a link for an approved sharing channel." },
                      { id: "pdf", icon: FileText, title: "Schedule PDF", text: "Generate and open a printable schedule PDF." },
                    ] as const).map((option) => {
                      const active = via === option.id;
                      const Icon = option.icon;
                      return (
                        <Pressable key={option.id} onPress={() => setVia(option.id)} style={[s.delivery, active && s.deliveryActive]}>
                          <View style={[s.deliveryIcon, active && s.deliveryIconActive]}><Icon size={18} color={active ? colors.white : colors.primary} /></View>
                          <View style={s.flex}><Text style={s.deliveryTitle}>{option.title}</Text><Text style={s.deliveryText}>{option.text}</Text></View>
                          <SelectionMark active={active} />
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              </>
            )}
            {!!err && <Text style={s.error}>{err}</Text>}
          </ScrollView>
          <View style={s.footer}>
            {step === 1 ? (
              <>
                <View style={s.flex}>
                  <Text style={s.footerLabel}>{selectedSites.size} SITE{selectedSites.size === 1 ? "" : "S"} SELECTED</Text>
                  <Text numberOfLines={1} style={s.footerMeta}>{selectedDocument?.name || "Choose a document"}</Text>
                </View>
                <Pressable onPress={continueToReview} style={s.shareButton}>
                  <Text style={s.shareButtonText}>Review</Text>
                  <ChevronRight size={17} color={colors.white} />
                </Pressable>
              </>
            ) : (
              <>
                <Pressable onPress={() => setStep(1)} style={s.footerBack}>
                  <Text style={s.footerBackText}>Back</Text>
                </Pressable>
                <Pressable onPress={share} disabled={loading} style={[s.shareButton, s.flex, loading && s.disabled]}>
                  {loading ? <ActivityIndicator color={colors.white} /> : <><Share2 size={17} color={colors.white} /><Text style={s.shareButtonText}>Share for PI Review</Text></>}
                </Pressable>
              </>
            )}
          </View>
        </>
      )}
    </View>
  );
}

function SelectionMark({ active }: { active: boolean }) {
  return (
    <View style={[s.radio, active && s.radioActive]}>
      {active && <View style={s.radioDot} />}
    </View>
  );
}

function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.reviewCard}>
      <Text style={s.reviewLabel}>{label.toUpperCase()}</Text>
      <Text style={s.reviewValue}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  flex: { flex: 1 },
  header: { minHeight: 70, paddingHorizontal: 17, paddingTop: 8, paddingBottom: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.primaryDeep },
  headerTitle: { fontFamily: fonts.semibold, fontSize: 15, color: colors.white },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  progressWrap: { paddingHorizontal: 16, paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.card },
  progressTextRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  progressTitle: { fontFamily: fonts.semibold, fontSize: 11, color: colors.foreground },
  progressCount: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.8, color: colors.primary },
  progressTrack: { height: 4, marginTop: 8, overflow: "hidden", borderRadius: 2, backgroundColor: colors.secondary },
  progressFill: { height: 4, borderRadius: 2, backgroundColor: colors.primary },
  content: { padding: 15, paddingBottom: 26, gap: 24 },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.primary },
  sectionTitle: { marginTop: 3, fontFamily: fonts.heading, fontSize: 17, color: colors.foreground },
  horizontal: { paddingTop: 11, paddingRight: 15, gap: 9 },
  trialChoice: { width: 190, minHeight: 112, padding: 13, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  trialChoiceActive: { borderColor: colors.primary, backgroundColor: colors.primaryDeep },
  protocol: { fontFamily: fonts.mono, fontSize: 10, color: colors.primary },
  lightText: { color: colors.white },
  lightMeta: { color: "rgba(255,255,255,0.72)" },
  trialChoiceTitle: { marginTop: 9, fontFamily: fonts.semibold, fontSize: 12, lineHeight: 16, color: colors.foreground },
  trialChoiceMeta: { marginTop: "auto", paddingTop: 7, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  documentList: { marginTop: 10, gap: 8 },
  documentRow: { minHeight: 62, padding: 11, flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderColor: colors.border, borderRadius: 17, backgroundColor: colors.card },
  documentRowActive: { borderColor: colors.primary, backgroundColor: "rgba(166,33,63,0.035)" },
  documentIcon: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.secondary },
  documentName: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  documentMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  uploadButton: { height: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderWidth: 1, borderStyle: "dashed", borderColor: colors.primary, borderRadius: 14, backgroundColor: colors.card },
  uploadText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.primary },
  localFile: { minHeight: 40, paddingHorizontal: 11, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.secondary },
  localFileName: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, color: colors.foreground },
  sectionHead: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between" },
  selectAll: { paddingVertical: 4, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  searchBox: { height: 42, marginTop: 11, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 12.5, color: colors.foreground, outlineStyle: "none" } as any,
  siteList: { gap: 9, marginTop: 10 },
  siteRow: { minHeight: 65, padding: 11, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 17, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  siteRowActive: { borderColor: colors.primary, backgroundColor: "rgba(166,33,63,0.035)" },
  checkbox: { width: 21, height: 21, borderRadius: 7, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  checkboxActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  siteIcon: { width: 35, height: 35, borderRadius: 13, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(230,155,92,0.13)" },
  siteName: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  siteMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  chips: { paddingTop: 10, gap: 7 },
  chip: { maxWidth: 180, height: 29, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 6, borderRadius: 999, backgroundColor: colors.secondary },
  chipText: { maxWidth: 145, fontFamily: fonts.semibold, fontSize: 9.5, color: colors.primary },
  warning: { marginTop: 10, padding: 11, flexDirection: "row", alignItems: "flex-start", gap: 8, borderRadius: 14, borderWidth: 1, borderColor: "rgba(217,142,45,0.25)", backgroundColor: "rgba(217,142,45,0.08)" },
  warningText: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.foreground },
  fieldLabel: { marginBottom: 6, fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 0.8, color: colors.mutedFg },
  emailInput: { minHeight: 44, marginBottom: 13, paddingHorizontal: 12, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12.5, color: colors.foreground, outlineStyle: "none" } as any,
  messageInput: { minHeight: 88, paddingHorizontal: 12, paddingVertical: 11, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 17, color: colors.foreground, outlineStyle: "none" } as any,
  characterCount: { marginTop: 4, textAlign: "right", fontFamily: fonts.regular, fontSize: 9, color: colors.mutedFg },
  empty: { padding: 18, alignItems: "center", borderRadius: 16, borderWidth: 1, borderStyle: "dashed", borderColor: colors.border, backgroundColor: colors.card },
  emptyText: { fontFamily: fonts.regular, fontSize: 11, color: colors.mutedFg },
  reviewHero: { padding: 15, flexDirection: "row", alignItems: "flex-start", gap: 11, borderRadius: 20, backgroundColor: colors.secondary },
  reviewIcon: { width: 46, height: 46, alignItems: "center", justifyContent: "center", borderRadius: 16, backgroundColor: colors.card },
  reviewTitle: { fontFamily: fonts.heading, fontSize: 15, color: colors.foreground },
  reviewCopy: { marginTop: 4, fontFamily: fonts.regular, fontSize: 10, lineHeight: 15, color: colors.mutedFg },
  reviewCard: { padding: 14, gap: 7, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  reviewLabel: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  reviewValue: { fontFamily: fonts.regular, fontSize: 11.5, lineHeight: 17, color: colors.foreground },
  reviewSite: { minHeight: 50, flexDirection: "row", alignItems: "center", gap: 8, borderTopWidth: 1, borderTopColor: colors.border },
  pendingText: { fontFamily: fonts.semibold, fontSize: 8.5, color: colors.warning },
  deliveryList: { gap: 9, marginTop: 10 },
  delivery: { padding: 12, flexDirection: "row", alignItems: "center", gap: 10, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  deliveryActive: { borderColor: colors.primary },
  deliveryIcon: { width: 39, height: 39, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  deliveryIconActive: { backgroundColor: colors.primary },
  deliveryTitle: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  deliveryText: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 13, color: colors.mutedFg },
  radio: { width: 19, height: 19, borderRadius: 10, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  radioActive: { borderColor: colors.primary },
  radioDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.primary },
  error: { fontFamily: fonts.regular, fontSize: 11, color: colors.destructive },
  footer: { padding: 13, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  footerLabel: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 0.6, color: colors.primary },
  footerMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  shareButton: { minWidth: 112, height: 44, paddingHorizontal: 17, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  shareButtonText: { fontFamily: fonts.bold, fontSize: 11.5, color: colors.white },
  footerBack: { width: 80, height: 44, alignItems: "center", justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: colors.border },
  footerBackText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.foreground },
  disabled: { opacity: 0.65 },
  successPage: { flexGrow: 1, padding: 24, alignItems: "center", justifyContent: "center" },
  successOrb: { width: 76, height: 76, borderRadius: 38, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(92,154,110,0.12)" },
  successTitle: { marginTop: 16, fontFamily: fonts.heading, fontSize: 21, color: colors.foreground },
  successText: { marginTop: 6, maxWidth: 290, textAlign: "center", fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, color: colors.mutedFg },
  successList: { alignSelf: "stretch", marginTop: 21, padding: 14, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  successListTitle: { marginBottom: 4, fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  successSite: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8, borderTopWidth: 1, borderTopColor: colors.border },
  successCheck: { width: 20, height: 20, alignItems: "center", justifyContent: "center", borderRadius: 10, backgroundColor: colors.success },
  successSiteName: { flex: 1, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  linkCard: { alignSelf: "stretch", marginTop: 12, padding: 15, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  linkText: { marginTop: 7, fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 15, color: colors.primary },
  copyText: { marginTop: 8, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  secondaryButton: { alignSelf: "stretch", height: 46, marginTop: 12, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderWidth: 1, borderColor: colors.primary, backgroundColor: colors.card },
  secondaryButtonText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.primary },
  primaryButton: { alignSelf: "stretch", height: 47, marginTop: 10, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  primaryButtonText: { fontFamily: fonts.bold, fontSize: 12, color: colors.white },
  textButton: { marginTop: 7, padding: 8 },
  textButtonLabel: { fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg },
});
