import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { Platform } from 'react-native';
import { router } from 'expo-router';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || '';
export const API_BASE = `${BASE}/api`;

const isWeb = Platform.OS === 'web';
const store = {
  get: (k: string) => isWeb ? AsyncStorage.getItem(k) : SecureStore.getItemAsync(k),
  set: (k: string, v: string) => isWeb ? AsyncStorage.setItem(k, v) : SecureStore.setItemAsync(k, v),
  del: (k: string) => isWeb ? AsyncStorage.removeItem(k) : SecureStore.deleteItemAsync(k),
};
export const tokenStore = store;

// Axios's default export is the documented factory object; the similarly named
// type-level export makes the generic lint rule flag this valid usage.
// eslint-disable-next-line import/no-named-as-default-member
export const api = axios.create({ baseURL: API_BASE });
api.interceptors.request.use(async (config) => {
  const t = await store.get('access_token');
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

// ── 401 → refresh-once, then retry (single-flight across concurrent requests) ─
// AuthContext registers a handler so React state (user) is cleared on hard expiry.
let onSessionExpired: (() => void) | null = null;
export function setSessionExpiredHandler(fn: (() => void) | null) { onSessionExpired = fn; }

let refreshPromise: Promise<string | null> | null = null;
function refreshAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const rt = await store.get('refresh_token');
        if (!rt) return null;
        // Bare axios (not `api`) so this call bypasses both interceptors.
        const r = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: rt });
        const t: string | undefined = r.data?.access_token;
        if (!t) return null;
        await store.set('access_token', t);
        if (r.data?.refresh_token) await store.set('refresh_token', r.data.refresh_token);
        return t;
      } catch {
        return null;
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
    const hadSession = !!(await store.get('refresh_token'));
    const token = await refreshAccessToken();
    if (token) {
      original.headers.Authorization = `Bearer ${token}`;
      return api(original);
    }
    // Refresh failed → hard sign-out; only route to session-timeout if a session existed.
    await store.del('access_token');
    await store.del('refresh_token');
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
