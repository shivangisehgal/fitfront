import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const [dark, setDark] = useState(() => {
    // Check localStorage first, then system preference
    const stored = localStorage.getItem('scheduler_ai_theme');
    if (stored) return stored === 'dark';
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });

  // Sync class on mount and whenever dark changes
  useEffect(() => {
    const root = document.documentElement;
    if (dark) {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('scheduler_ai_theme', dark ? 'dark' : 'light');
  }, [dark]);

  // Also apply the class synchronously on first render (before paint)
  // to avoid a flash of wrong theme
  if (typeof document !== 'undefined') {
    const root = document.documentElement;
    if (dark && !root.classList.contains('dark')) {
      root.classList.add('dark');
    } else if (!dark && root.classList.contains('dark')) {
      root.classList.remove('dark');
    }
  }

  const toggle = useCallback(() => {
    setDark((d) => {
      const next = !d;
      // Synchronously toggle class for instant visual feedback
      const root = document.documentElement;
      if (next) {
        root.classList.add('dark');
      } else {
        root.classList.remove('dark');
      }
      localStorage.setItem('scheduler_ai_theme', next ? 'dark' : 'light');
      return next;
    });
  }, []);

  return (
    <ThemeContext.Provider value={{ dark, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>');
  return ctx;
}
