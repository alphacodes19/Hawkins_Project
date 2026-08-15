"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import type { User } from "@/lib/types";
import { authApi } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  justLoggedIn: boolean;
  clearJustLoggedIn: () => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  // Set true only by an explicit login() call below -- never by refresh()
  // picking up an already-existing session (e.g. on a page reload). That
  // distinction is what makes the welcome toast show once, right after
  // actually signing in, and not on every subsequent page load.
  const [justLoggedIn, setJustLoggedIn] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const me = await authApi.login(username, password);
    setUser(me);
    setJustLoggedIn(true);
  }, []);

  const clearJustLoggedIn = useCallback(() => setJustLoggedIn(false), []);

  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
    setJustLoggedIn(false);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, justLoggedIn, clearJustLoggedIn, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
