"use client";

import Link from "next/link";

import { useI18n } from "@/lib/i18n/context";

export default function NotFound() {
  const { t } = useI18n();
  return (
    <section className="card">
      <h1 className="text-xl font-semibold">{t("state.notFoundTitle")}</h1>
      <Link href="/" className="mt-3 inline-block">
        {t("nav.home")}
      </Link>
    </section>
  );
}
