"use client";

import Link from "next/link";

import { api, unwrap, type TopicOut } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { EmptyState, ErrorState, Loading } from "../states";

/** Topic navigation: the curriculum with its (never blocking) prerequisites. */
export function TopicList() {
  const { t, locale } = useI18n();
  const topics = useLoad<TopicOut[]>(
    () => unwrap(api.GET("/api/v1/topics", { params: { query: { locale } } })),
    [locale],
  );

  return (
    <section aria-labelledby="topics-title" className="flex flex-col gap-4">
      <h1 id="topics-title" className="text-2xl font-semibold">
        {t("topics.title")}
      </h1>
      <p className="text-slate-700">{t("topics.lead")}</p>
      {topics.state.status === "loading" && <Loading />}
      {topics.state.status === "error" && (
        <ErrorState message={t(errorKey(topics.state.error))} onRetry={() => void topics.reload()} />
      )}
      {topics.state.status === "ready" && topics.state.data.length === 0 && (
        <EmptyState>{t("topics.empty")}</EmptyState>
      )}
      {topics.state.status === "ready" && topics.state.data.length > 0 && (
        <TopicCards topics={topics.state.data} />
      )}
    </section>
  );
}

function TopicCards({ topics }: { topics: TopicOut[] }) {
  const { t } = useI18n();
  const names = new Map(topics.map((tp) => [tp.slug, tp.name]));
  const describe = (strength: "required" | "helpful", topic: TopicOut) =>
    [
      ...new Set(
        topic.prerequisites
          .filter((p) => p.strength === strength && p.requires !== topic.slug)
          .map((p) => `${names.get(p.requires) ?? p.requires} (${t("problem.stage", { stage: p.requires_stage })})`),
      ),
    ].join(", ");

  return (
    <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {topics.map((topic) => {
        const required = describe("required", topic);
        const helpful = describe("helpful", topic);
        return (
          <li key={topic.slug} className="card flex flex-col gap-2">
            <h2 className="text-lg font-semibold">{topic.name}</h2>
            {topic.summary_md && <p className="text-sm text-slate-700">{topic.summary_md}</p>}
            <p className="text-sm text-slate-600">{t("topics.stages", { n: topic.stage_count })}</p>
            <p className="text-sm">
              {required ? t("topics.required", { list: required }) : t("topics.noPrereq")}
            </p>
            {helpful && <p className="text-sm text-slate-600">{t("topics.helpful", { list: helpful })}</p>}
            <Link href={`/problems?topic=${encodeURIComponent(topic.slug)}`} className="mt-auto text-sm font-medium">
              {t("topics.problems")} →
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
