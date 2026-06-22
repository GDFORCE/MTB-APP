import axios from 'axios';
import { Platform } from 'react-native';
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

export const api = axios.create({ baseURL: API_BASE });
api.interceptors.request.use(async (config) => {
  const t = await store.get('access_token');
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

export function wsUrl(token: string) {
  const u = BASE.replace(/^http/, 'ws');
  return `${u}/api/ws?token=${token}`;
}
