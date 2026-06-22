import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { LogBox, StatusBar } from "react-native";
import { useIconFonts } from "@/src/hooks/use-icon-fonts";
import { AuthProvider, useAuth } from "@/src/auth/AuthContext";
import { colors } from "@/src/theme/tokens";
import "@/src/i18n";

LogBox.ignoreAllLogs(true);
SplashScreen.preventAutoHideAsync();

function RouterGuard() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const segments = useSegments();
  useEffect(() => {
    if (loading) return;
    const inAuth = segments[0] === "(auth)" || (segments[0] as any) === undefined || segments[0] === "index";
    if (!user && !inAuth) router.replace("/(auth)/welcome");
    else if (user) {
      // route to the role dashboard if currently on auth screens
      if (inAuth || segments.length === 0) {
        const role = user.role;
        if (role === "patient") router.replace("/(app)/patient/dashboard");
        else if (role === "pi" || role === "site") router.replace("/(app)/pi/dashboard");
        else if (role === "crc") router.replace("/(app)/crc/dashboard");
        else router.replace("/(app)/sponsor/dashboard");
      }
    }
  }, [user, loading, segments]);
  return null;
}

export default function RootLayout() {
  const [loaded, error] = useIconFonts();
  useEffect(() => { if (loaded || error) SplashScreen.hideAsync(); }, [loaded, error]);
  if (!loaded && !error) return null;
  return (
    <AuthProvider>
      <StatusBar barStyle="dark-content" backgroundColor={colors.background} />
      <RouterGuard />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }} />
    </AuthProvider>
  );
}
