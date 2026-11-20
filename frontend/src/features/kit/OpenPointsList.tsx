import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Check } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import { FOCUS as G } from "./focus";
import type { OpenPointView } from "./openPoints";

/**
 * The points a reviewer must check against the protocol, each with the protocol's own words and
 * page, the visits it concerns and what the schedule does meanwhile. With onToggle the reviewer
 * ticks each one off; without it the list is read-only.
 */
export function OpenPointsList({ points, onToggle }: { points: OpenPointView[]; onToggle?: (id: string, checked: boolean) => void }) {
  return <View>
    {points.map((p) => <View key={p.id} testID={`open-point-${p.id}`} style={s.point}>
      <Text style={s.kind}>{p.kindLabel}</Text>
      <Text style={s.text}>{p.text}</Text>
      {!!p.quote && <View style={s.quoteBox}>
        <Text style={s.quote}>“{p.quote}”</Text>
        {!!p.page && <Text style={s.page}>{p.page}</Text>}
      </View>}
      {!p.quote && !!p.page && <Text style={s.page}>{p.page}</Text>}
      {!!p.why && <Text style={s.meta}>{p.why}</Text>}
      {!!p.used && <Text style={s.meta}>For now: {p.used}</Text>}
      {!!p.affects.length && <Text style={s.meta}>Concerns: {p.affects.join(", ")}</Text>}
      {onToggle && p.tickable && <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: p.checked }}
        onPress={() => onToggle(p.id, !p.checked)} style={s.tick} hitSlop={6}>
        <View style={[s.box, p.checked && s.boxOn]}>{p.checked && <Check size={13} color="#FFFFFF" strokeWidth={3} />}</View>
        <Text style={s.tickText}>{p.checked ? "Checked against the protocol" : "Mark as checked against the protocol"}</Text>
      </Pressable>}
    </View>)}
  </View>;
}

const s = StyleSheet.create({
  point: { paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: G.softLine, gap: 6 },
  kind: { color: G.amberText, fontFamily: fonts.semibold, fontSize: 12 },
  text: { color: G.ink, fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 },
  quoteBox: { borderLeftWidth: 3, borderLeftColor: G.line, paddingLeft: 10, gap: 3 },
  quote: { color: G.ink, fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, fontStyle: "italic" },
  page: { color: G.muted, fontFamily: fonts.medium, fontSize: 12 },
  meta: { color: G.muted, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17 },
  tick: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 4 },
  box: { width: 20, height: 20, borderRadius: 5, borderWidth: 1.5, borderColor: G.line, alignItems: "center", justifyContent: "center" },
  boxOn: { backgroundColor: G.accent, borderColor: G.accent },
  tickText: { color: G.ink, fontFamily: fonts.medium, fontSize: 13 },
});
