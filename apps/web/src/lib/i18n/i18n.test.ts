import { describe, expect, it } from "vitest";

import { en } from "./en";
import { formatDate, isLocale, translate } from "./index";
import { ky } from "./ky";
import { ru } from "./ru";

describe("dictionaries", () => {
  it.each([
    ["ru", ru],
    ["ky", ky],
  ])("%s has exactly the English keys, all non-empty", (_, dict) => {
    expect(Object.keys(dict).sort()).toEqual(Object.keys(en).sort());
    for (const value of Object.values(dict)) expect(value.trim()).not.toBe("");
  });

  it("keeps interpolation placeholders consistent across locales", () => {
    const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(ru[key]), `ru:${key}`).toEqual(placeholders(en[key]));
      expect(placeholders(ky[key]), `ky:${key}`).toEqual(placeholders(en[key]));
    }
  });
});

describe("translate", () => {
  it("interpolates variables and leaves unknown placeholders intact", () => {
    expect(translate("en", "admin.showing", { from: 1, to: 20, total: 42 })).toBe("1–20 of 42");
    expect(translate("en", "admin.showing", { from: 1 })).toBe("1–{to} of {total}");
  });

  it("returns the requested locale", () => {
    expect(translate("ru", "nav.signIn")).toBe("Войти");
    expect(translate("ky", "nav.signIn")).toBe("Кирүү");
  });
});

describe("isLocale", () => {
  it("accepts only supported locales", () => {
    expect(isLocale("ky")).toBe(true);
    expect(isLocale("de")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });
});

describe("formatDate", () => {
  it("never falls back to English for Kyrgyz", () => {
    const formatted = formatDate("ky", "2026-10-24T12:00:00Z");
    expect(formatted).not.toMatch(/October/);
    expect(formatted).toContain("2026");
  });
});
