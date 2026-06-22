import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api, tokenStore } from '../api/client';

export type Role = 'sponsor' | 'cro' | 'smo' | 'site' | 'pi' | 'crc' | 'patient';
export type User = { id: string; email: string; full_name: string; role: Role; phone?: string; organization?: string; avatar_initials?: string };

interface Ctx {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signUp: (data: any) => Promise<User>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}
const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const t = await tokenStore.get('access_token');
      if (!t) { setUser(null); return; }
      const r = await api.get('/auth/me');
      setUser(r.data);
    } catch { setUser(null); await tokenStore.del('access_token'); }
  }, []);

  useEffect(() => { (async () => { await refresh(); setLoading(false); })(); }, [refresh]);

  const signIn = async (email: string, password: string) => {
    const r = await api.post('/auth/login', { email, password });
    await tokenStore.set('access_token', r.data.access_token);
    await tokenStore.set('refresh_token', r.data.refresh_token);
    setUser(r.data.user);
    return r.data.user;
  };
  const signUp = async (data: any) => {
    const r = await api.post('/auth/register', data);
    await tokenStore.set('access_token', r.data.access_token);
    await tokenStore.set('refresh_token', r.data.refresh_token);
    setUser(r.data.user);
    return r.data.user;
  };
  const signOut = async () => {
    await tokenStore.del('access_token'); await tokenStore.del('refresh_token'); setUser(null);
  };

  return <AuthCtx.Provider value={{ user, loading, signIn, signUp, signOut, refresh }}>{children}</AuthCtx.Provider>;
}
