"use client";

import Link from "next/link";

import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

const HOW: MessageKey[] = ["home.howPractice", "home.howEvidence", "home.howOpen"];

export default function HomePage() {
  const { t } = useI18n();
  const { state } = useSession();

  return (
    <div className="flex flex-col gap-24 sm:gap-32">
      <section aria-labelledby="home-title" className="relative isolate flex flex-col items-start gap-6 pt-12 sm:pt-24">
        <div aria-hidden className="hero-backdrop" />
        <h1 id="home-title" className="max-w-4xl pb-1 !text-5xl font-semibold sm:!text-7xl">
          {t("home.title")}
        </h1>
        <p className="max-w-2xl text-lg leading-relaxed text-slate-600 sm:text-xl">{t("home.lead")}</p>
        <div className="mt-2 flex flex-wrap items-center gap-6 text-sm">
          {state.status === "authenticated" ? (
            <Link href="/dashboard" className="btn-primary inline-block px-5 py-2.5">
              {t("home.ctaDashboard")}
            </Link>
          ) : (
            <Link href="/register" className="btn-primary inline-block px-5 py-2.5">
              {t("home.ctaRegister")}
            </Link>
          )}
          <Link href="/problems">{t("problems.title")} →</Link>
        </div>
      </section>

      <section aria-labelledby="how-title" className="flex flex-col gap-8">
        <h2 id="how-title" className="bg-gradient-to-b from-white to-[#9CA3AF] bg-clip-text text-3xl font-semibold tracking-tight text-transparent sm:text-4xl">
          {t("home.howTitle")}
        </h2>
        <ul className="bento">
          {HOW.map((key) => (
            <li key={key} className="card flex items-end leading-relaxed text-slate-700">
              {t(key)}
            </li>
          ))}
        </ul>
        <p>
          <Link href="/about">{t("home.learnMore")} →</Link>
        </p>
      </section>

      <p role="note" className="-mt-12 rounded-lg border border-slate-200 bg-surface p-4 text-sm text-slate-600">
        {t("home.status")}
      </p>
    </div>
  );
}
