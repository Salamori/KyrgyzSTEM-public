"use client";

import Link from "next/link";

import { useI18n } from "@/lib/i18n/context";

// Inlined at build time: development builds always say their data is synthetic.
const IS_DEV = process.env.NODE_ENV !== "production";

export function SiteFooter() {
  const { t } = useI18n();
  return (
    <footer className="mt-16 border-t border-slate-200 bg-canvas">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-10 text-sm text-slate-500 sm:px-6">
        <p>{t("footer.note")}</p>
        <p className="flex flex-wrap gap-x-5 [&_a]:text-slate-600 [&_a]:no-underline [&_a:hover]:text-slate-900">
          <Link href="/about">{t("nav.about")}</Link>
          <Link href="/topics">{t("nav.topics")}</Link>
          <Link href="/learning-paths">{t("nav.learningPaths")}</Link>
          <Link href="/privacy">{t("nav.privacyPolicy")}</Link>
          <Link href="/terms">{t("nav.terms")}</Link>
          <Link href="/minors">{t("nav.minors")}</Link>
        </p>
        {IS_DEV && (
          <p role="note" className="font-medium text-amber-900">
            {t("footer.devData")}
          </p>
        )}
      </div>
    </footer>
  );
}
