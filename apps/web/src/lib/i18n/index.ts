import { en, type Dictionary, type MessageKey } from "./en";
import { ky } from "./ky";
import { ru } from "./ru";

export const LOCALES = ["ky", "ru", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "ru";
export const LOCALE_COOKIE = "ks_locale";

export const LOCALE_NAMES: Record<Locale, string> = { ky: "Кыргызча", ru: "Русский", en: "English" };

const dictionaries: Record<Locale, Dictionary> = { en, ru, ky };

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function translate(
  locale: Locale,
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  const template = dictionaries[locale][key] ?? en[key];
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/** Format a date; runtimes without Kyrgyz locale data fall back to Russian, not English. */
export function formatDate(locale: Locale, iso: string): string {
  const [supported] = Intl.DateTimeFormat.supportedLocalesOf([locale, "ru"]);
  return new Intl.DateTimeFormat(supported ?? "ru", { dateStyle: "long" }).format(new Date(iso));
}

export type { MessageKey };

/** Date and time, for scheduled sessions. */
export function formatDateTime(locale: Locale, iso: string): string {
  const [supported] = Intl.DateTimeFormat.supportedLocalesOf([locale, "ru"]);
  return new Intl.DateTimeFormat(supported ?? "ru", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}
