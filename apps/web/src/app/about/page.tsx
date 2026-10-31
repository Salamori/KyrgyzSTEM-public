"use client";

import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";

const SECTIONS = ["mission", "practice", "difficulty", "content", "privacy", "status", "contribute"] as const;

export default function AboutPage() {
  const { t } = useI18n();
  return (
    <article aria-labelledby="about-title" className="flex max-w-3xl flex-col gap-10 py-6 sm:py-12">
      <h1 id="about-title" className="text-4xl font-bold sm:text-5xl">
        {t("about.title")}
      </h1>
      {SECTIONS.map((s) => (
        <section key={s} aria-labelledby={`about-${s}`} className="flex flex-col gap-3 border-t border-slate-200 pt-8">
          <h2 id={`about-${s}`} className="text-2xl font-semibold tracking-tight">
            {t(`about.${s}Title` as MessageKey)}
          </h2>
          <p className="text-lg leading-relaxed text-slate-600">{t(`about.${s}Body` as MessageKey)}</p>
        </section>
      ))}
    </article>
  );
}
