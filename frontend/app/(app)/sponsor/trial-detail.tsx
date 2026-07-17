import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Modal, Platform,
  Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { LinearGradient } from "expo-linear-gradient";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle, ArrowLeft, CalendarDays, CheckCircle2, Download, FileText,
  Mail, MapPin, Pencil, Phone, Plus, RefreshCw, Share2, Target, Upload,
  UserRoundCheck, Users, X,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { getSponsorTrialDetail } from "@/src/features/sponsor/api";
import type {
  RecruitmentFunnel, SponsorTrialDetail as SponsorTrialDetailPayload, SponsorTrialSubject, SponsorTrialTeamMember,
} from "@/src/features/sponsor/types";
import { downloadFile, uploadFile } from "@/src/lib/upload";
import { colors, dawnGradient, fonts, shadows } from "@/src/theme/tokens";

type RichTrial = SponsorTrialDetailPayload & {
  schedule_version?: string;
  updated_at?: string;
  modified_at?: string;
  updated_by_name?: string;
};

type EditForm = {
  title: string;
  phase: string;
  condition: string;
  drug: string;
  duration: string;
  target_enrollment: string;
  recruitment_status: string;
  ctri_number: string;
  description: string;
};

const FUNNEL: { key: keyof RecruitmentFunnel; label: string; tone?: string }[] = [
  { key: "screened", label: "Screened" },
  { key: "screen_fail", label: "Screen fail", tone: colors.destructive },
  { key: "randomized", label: "Randomized" },
  { key: "active", label: "Active", tone: colors.success },
  { key: "withdrawn", label: "Withdrawn", tone: colors.warning },
  { key: "dropout", label: "Dropout", tone: colors.warning },
  { key: "follow_up", label: "Follow-up", tone: colors.info },
  { key: "completed", label: "Completed", tone: colors.success },
];

const dateLabel = (value?: string) => {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
};

const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "—";

export default function SponsorTrialDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [trial, setTrial] = useState<RichTrial | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [expandedSite, setExpandedSite] = useState<string | null>(null);
  const [showAllSubjects, setShowAllSubjects] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState("");
  const [editSuccess, setEditSuccess] = useState("");
  const [editForm, setEditForm] = useState<EditForm>({
    title: "", phase: "", condition: "", drug: "", duration: "",
    target_enrollment: "", recruitment_status: "", ctri_number: "", description: "",
  });

  const load = useCallback(async () => {
    if (!id) {
      setError("Trial ID is missing.");
      setLoading(false);
      setRefreshing(false);
      return;
    }
    setError("");
    try {
      setTrial(await getSponsorTrialDetail(id) as RichTrial);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load this trial.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const target = Number(trial?.target_enrollment || 0);
  const enrolled = Number(trial?.enrolled_count || 0);
  const enrollmentPct = target > 0 ? Math.min(100, Math.round((enrolled / target) * 100)) : 0;
  const protocolDocument = useMemo(
    () => trial?.documents.find((document) => /protocol/i.test(document.name)) || trial?.documents[0],
    [trial?.documents],
  );

  const openLink = async (url: string, failure: string) => {
    try {
      if (!await Linking.canOpenURL(url)) throw new Error(failure);
      await Linking.openURL(url);
    } catch {
      Alert.alert("Action unavailable", failure);
    }
  };

  const uploadDocument = async () => {
    if (!trial || uploading) return;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "image/png", "image/jpeg", "application/msword",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        copyToCacheDirectory: true,
      });
      if (result.canceled) return;
      setUploading(true);
      const asset = result.assets[0];
      await uploadFile(
        { uri: asset.uri, name: asset.name || "Trial document", mimeType: asset.mimeType, file: (asset as any).file },
        { scopeType: "trial", scopeId: trial.id },
      );
      await load();
      Alert.alert("Document uploaded", "The document is now available to the trial team.");
    } catch (e: any) {
      Alert.alert("Upload failed", e?.response?.data?.detail || e?.message || "Please try again.");
    } finally {
      setUploading(false);
    }
  };

  const download = async (document = protocolDocument) => {
    if (!document) {
      Alert.alert("No protocol document", "Upload a protocol document before downloading it.");
      return;
    }
    try {
      await downloadFile(document);
    } catch (e: any) {
      Alert.alert("Download failed", e?.message || "Couldn't open this document.");
    }
  };

  const openEdit = () => {
    if (!trial) return;
    setEditError("");
    setEditForm({
      title: trial.title || "",
      phase: trial.phase || "",
      condition: trial.condition || "",
      drug: trial.drug || "",
      duration: trial.duration || "",
      target_enrollment: trial.target_enrollment == null ? "" : String(trial.target_enrollment),
      recruitment_status: trial.recruitment_status || trial.status || "",
      ctri_number: trial.ctri_number || "",
      description: trial.description || "",
    });
    setShowEdit(true);
  };

  const updateField = (field: keyof EditForm, value: string) =>
    setEditForm((current) => ({ ...current, [field]: value }));

  const saveEdit = async () => {
    if (!trial || savingEdit) return;
    const title = editForm.title.trim();
    const phase = editForm.phase.trim();
    const condition = editForm.condition.trim();
    const rawTarget = editForm.target_enrollment.trim();
    const targetEnrollment = rawTarget === "" ? null : Number(rawTarget);
    if (!title || !phase || !condition) {
      setEditError("Title, phase, and condition are required.");
      return;
    }
    if (rawTarget !== "" && (!Number.isInteger(targetEnrollment) || Number(targetEnrollment) < 0)) {
      setEditError("Target enrollment must be a whole number of zero or more.");
      return;
    }
    setSavingEdit(true);
    setEditError("");
    try {
      await api.patch(`/trials/${trial.id}`, {
        title,
        phase,
        condition,
        drug: editForm.drug.trim(),
        duration: editForm.duration.trim(),
        target_enrollment: targetEnrollment,
        recruitment_status: editForm.recruitment_status.trim(),
        ctri_number: editForm.ctri_number.trim(),
        description: editForm.description.trim(),
      });
      await load();
      setShowEdit(false);
      setEditSuccess("Trial details updated successfully.");
    } catch (e: any) {
      setEditError(e?.response?.data?.detail || "Couldn't update this trial. Please try again.");
    } finally {
      setSavingEdit(false);
    }
  };

  if (loading) return (
    <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /><Text style={s.muted}>Loading trial details…</Text></View>
  );
  if (error || !trial) return (
    <View style={s.center}>
      <AlertTriangle size={29} color={colors.destructive} />
      <Text style={s.error}>{error || "Trial not found."}</Text>
      <Pressable onPress={() => { setLoading(true); load(); }} style={s.primary}>
        <RefreshCw size={16} color={colors.white} /><Text style={s.primaryText}>Try again</Text>
      </Pressable>
    </View>
  );

  const visibleSubjects = showAllSubjects ? trial.subjects : trial.subjects.slice(0, 4);
  const modifiedAt = trial.updated_at || trial.modified_at;

  return (
    <View style={s.page}>
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}><ArrowLeft size={20} color={colors.white} /></Pressable>
        <View style={s.headerCopy}>
          <Text style={s.headerEyebrow}>SPONSOR / CRO</Text>
          <Text numberOfLines={1} style={s.headerTitle}>{trial.protocol_id}</Text>
        </View>
        {trial.capabilities.can_share ? (
          <Pressable testID="share-trial-schedule" onPress={() => router.push({ pathname: "/(app)/sponsor/share-schedule", params: { id: trial.id } } as never)} hitSlop={10}>
            <Share2 size={19} color={colors.white} />
          </Pressable>
        ) : <View style={s.headerSpacer} />}
      </SafeAreaView>

      <ScrollView
        contentContainerStyle={s.content}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.primary} />}
      >
        {!!editSuccess && (
          <Pressable accessibilityRole="alert" onPress={() => setEditSuccess("")} style={s.successBanner}>
            <CheckCircle2 size={17} color={colors.success} />
            <Text style={s.successText}>{editSuccess}</Text>
            <X size={15} color={colors.success} />
          </Pressable>
        )}
        <LinearGradient colors={dawnGradient as any} style={s.hero}>
          <View style={s.between}>
            <View style={s.protocolPill}><Text style={s.protocol}>{trial.protocol_id}</Text></View>
            <View style={s.status}><Text style={s.statusText}>{trial.recruitment_status || trial.status}</Text></View>
          </View>
          <Text style={s.title}>{trial.title}</Text>
          <View style={s.detailGrid}>
            <HeroDetail label="CTRI number" value={trial.ctri_number || "Not recorded"} />
            <HeroDetail label="Phase" value={trial.phase || "Not recorded"} />
            <HeroDetail label="Disease" value={trial.condition || "Not recorded"} />
            <HeroDetail label="Drug" value={trial.drug || "Not recorded"} />
            <HeroDetail label="Duration" value={trial.duration || "Not recorded"} />
            <HeroDetail label="Schedule" value={trial.schedule_version || `${trial.total_visits} visits`} />
          </View>
          <View style={s.createdRow}>
            <UserRoundCheck size={15} color="rgba(255,255,255,0.82)" />
            <Text style={s.createdText}>
              Created by <Text style={s.createdStrong}>{trial.created_by_name || "Unknown"}</Text>
              {trial.created_by_role ? ` · ${trial.created_by_role}` : ""}
            </Text>
            <Text style={s.createdDate}>{dateLabel(trial.created_at)}</Text>
          </View>
          {!!modifiedAt && <Text style={s.modified}>Last modified {dateLabel(modifiedAt)}{trial.updated_by_name ? ` by ${trial.updated_by_name}` : ""}</Text>}
        </LinearGradient>

        <Section title="Recruitment · Across all sites" icon={Target}>
          <View style={s.summaryGrid}>
            <Metric value={String(trial.site_count)} label="Total sites" />
            <Metric value={target ? String(target) : "—"} label="Sample size" />
          </View>
          <Funnel data={trial.recruitment} />
          <Text style={s.smallCaps}>ENROLLMENT</Text>
          <Progress value={enrollmentPct} />
          <Text style={s.progressCopy}>{enrolled} / {target || "—"} enrolled{target ? ` (${enrollmentPct}%)` : ""}</Text>
        </Section>

        <Section title="Sites · Recruitment status" icon={MapPin} action={
          trial.capabilities.can_add_site ? (
            <Pressable testID="add-site-to-trial" onPress={() => router.push({ pathname: "/(app)/sponsor/sites", params: { trialId: trial.id } } as never)} style={s.inlineAction}>
              <Plus size={13} color={colors.info} /><Text style={s.inlineActionText}>Add Site</Text>
            </Pressable>
          ) : undefined
        }>
          {trial.sites.length ? trial.sites.map((site) => {
            const expanded = expandedSite === site.id;
            return (
              <Pressable
                key={site.id}
                testID={`open-site-${site.id}`}
                onPress={() => setExpandedSite(expanded ? null : site.id)}
                style={({ pressed }) => [s.siteCard, pressed && s.pressed]}
              >
                <View style={s.between}>
                  <View style={s.flex}>
                    <Text style={s.siteName}>{site.name}</Text>
                    <Text style={s.siteMeta}>{[site.address, site.city, site.state].filter(Boolean).join(", ") || "Address not provided"}</Text>
                  </View>
                  <View style={s.subjectCount}><Users size={12} color={colors.primary} /><Text style={s.subjectCountText}>{site.enrolled}</Text></View>
                </View>
                <View style={s.siteDetails}>
                  <Text style={s.siteDetail}>PI: <Text style={s.siteDetailStrong}>{site.pi_name || "Not assigned"}</Text></Text>
                  <Text style={s.siteDetail}>Department: <Text style={s.siteDetailStrong}>{site.department || "Not recorded"}</Text></Text>
                </View>
                <Progress value={site.enrollment_pct} />
                <Text style={s.progressCopy}>{site.enrolled} / {site.target_enrollment || "—"} enrolled · {site.visit_compliance}% visit compliance</Text>
                {expanded && (
                  <View style={s.expanded}>
                    <Funnel data={site.recruitment} compact />
                    {!!site.pi_phone && <ContactAction icon={Phone} label={site.pi_phone} onPress={() => openLink(`tel:${site.pi_phone}`, "This phone number couldn't be opened.")} />}
                    {!!site.pi_email && <ContactAction icon={Mail} label={site.pi_email} onPress={() => openLink(`mailto:${site.pi_email}`, "This email address couldn't be opened.")} />}
                    {!!site.overdue_visits && <Text style={s.warning}>{site.overdue_visits} overdue visit{site.overdue_visits === 1 ? "" : "s"} need attention</Text>}
                  </View>
                )}
              </Pressable>
            );
          }) : <Empty copy="No sites have been assigned to this trial yet." />}
        </Section>

        <Section title="Trial Team" icon={Users}>
          {trial.team.length ? trial.team.map((member) => <TeamCard key={member.id} member={member} openLink={openLink} />) : (
            <Empty copy="Team contacts will appear when staff are assigned." />
          )}
        </Section>

        <Section title="De-identified subject journeys" icon={UserRoundCheck} action={
          trial.subjects.length > 4 ? (
            <Pressable testID="open-subjects" onPress={() => setShowAllSubjects((value) => !value)}>
              <Text style={s.inlineActionText}>{showAllSubjects ? "Show less" : `View all ${trial.subjects.length}`}</Text>
            </Pressable>
          ) : undefined
        }>
          {visibleSubjects.length ? visibleSubjects.map((subject) => (
            <SubjectRow key={subject.id} subject={subject} totalVisits={trial.total_visits} />
          )) : <Empty copy="No subjects are enrolled yet." />}
          {!!trial.subjects.length && <Text style={s.privacy}>Only study IDs and initials are shown. Direct patient identifiers stay with the clinical site.</Text>}
        </Section>

        <Section title="Visit Schedule" icon={CalendarDays} action={
          trial.capabilities.can_manage_schedule ? (
            <Pressable testID="manage-trial-schedule" onPress={() => router.push({ pathname: "/(app)/sponsor/visit-schedule", params: { id: trial.id } } as never)}>
              <Text style={s.inlineActionText}>{trial.visits.length ? "Edit schedule" : "Build schedule"}</Text>
            </Pressable>
          ) : undefined
        }>
          {trial.visits.slice(0, 4).map((visit, index) => (
            <View key={visit.id || `${visit.name}-${index}`} style={s.visitRow}>
              <View style={s.visitIndex}><Text style={s.visitIndexText}>{visit.visit_number || index + 1}</Text></View>
              <View style={s.flex}>
                <Text style={s.visitName}>{visit.name || `Visit ${index + 1}`}</Text>
                <Text style={s.visitMeta}>Day {visit.day_offset || 0} · ±{visit.window_days || 0} days</Text>
              </View>
            </View>
          ))}
          {!trial.visits.length && <Empty copy="No visit schedule has been created." />}
          {trial.visits.length > 4 && <Text style={s.moreText}>+ {trial.visits.length - 4} more scheduled visits</Text>}
        </Section>

        <Section title="Documents & version history" icon={FileText} action={
          <Pressable testID="upload-trial-document" onPress={uploadDocument} disabled={uploading} style={s.inlineAction}>
            {uploading ? <ActivityIndicator size="small" color={colors.info} /> : <Upload size={13} color={colors.info} />}
            <Text style={s.inlineActionText}>{uploading ? "Uploading…" : "Upload"}</Text>
          </Pressable>
        }>
          {trial.documents.length ? trial.documents.map((document, index) => (
            <Pressable key={document.id} testID={`download-document-${document.id}`} onPress={() => download(document)} style={s.documentRow}>
              <View style={s.documentIcon}><FileText size={17} color={colors.info} /></View>
              <View style={s.flex}>
                <Text numberOfLines={1} style={s.documentName}>{document.name}</Text>
                <Text style={s.documentMeta}>
                  {index === 0 ? "Latest · " : ""}{dateLabel(document.created_at)}
                  {document.size ? ` · ${Math.max(1, Math.round(document.size / 1024))} KB` : ""}
                </Text>
              </View>
              {index === 0 ? <CheckCircle2 size={16} color={colors.success} /> : <Download size={16} color={colors.info} />}
            </Pressable>
          )) : <Empty copy="No persistent trial documents have been uploaded." />}
        </Section>

        <View style={s.actions}>
          <Pressable testID="edit-trial" onPress={openEdit} style={s.secondary}>
            <Pencil size={17} color={colors.primary} /><Text style={s.secondaryText}>Edit Trial</Text>
          </Pressable>
          <Pressable testID="download-protocol" onPress={() => download()} style={s.secondary}>
            <Download size={17} color={colors.primary} /><Text style={s.secondaryText}>Download Protocol</Text>
          </Pressable>
        </View>
        {trial.capabilities.can_share && (
          <Pressable testID="share-schedule" onPress={() => router.push({ pathname: "/(app)/sponsor/share-schedule", params: { id: trial.id } } as never)} style={s.primaryWide}>
            <Share2 size={17} color={colors.white} /><Text style={s.primaryText}>Share Schedule</Text>
          </Pressable>
        )}
      </ScrollView>

      <Modal
        visible={showEdit}
        transparent
        animationType="slide"
        onRequestClose={() => !savingEdit && setShowEdit(false)}
      >
        <KeyboardAvoidingView style={s.modalRoot} behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <Pressable style={s.modalBackdrop} onPress={() => !savingEdit && setShowEdit(false)} />
          <View style={s.editSheet}>
            <View style={s.sheetHandle} />
            <View style={s.sheetHeader}>
              <View style={s.flex}>
                <Text style={s.sheetEyebrow}>TRIAL MANAGEMENT</Text>
                <Text style={s.sheetTitle}>Edit trial details</Text>
                <Text style={s.sheetSubtitle}>Changes are saved to this trial and recorded in its audit history.</Text>
              </View>
              <Pressable accessibilityLabel="Close edit trial" onPress={() => !savingEdit && setShowEdit(false)} style={s.closeButton}>
                <X size={18} color={colors.foreground} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={s.form} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              <EditField label="Protocol ID" value={trial.protocol_id} readOnly helper="Protocol ID cannot be changed." />
              <EditField required label="Trial title" value={editForm.title} onChangeText={(value) => updateField("title", value)} />
              <View style={s.formRow}>
                <View style={s.formHalf}><EditField required label="Phase" value={editForm.phase} onChangeText={(value) => updateField("phase", value)} placeholder="Phase III" /></View>
                <View style={s.formHalf}><EditField label="CTRI number" value={editForm.ctri_number} onChangeText={(value) => updateField("ctri_number", value)} placeholder="CTRI/2026/…" /></View>
              </View>
              <EditField required label="Condition / indication" value={editForm.condition} onChangeText={(value) => updateField("condition", value)} />
              <View style={s.formRow}>
                <View style={s.formHalf}><EditField label="Drug / intervention" value={editForm.drug} onChangeText={(value) => updateField("drug", value)} /></View>
                <View style={s.formHalf}><EditField label="Duration" value={editForm.duration} onChangeText={(value) => updateField("duration", value)} placeholder="18 months" /></View>
              </View>
              <View style={s.formRow}>
                <View style={s.formHalf}>
                  <EditField label="Target enrollment" value={editForm.target_enrollment} onChangeText={(value) => updateField("target_enrollment", value.replace(/[^\d]/g, ""))} keyboardType="number-pad" placeholder="100" />
                </View>
                <View style={s.formHalf}><EditField label="Recruitment status" value={editForm.recruitment_status} onChangeText={(value) => updateField("recruitment_status", value)} placeholder="Recruiting" /></View>
              </View>
              <EditField label="Description" value={editForm.description} onChangeText={(value) => updateField("description", value)} multiline placeholder="Study summary and objectives" />
              {!!editError && (
                <View accessibilityRole="alert" style={s.formErrorBox}>
                  <AlertTriangle size={16} color={colors.destructive} />
                  <Text style={s.formError}>{editError}</Text>
                </View>
              )}
            </ScrollView>
            <View style={s.sheetActions}>
              <Pressable onPress={() => setShowEdit(false)} disabled={savingEdit} style={s.cancelButton}>
                <Text style={s.cancelText}>Cancel</Text>
              </Pressable>
              <Pressable testID="save-trial-edit" onPress={saveEdit} disabled={savingEdit} style={[s.saveButton, savingEdit && s.disabled]}>
                {savingEdit ? <ActivityIndicator size="small" color={colors.white} /> : <CheckCircle2 size={17} color={colors.white} />}
                <Text style={s.saveText}>{savingEdit ? "Saving…" : "Save changes"}</Text>
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

function EditField({
  label, value, onChangeText, placeholder, helper, required, readOnly, multiline, keyboardType,
}: {
  label: string; value: string; onChangeText?: (value: string) => void; placeholder?: string;
  helper?: string; required?: boolean; readOnly?: boolean; multiline?: boolean;
  keyboardType?: "default" | "number-pad";
}) {
  return (
    <View>
      <Text style={s.fieldLabel}>{label}{required ? <Text style={s.required}> *</Text> : null}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        editable={!readOnly}
        placeholder={placeholder}
        placeholderTextColor={colors.mutedFg}
        multiline={multiline}
        keyboardType={keyboardType}
        style={[s.input, readOnly && s.readOnlyInput, multiline && s.textarea]}
      />
      {!!helper && <Text style={s.fieldHelper}>{helper}</Text>}
    </View>
  );
}

function Section({ title, icon: Icon, action, children }: { title: string; icon: any; action?: React.ReactNode; children: React.ReactNode }) {
  return <View style={s.card}><View style={s.sectionHead}><View style={s.sectionIdentity}><Icon size={16} color={colors.primary} /><Text style={s.sectionTitle}>{title}</Text></View>{action}</View>{children}</View>;
}

function HeroDetail({ label, value }: { label: string; value: string }) {
  return <View style={s.heroDetail}><Text style={s.heroDetailLabel}>{label}</Text><Text numberOfLines={2} style={s.heroDetailValue}>{value}</Text></View>;
}

function Metric({ value, label }: { value: string; label: string }) {
  return <View style={s.metric}><Text style={s.metricValue}>{value}</Text><Text style={s.metricLabel}>{label}</Text></View>;
}

function Funnel({ data, compact = false }: { data: RecruitmentFunnel; compact?: boolean }) {
  return <View style={[s.funnel, compact && s.funnelCompact]}>{FUNNEL.map((field) => (
    <View key={field.key} style={s.funnelCell}>
      <Text style={[s.funnelValue, field.tone ? { color: field.tone } : null]}>{Number(data?.[field.key] || 0)}</Text>
      <Text style={s.funnelLabel}>{field.label}</Text>
    </View>
  ))}</View>;
}

function Progress({ value }: { value: number }) {
  return <View style={s.track}><LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={[s.fill, { width: `${Math.max(value ? 2 : 0, Math.min(100, value))}%` }]} /></View>;
}

function ContactAction({ icon: Icon, label, onPress }: { icon: any; label: string; onPress: () => void }) {
  return <Pressable onPress={onPress} style={s.contactAction}><Icon size={14} color={colors.info} /><Text numberOfLines={1} style={s.contactActionText}>{label}</Text></Pressable>;
}

function TeamCard({ member, openLink }: { member: SponsorTrialTeamMember; openLink: (url: string, failure: string) => Promise<void> }) {
  return (
    <View style={s.teamCard}>
      <View style={s.avatar}><Text style={s.avatarText}>{initials(member.name)}</Text></View>
      <View style={s.memberCopy}>
        <Text numberOfLines={1} style={s.memberName}>{member.name || "Unnamed team member"}</Text>
        <Text numberOfLines={1} style={s.memberMeta}>{[member.designation, member.organization].filter(Boolean).join(" · ") || member.role}</Text>
        <View style={s.memberActions}>
          {!!member.phone && <ContactAction icon={Phone} label={member.phone} onPress={() => openLink(`tel:${member.phone}`, "This phone number couldn't be opened.")} />}
          {!!member.email && <ContactAction icon={Mail} label={member.email} onPress={() => openLink(`mailto:${member.email}`, "This email address couldn't be opened.")} />}
        </View>
      </View>
      <View style={s.roleBadge}><Text style={s.roleText}>{member.role || "Team"}</Text></View>
    </View>
  );
}

function SubjectRow({ subject, totalVisits }: { subject: SponsorTrialSubject; totalVisits: number }) {
  const progress = totalVisits > 0 ? Math.min(100, Math.round((subject.visits_completed / totalVisits) * 100)) : 0;
  return (
    <View style={s.subjectRow}>
      <View style={s.subjectAvatar}><Text style={s.subjectAvatarText}>{subject.initials || "—"}</Text></View>
      <View style={s.flex}>
        <View style={s.between}><Text style={s.subjectId}>{subject.subject_id}</Text><Text style={s.subjectStatus}>{subject.status}</Text></View>
        <Text style={s.subjectMeta}>{subject.site} · Enrolled {dateLabel(subject.enrolled_at)}</Text>
        <Progress value={progress} />
        <Text style={s.subjectMeta}>{subject.visits_completed}/{totalVisits || "—"} visits completed</Text>
      </View>
    </View>
  );
}

function Empty({ copy }: { copy: string }) {
  return <View style={s.empty}><Text style={s.emptyText}>{copy}</Text></View>;
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  flex: { flex: 1 },
  center: { flex: 1, padding: 30, alignItems: "center", justifyContent: "center", gap: 13, backgroundColor: colors.background },
  error: { textAlign: "center", fontFamily: fonts.regular, fontSize: 13, color: colors.destructive },
  muted: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
  header: { minHeight: 72, paddingHorizontal: 17, paddingTop: 8, paddingBottom: 12, flexDirection: "row", alignItems: "center", gap: 13, backgroundColor: colors.primaryDeep },
  headerCopy: { flex: 1 },
  headerSpacer: { width: 19 },
  headerEyebrow: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 1.1, color: "rgba(255,255,255,0.62)" },
  headerTitle: { marginTop: 2, fontFamily: fonts.semibold, fontSize: 15, color: colors.white },
  content: { padding: 15, paddingBottom: 38, gap: 13 },
  successBanner: { paddingHorizontal: 12, minHeight: 43, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 15, borderWidth: 1, borderColor: colors.success + "45", backgroundColor: colors.success + "12" },
  successText: { flex: 1, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.success },
  hero: { padding: 18, borderRadius: 24, ...shadows.md },
  between: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  protocolPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.16)" },
  protocol: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.white },
  status: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.17)", borderWidth: 1, borderColor: "rgba(255,255,255,0.20)" },
  statusText: { fontFamily: fonts.semibold, fontSize: 9.5, color: colors.white, textTransform: "capitalize" },
  title: { marginTop: 14, fontFamily: fonts.heading, fontSize: 19, lineHeight: 24, color: colors.white },
  detailGrid: { marginTop: 15, flexDirection: "row", flexWrap: "wrap", rowGap: 11 },
  heroDetail: { width: "50%", paddingRight: 8 },
  heroDetailLabel: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.7, textTransform: "uppercase", color: "rgba(255,255,255,0.60)" },
  heroDetailValue: { marginTop: 2, fontFamily: fonts.medium, fontSize: 11, lineHeight: 15, color: colors.white },
  createdRow: { marginTop: 15, paddingTop: 12, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.18)", flexDirection: "row", alignItems: "center", gap: 6 },
  createdText: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, color: "rgba(255,255,255,0.82)" },
  createdStrong: { fontFamily: fonts.semibold },
  createdDate: { fontFamily: fonts.regular, fontSize: 9, color: "rgba(255,255,255,0.67)" },
  modified: { marginTop: 6, fontFamily: fonts.regular, fontSize: 9, color: "rgba(255,255,255,0.67)" },
  card: { padding: 15, borderRadius: 21, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, gap: 12, ...shadows.sm },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  sectionIdentity: { flex: 1, flexDirection: "row", alignItems: "center", gap: 7 },
  sectionTitle: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground },
  inlineAction: { flexDirection: "row", alignItems: "center", gap: 4 },
  inlineActionText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.info },
  summaryGrid: { flexDirection: "row", gap: 8 },
  metric: { flex: 1, padding: 10, alignItems: "center", borderRadius: 13, backgroundColor: colors.surface },
  metricValue: { fontFamily: fonts.heading, fontSize: 18, color: colors.primaryDeep },
  metricLabel: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9, color: colors.mutedFg },
  funnel: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  funnelCompact: { marginTop: 2 },
  funnelCell: { width: "23%", minHeight: 50, padding: 6, alignItems: "center", justifyContent: "center", borderRadius: 11, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  funnelValue: { fontFamily: fonts.heading, fontSize: 14, color: colors.foreground },
  funnelLabel: { marginTop: 2, textAlign: "center", fontFamily: fonts.regular, fontSize: 7.5, color: colors.mutedFg },
  smallCaps: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  track: { height: 7, overflow: "hidden", borderRadius: 999, backgroundColor: colors.surface },
  fill: { height: "100%", borderRadius: 999 },
  progressCopy: { marginTop: -5, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  siteCard: { padding: 12, borderRadius: 16, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, gap: 10 },
  pressed: { opacity: 0.82, transform: [{ scale: 0.99 }] },
  siteName: { fontFamily: fonts.semibold, fontSize: 12.5, color: colors.foreground },
  siteMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  subjectCount: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.card },
  subjectCountText: { fontFamily: fonts.semibold, fontSize: 10, color: colors.primary },
  siteDetails: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  siteDetail: { width: "47%", fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  siteDetailStrong: { color: colors.foreground, fontFamily: fonts.medium },
  expanded: { paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.border, gap: 8 },
  warning: { fontFamily: fonts.medium, fontSize: 10, color: colors.destructive },
  teamCard: { padding: 11, flexDirection: "row", alignItems: "flex-start", gap: 9, borderRadius: 15, backgroundColor: colors.surface },
  avatar: { width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: colors.primaryDeep },
  avatarText: { fontFamily: fonts.bold, fontSize: 10.5, color: colors.white },
  memberCopy: { flex: 1, minWidth: 0 },
  memberName: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  memberMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9, color: colors.mutedFg },
  memberActions: { marginTop: 7, gap: 5 },
  contactAction: { minWidth: 0, flexDirection: "row", alignItems: "center", gap: 6 },
  contactActionText: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, color: colors.info },
  roleBadge: { paddingHorizontal: 7, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.card },
  roleText: { fontFamily: fonts.semibold, fontSize: 8, color: colors.primary, textTransform: "uppercase" },
  subjectRow: { padding: 10, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  subjectAvatar: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: colors.primaryDeep },
  subjectAvatarText: { fontFamily: fonts.bold, fontSize: 10, color: colors.white },
  subjectId: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  subjectStatus: { fontFamily: fonts.semibold, fontSize: 8, color: colors.success, textTransform: "capitalize" },
  subjectMeta: { marginTop: 4, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  privacy: { padding: 10, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.mutedFg, backgroundColor: colors.surface, borderRadius: 12 },
  visitRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  visitIndex: { width: 30, height: 30, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  visitIndexText: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.primary },
  visitName: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  visitMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  moreText: { textAlign: "center", fontFamily: fonts.semibold, fontSize: 9.5, color: colors.info },
  documentRow: { padding: 10, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 14, backgroundColor: colors.surface },
  documentIcon: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: colors.card },
  documentName: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  documentMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  empty: { paddingVertical: 14, alignItems: "center" },
  emptyText: { textAlign: "center", fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  actions: { flexDirection: "row", gap: 9 },
  primary: { flex: 1, minHeight: 46, paddingHorizontal: 12, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  primaryWide: { minHeight: 47, paddingHorizontal: 12, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  primaryText: { fontFamily: fonts.bold, fontSize: 11, color: colors.white },
  secondary: { flex: 1, minHeight: 46, paddingHorizontal: 10, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderWidth: 1, borderColor: colors.primary, backgroundColor: colors.card },
  secondaryText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  modalBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(46,27,51,0.48)" },
  editSheet: { maxHeight: "92%", paddingTop: 10, borderTopLeftRadius: 28, borderTopRightRadius: 28, backgroundColor: colors.background, ...shadows.md },
  sheetHandle: { alignSelf: "center", width: 42, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: 13 },
  sheetHeader: { paddingHorizontal: 18, paddingBottom: 14, flexDirection: "row", alignItems: "flex-start", gap: 12, borderBottomWidth: 1, borderBottomColor: colors.border },
  sheetEyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.primary },
  sheetTitle: { marginTop: 3, fontFamily: fonts.heading, fontSize: 20, color: colors.foreground },
  sheetSubtitle: { marginTop: 4, maxWidth: 290, fontFamily: fonts.regular, fontSize: 10.5, lineHeight: 15, color: colors.mutedFg },
  closeButton: { width: 35, height: 35, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.surface },
  form: { padding: 18, gap: 14 },
  formRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  formHalf: { flex: 1 },
  fieldLabel: { marginBottom: 6, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 0.4, color: colors.mutedFg, textTransform: "uppercase" },
  required: { color: colors.destructive },
  input: { minHeight: 44, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12, color: colors.foreground, outlineStyle: "none" } as any,
  readOnlyInput: { color: colors.mutedFg, backgroundColor: colors.surface },
  textarea: { minHeight: 88, textAlignVertical: "top" },
  fieldHelper: { marginTop: 4, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  formErrorBox: { padding: 11, flexDirection: "row", alignItems: "flex-start", gap: 8, borderRadius: 13, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "0D" },
  formError: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, lineHeight: 15, color: colors.destructive },
  sheetActions: { paddingHorizontal: 18, paddingTop: 12, paddingBottom: Platform.OS === "ios" ? 28 : 18, flexDirection: "row", gap: 10, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  cancelButton: { flex: 1, minHeight: 47, alignItems: "center", justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background },
  cancelText: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  saveButton: { flex: 1.5, minHeight: 47, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 999, backgroundColor: colors.primary },
  saveText: { fontFamily: fonts.bold, fontSize: 11.5, color: colors.white },
  disabled: { opacity: 0.62 },
});
