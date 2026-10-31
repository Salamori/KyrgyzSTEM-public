"use client";

import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";

export interface LegalSection {
  id: string;
  /** Marks a section with a decision that only the owner and a lawyer can make. */
  needsDecision?: boolean;
}

/** A draft legal document. The banner stays until a lawyer has reviewed the text. */
export function LegalPage({ doc, sections }: { doc: string; sections: LegalSection[] }) {
  const { t } = useI18n();
  const key = (suffix: string) => `legal.${doc}.${suffix}` as MessageKey;
  return (
    <article aria-labelledby="legal-title" className="flex max-w-3xl flex-col gap-8 py-6 sm:py-12">
      <h1 id="legal-title" className="text-4xl font-bold sm:text-5xl">
        {t(key("title"))}
      </h1>
      <p role="note" className="rounded-md bg-amber-100 p-4 font-medium text-amber-900">
        {t("legal.draftBanner")}
      </p>
      {sections.map(({ id, needsDecision }) => (
        <section key={id} aria-labelledby={`legal-${id}`} className="flex flex-col gap-3 border-t border-slate-200 pt-6">
          <h2 id={`legal-${id}`} className="text-2xl font-semibold tracking-tight">
            {t(key(`${id}Title`))}
          </h2>
          <p className="text-lg leading-relaxed text-slate-600">{t(key(`${id}Body`))}</p>
          {needsDecision && (
            <p className="rounded-md bg-amber-100 p-3 text-amber-900">{t(key(`${id}Decision`))}</p>
          )}
        </section>
      ))}
    </article>
  );
}
