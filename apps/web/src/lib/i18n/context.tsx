"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { LOCALE_COOKIE, translate, type Locale, type MessageKey } from "./index";

type TranslateFn = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: TranslateFn;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ initialLocale, children }: { initialLocale: Locale; children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.lang = next;
  }, []);

  const value = useMemo<I18nValue>(
    () => ({ locale, setLocale, t: (key, vars) => translate(locale, key, vars) }),
    [locale, setLocale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}
