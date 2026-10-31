"use client";

import Link from "next/link";

import { api, ApiError, unwrap, type ProblemDetail } from "@/lib/api/client";
import { LOCALE_NAMES, isLocale, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Markdown } from "../Markdown";
import { ErrorState, Loading } from "../states";
import { AssistantPanel } from "./AssistantPanel";
import { BandBadge, MyStatusBadge } from "./Badges";
import { EditorialPanel, HintsPanel, SelfReportPanel, SubmitPanel } from "./ProblemPanels";
import { ReviewPanel } from "./ReviewPanel";

export function ProblemView({ slug }: { slug: string }) {
  const { t, locale } = useI18n();
  const problem = useLoad<ProblemDetail>(
    () => unwrap(api.GET("/api/v1/problems/{slug}", { params: { path: { slug }, query: { locale } } })),
    [slug, locale],
  );

  if (problem.state.status === "loading") return <Loading />;
  if (problem.state.status === "error") {
    const notFound = problem.state.error instanceof ApiError && problem.state.error.status === 404;
    return (
      <div className="flex flex-col gap-3">
        <ErrorState
          message={notFound ? t("state.notFoundTitle") : t(errorKey(problem.state.error))}
          onRetry={notFound ? undefined : () => void problem.reload()}
        />
        <Link href="/problems">{t("problem.back")}</Link>
      </div>
    );
  }
  const p = problem.state.data;
  const servedIn = isLocale(p.locale) ? LOCALE_NAMES[p.locale] : p.locale;

  return (
    <article aria-labelledby="problem-title" className="flex flex-col gap-5">
      <Link href="/problems" className="text-sm">
        ← {t("problem.back")}
      </Link>

      {p.status !== "published" && (
        <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">
          {t("problem.notPublished", { status: t(`problemStatus.${p.status}` as MessageKey) })}
        </p>
      )}

      <header className="flex flex-col gap-2">
        <h1 id="problem-title" className="text-2xl font-bold sm:text-3xl">
          {p.title}
        </h1>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <BandBadge band={p.difficulty_band} status={p.difficulty_status} />
          <MyStatusBadge status={p.my_status} />
        </div>
        <details className="text-sm text-slate-700">
          <summary className="cursor-pointer">{t("problem.whyBand")}</summary>
          <p className="mt-1">{p.difficulty_rationale}</p>
          <p className="mt-1 text-slate-600">{t("bandStatus.explain")}</p>
        </details>
      </header>

      {p.is_fallback && (
        <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">
          {t("problem.fallbackNotice", { language: servedIn })}
        </p>
      )}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
        <dt className="font-medium text-slate-600">{t("problem.timeLimit")}</dt>
        <dd>{t("problem.seconds", { value: p.time_limit_ms / 1000 })}</dd>
        <dt className="font-medium text-slate-600">{t("problem.memoryLimit")}</dt>
        <dd>{t("problem.megabytes", { value: p.memory_limit_mb })}</dd>
        <dt className="font-medium text-slate-600">{t("problem.topics")}</dt>
        <dd className="col-span-1 sm:col-span-3">
          {p.topics.map((tp) => `${tp.name} (${t("problem.stage", { stage: tp.stage })})`).join(", ")}
        </dd>
        {p.tags.length > 0 && (
          <>
            <dt className="font-medium text-slate-600">{t("problem.tags")}</dt>
            <dd className="col-span-1 sm:col-span-3">{p.tags.map((tg) => tg.name).join(", ")}</dd>
          </>
        )}
      </dl>

      <section className="card flex flex-col gap-4" lang={p.locale}>
        <Markdown>{p.legend_md}</Markdown>
        <h2 className="text-lg font-semibold">{t("problem.input")}</h2>
        <Markdown>{p.input_md}</Markdown>
        <h2 className="text-lg font-semibold">{t("problem.output")}</h2>
        <Markdown>{p.output_md}</Markdown>
        {p.notes_md && (
          <>
            <h2 className="text-lg font-semibold">{t("problem.notes")}</h2>
            <Markdown>{p.notes_md}</Markdown>
          </>
        )}
      </section>

      <section aria-labelledby="examples-title" className="flex flex-col gap-3">
        <h2 id="examples-title" className="text-lg font-semibold">
          {t("problem.examples")}
        </h2>
        {p.samples.map((s, i) => (
          <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-sm font-medium text-slate-600">{t("problem.exampleInput")}</p>
              <pre className="sample">{s.input}</pre>
            </div>
            <div>
              <p className="mb-1 text-sm font-medium text-slate-600">{t("problem.exampleOutput")}</p>
              <pre className="sample">{s.output}</pre>
            </div>
          </div>
        ))}
      </section>

      {p.groups.length > 1 && (
        <section aria-labelledby="subtasks-title">
          <h2 id="subtasks-title" className="mb-2 text-lg font-semibold">
            {t("problem.subtasks")}
          </h2>
          <ol className="flex flex-col gap-1 text-sm">
            {p.groups.map((g) => (
              <li key={g.ordinal}>
                <span className="font-medium">{g.ordinal}.</span> {g.description_md} —{" "}
                {t("problem.points", { points: g.points })}
              </li>
            ))}
          </ol>
        </section>
      )}

      <HintsPanel slug={p.slug} total={p.hint_count} />
      <AssistantPanel problem={p} />
      <SubmitPanel problem={p} />
      <EditorialPanel problem={p} />
      <SelfReportPanel problem={p} />
      {p.review && <ReviewPanel problem={p} onChange={problem.setData} />}

      <footer className="border-t border-slate-200 pt-3 text-sm text-slate-600">
        <p>
          {t("problem.license")}: {p.license} · {t("problem.authors")}: {p.authors_md}
        </p>
        <p>{p.attribution_md}</p>
        {p.source && (
          <p>
            {t("problem.source", { title: p.source.title, author: p.source.author, license: p.source.license })}{" "}
            <a href={p.source.url} rel="noopener noreferrer nofollow" target="_blank" className="break-all">
              {p.source.url}
            </a>
          </p>
        )}
        {p.generated_with_ai && <p>{t("problem.aiNotice")}</p>}
      </footer>
    </article>
  );
}
