import { Redirect } from "expo-router";
import { useAuth } from "@/src/auth/AuthContext";
import { View, ActivityIndicator } from "react-native";
import { colors } from "@/src/theme/tokens";

export default function Index() {
  const { user, loading } = useAuth();
  if (loading) return <View style={{ flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.background }}><ActivityIndicator color={colors.primary} /></View>;
  if (!user) return <Redirect href="/(auth)/welcome" />;
  const r = user.role;
  if (r === "patient") return <Redirect href="/(app)/patient/dashboard" />;
  if (r === "pi" || r === "site") return <Redirect href="/(app)/pi/dashboard" />;
  if (r === "crc") return <Redirect href="/(app)/crc/dashboard" />;
  return <Redirect href="/(app)/sponsor/dashboard" />;
}
