import React from "react";
import { StyleSheet, View } from "react-native";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react-native";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, spacing } from "@/src/theme/tokens";
import {
  blockHeadline, classificationLabel, issueLines, reviewStatusLabel, successNote,
  type IdentityBlock, type ProtocolIdentity,
} from "./protocolIdentity";

/**
 * Shows the outcome of the protocol identity gate on an upload screen.
 *
 * - `block`: the upload was refused (quarantined / unverified / being extracted).
 *   Rendered as a warning with the reason, the cited evidence and what happens
 *   next - never as a generic extraction error.
 * - `identity` with no block: a permitted upload; only repeats and new versions
 *   are worth a line.
 */
export function ProtocolIdentityNotice({
  block, identity, onOpenReview,
}: {
  block?: IdentityBlock | null;
  identity?: ProtocolIdentity | null;
  onOpenReview?: (identityId: string) => void;
}) {
  if (block) {
    const subject = block.identity;
    const lines = issueLines(subject);
    const pending = subject?.review_status === "PENDING_REVIEW";
    return (
      <View testID="protocol-identity-blocked">
      <Card style={styles.blocked}>
        <View style={styles.row}>
          <AlertTriangle size={20} color={colors.warning} />
          <Body weight="700" style={styles.flex}>{blockHeadline(block)}</Body>
        </View>
        <Small style={styles.copy}>{block.message}</Small>
        {lines.map((line) => <Small key={line} style={styles.line}>• {line}</Small>)}
        {subject ? (
          <Small style={styles.meta}>
            {classificationLabel(subject.classification)} · {reviewStatusLabel(subject.review_status)}
            {subject.filename ? ` · ${subject.filename}` : ""}
          </Small>
        ) : null}
        {block.next_action ? <Small style={styles.copy}>{block.next_action}</Small> : null}
        {pending && subject && onOpenReview ? (
          <View style={styles.action}>
            <Button variant="secondary" onPress={() => onOpenReview(subject.id)}>
              Open protocol review
            </Button>
          </View>
        ) : null}
      </Card>
      </View>
    );
  }
  const note = successNote(identity);
  if (!note) return null;
  return (
    <View testID="protocol-identity-note">
    <Card style={styles.note}>
      <View style={styles.row}>
        {identity?.classification === "NEW_VERSION"
          ? <Info size={18} color={colors.primary} />
          : <CheckCircle2 size={18} color={colors.success} />}
        <Small style={styles.flex}>{note}</Small>
      </View>
    </Card>
    </View>
  );
}

const styles = StyleSheet.create({
  blocked: { borderColor: colors.warning, borderWidth: 1, gap: 6, marginBottom: spacing.md },
  note: { gap: 6, marginBottom: spacing.md },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  flex: { flex: 1 },
  copy: { marginTop: 2 },
  line: { marginTop: 2 },
  meta: { marginTop: 6, color: colors.mutedFg },
  action: { marginTop: 10 },
});
