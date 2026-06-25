"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
} from "react";

export type Theme = "light";

interface ThemeContextValue {
  theme: Theme;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyTheme() {
  document.documentElement.setAttribute("data-theme", "light");
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    applyTheme();
    localStorage.setItem("zo-theme", "light");
  }, []);

  const value = useMemo(() => ({ theme: "light" as const }), []);

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
