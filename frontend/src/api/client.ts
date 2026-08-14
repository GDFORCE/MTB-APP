import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { Platform } from 'react-native';
import { router } from 'expo-router';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || '';
if (!__DEV__ && BASE && !BASE.startsWith('https://')) {
  throw new Error('EXPO_PUBLIC_BACKEND_URL must use HTTPS in production builds.');
}
export const API_BASE = `${BASE}/api`;

const isWeb = Platform.OS === 'web';
let biometricSupport: boolean | null = null;

function nativeSecureOptions(key: string): SecureStore.SecureStoreOptions {
  if (biometricSupport === null) {
    biometricSupport = SecureStore.canUseBiometricAuthentication();
  }
  const canGateWithBiometrics = biometricSupport === true;
  return {
    keychainService: 'mtb-auth-session',
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    // Expo Go cannot create biometric-gated keys reliably. Release builds use
    // biometric gating for the long-lived credential; access tokens remain
    // available while the unlocked app is making ordinary API requests.
    requireAuthentication: key === 'refresh_token' && !__DEV__ && canGateWithBiometrics,
    authenticationPrompt: 'Unlock My Trial Board',
  };
}

const persistentStore = {
  get: async (k: string) => isWeb
    ? AsyncStorage.getItem(k)
    : SecureStore.getItemAsync(k, nativeSecureOptions(k)),
  set: async (k: string, v: string) => isWeb
    ? AsyncStorage.setItem(k, v)
    : SecureStore.setItemAsync(k, v, nativeSecureOptions(k)),
  del: async (k: string) => isWeb
    ? AsyncStorage.removeItem(k)
    : SecureStore.deleteItemAsync(k, nativeSecureOptions(k)),
};

// A non-remembered login remains usable for the lifetime of the current app
// process, but its tokens are never left in device storage for the next launch.
const memoryTokens = new Map<string, string>();
let persistSession = false;
let persistenceLoaded = false;

async function loadPersistencePreference() {
  if (persistenceLoaded) return;
  persistSession = (await persistentStore.get('remember_session')) === '1';
  persistenceLoaded = true;
}

export const tokenStore = {
  async get(k: string) {
    const inMemory = memoryTokens.get(k);
    return inMemory !== undefined ? inMemory : persistentStore.get(k);
  },
  async set(k: string, v: string) {
    await loadPersistencePreference();
    memoryTokens.set(k, v);
    if (persistSession) await persistentStore.set(k, v);
    else await persistentStore.del(k);
  },
  async del(k: string) {
    memoryTokens.delete(k);
    await persistentStore.del(k);
  },
  async setPersistence(remember: boolean) {
    persistSession = remember;
    persistenceLoaded = true;
    if (remember) await persistentStore.set('remember_session', '1');
    else await persistentStore.del('remember_session');
    if (!remember) {
      memoryTokens.delete('access_token');
      memoryTokens.delete('refresh_token');
      await Promise.all([
        persistentStore.del('access_token'),
        persistentStore.del('refresh_token'),
      ]);
    }
  },
};

// Axios's default export is the documented factory object; the similarly named
// type-level export makes the generic lint rule flag this valid usage.
// eslint-disable-next-line import/no-named-as-default-member
export const api = axios.create({ baseURL: API_BASE });
api.interceptors.request.use(async (config) => {
  const t = await tokenStore.get('access_token');
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

// ── 401 → refresh-once, then retry (single-flight across concurrent requests) ─
// AuthContext registers a handler so React state (user) is cleared on hard expiry.
let onSessionExpired: (() => void) | null = null;
export function setSessionExpiredHandler(fn: (() => void) | null) { onSessionExpired = fn; }

let refreshPromise: Promise<string | null> | null = null;
function refreshAccessToken(providedRefreshToken?: string | null): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const rt = providedRefreshToken === undefined
          ? await tokenStore.get('refresh_token')
          : providedRefreshToken;
        if (!rt) return null;
        // Bare axios (not `api`) so this call bypasses both interceptors.
        const r = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: rt });
        const t: string | undefined = r.data?.access_token;
        if (!t) return null;
        await tokenStore.set('access_token', t);
        if (r.data?.refresh_token) await tokenStore.set('refresh_token', r.data.refresh_token);
        return t;
      } catch (error: any) {
        const status = error?.response?.status;
        if (status === 400 || status === 401 || status === 403) return null;
        throw error;
      }
    })().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

type RetriableConfig = InternalAxiosRequestConfig & { _retry?: boolean };

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined;
    const url = original?.url || '';
    // Never refresh-retry auth endpoints themselves (login/register/refresh/otp…);
    // /auth/me is the one auth route that legitimately runs on an access token.
    const isAuthRoute = url.includes('/auth/') && !url.includes('/auth/me');
    if (error.response?.status !== 401 || !original || original._retry || isAuthRoute) throw error;

    original._retry = true;
    const refreshToken = await tokenStore.get('refresh_token');
    const hadSession = !!refreshToken;
    const token = await refreshAccessToken(refreshToken);
    if (token) {
      original.headers.Authorization = `Bearer ${token}`;
      return api(original);
    }
    // Refresh failed → hard sign-out; only route to session-timeout if a session existed.
    await tokenStore.setPersistence(false);
    if (hadSession) {
      onSessionExpired?.();
      router.replace('/session-timeout');
    }
    throw error;
  },
);

export function wsUrl(token: string) {
  const u = BASE.replace(/^http/, 'ws');
  return `${u}/api/ws?token=${token}`;
}
