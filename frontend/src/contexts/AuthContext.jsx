import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { apiFetch, setToken, clearToken, getToken } from '../lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  // Holds the server message when the account is suspended/deactivated (403).
  // The UI can check this to show a "your account has been disabled" page
  // instead of silently logging the user out.
  const [accountError, setAccountError] = useState(null);

  // Fetch current user from /api/auth/me on mount if token present
  useEffect(() => {
    let cancelled = false;
    async function loadUser() {
      const token = getToken();
      if (!token) {
        setLoading(false);
        return;
      }
      try {
        const me = await apiFetch('/api/auth/me');
        if (!cancelled) setUser(me);
      } catch (err) {
        if (!cancelled) {
          if (err.status === 403) {
            // Account suspended/deactivated — keep token so user can see
            // a friendly message, but clear the user object.
            setUser(null);
            setAccountError(
              err.message || 'Your account has been deactivated. Contact support.'
            );
          } else {
            // 401 or network error — bad/expired token
            clearToken();
            setUser(null);
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadUser();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email, password) => {
    const res = await apiFetch('/api/auth/login', {
      method: 'POST',
      body: { email, password },
    });
    setToken(res.access_token);
    setUser(res.user);
    setAccountError(null);
    return res.user;
  }, []);

  const register = useCallback(async (payload) => {
    const res = await apiFetch('/api/auth/register', {
      method: 'POST',
      body: payload,
    });
    setToken(res.access_token);
    setUser(res.user);
    setAccountError(null);
    return res.user;
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    setAccountError(null);
    // Chat sessionStorage keys are scoped per-user (scheduler_ai_chat_{userId}_*)
    // so no cross-account leakage — no need to clear on logout.
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const me = await apiFetch('/api/auth/me');
      setUser(me);
      setAccountError(null);
      return me;
    } catch (err) {
      if (err.status === 403) {
        setUser(null);
        setAccountError(
          err.message || 'Your account has been deactivated. Contact support.'
        );
      }
      return null;
    }
  }, []);

  const value = {
    user,
    loading,
    isAuthenticated: !!user,
    isAdmin: !!user?.is_admin,
    isPending: user?.status === 'PENDING',
    isActive: user?.status === 'ACTIVE',
    accountError,
    login,
    register,
    logout,
    refreshUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
