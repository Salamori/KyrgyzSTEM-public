"use client";

import { useId } from "react";

import { api, unwrap } from "@/lib/api/client";
import { LOCALES, LOCALE_NAMES, isLocale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();
  const { state, setUser } = useSession();
  const id = useId();

  async function onChange(value: string) {
    if (!isLocale(value)) return;
    setLocale(value);
    if (state.status === "authenticated") {
      // Best effort: the interface already switched; persisting the preference can fail quietly.
      try {
        setUser(await unwrap(api.PATCH("/api/v1/users/me", { body: { preferred_locale: value } })));
      } catch {}
    }
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="sr-only">
        {t("language.label")}
      </label>
      <select
        id={id}
        value={locale}
        onChange={(e) => void onChange(e.target.value)}
        className="rounded-md border border-slate-300 bg-surface px-2 py-1 text-sm"
      >
        {LOCALES.map((l) => (
          <option key={l} value={l} lang={l}>
            {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
    </div>
  );
}
