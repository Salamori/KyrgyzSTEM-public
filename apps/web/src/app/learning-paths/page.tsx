"use client";

import Link from "next/link";

import { EmptyState } from "@/components/states";
import { useI18n } from "@/lib/i18n/context";

// No learning-path endpoints exist yet (proposal 0002, milestone 4C): say so honestly.
export default function LearningPathsPage() {
  const { t } = useI18n();
  return (
    <section aria-labelledby="paths-title" className="flex flex-col gap-4">
      <h1 id="paths-title" className="text-2xl font-semibold">
        {t("paths.title")}
      </h1>
      <EmptyState>
        {t("paths.notYet")}{" "}
        <Link href="/topics">{t("paths.browseTopics")}</Link>
      </EmptyState>
    </section>
  );
}
