"use client";

import Link from "next/link";
import { useState } from "react";

import { api, ApiError, unwrap, type SubmissionDetail, type SubmissionPage, type SubmissionSummary } from "@/lib/api/client";
import { formatDate, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";
import { useSubmissionStatus } from "@/lib/useSubmissionStatus";

import { errorKey } from "../errorMessage";
import { EmptyState, ErrorState, Loading, Unauthorized } from "../states";

const PAGE_SIZE = 20;

export function Outcome({ s }: { s: Pick<SubmissionSummary, "state" | "verdict" | "score"> }) {
  const { t } = useI18n();
  if (s.state === "done" && s.verdict) {
    const ok = s.verdict === "accepted";
    return (
      <span className={`badge ${ok ? "bg-emerald-600 text-white" : "bg-rose-100 text-rose-900"}`}>
        {t(`verdict.${s.verdict}` as MessageKey)}
        {s.score !== null && !ok ? ` · ${s.score}` : ""}
      </span>
    );
  }
  return <span className="badge bg-slate-200 text-slate-800">{t(`state.${s.state}` as MessageKey)}</span>;
}

export function SubmissionHistory() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <HistoryList />;
}

function HistoryList() {
  const { t, locale } = useI18n();
  const [offset, setOffset] = useState(0);
  const page = useLoad<SubmissionPage>(
    () => unwrap(api.GET("/api/v1/submissions", { params: { query: { locale, limit: PAGE_SIZE, offset } } })),
    [locale, offset],
  );

  return (
    <section aria-labelledby="subs-title" className="flex flex-col gap-4">
      <h1 id="subs-title" className="text-2xl font-semibold">
        {t("submissions.title")}
      </h1>
      {page.state.status === "loading" && <Loading />}
      {page.state.status === "error" && (
        <ErrorState message={t(errorKey(page.state.error))} onRetry={() => void page.reload()} />
      )}
      {page.state.status === "ready" && page.state.data.items.length === 0 && (
        <EmptyState>
          {t("submissions.empty")} <Link href="/problems">{t("nav.problems")}</Link>
        </EmptyState>
      )}
      {page.state.status === "ready" && page.state.data.items.length > 0 && (
        <>
          <ul className="flex flex-col gap-2">
            {page.state.data.items.map((s) => (
              <li key={s.id} className="card flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <Link href={`/submissions/${s.id}`} className="font-medium">
                    {s.problem_title}
                  </Link>
                  <p className="text-sm text-slate-600">
                    {t(`lang.${s.language}` as MessageKey)} · {formatDate(locale, s.created_at)} ·{" "}
                    {t("submissions.hintsUsed", { n: s.hints_revealed_count })}
                  </p>
                </div>
                <Outcome s={s} />
              </li>
            ))}
          </ul>
          <nav aria-label={t("admin.pagination")} className="flex items-center justify-between gap-3 text-sm">
            <button type="button" className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              {t("admin.prev")}
            </button>
            <span>
              {t("admin.showing", { from: offset + 1, to: offset + page.state.data.items.length, total: page.state.data.total })}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={offset + page.state.data.items.length >= page.state.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              {t("admin.next")}
            </button>
          </nav>
        </>
      )}
    </section>
  );
}

export function SubmissionView({ id }: { id: string }) {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <SubmissionBody id={id} />;
}

/**
 * What the student sees about a submission's checking, updated while it is queued or being
 * checked (`useSubmissionStatus`). Never shows compiler output, tests or internal errors.
 */
export function CheckingStatus({ live }: { live: ReturnType<typeof useSubmissionStatus> }) {
  const { t } = useI18n();
  const s = live.submission;
  if (s === null) return null;
  return (
    <div aria-live="polite" className="flex flex-col gap-2 text-sm">
      {live.polling && <p>{t("submit.checking")}</p>}
      {s.state === "not_judged" && <p className="rounded-md bg-slate-100 p-3">{t("submit.noJudge")}</p>}
      {s.state === "judge_error" && <p className="rounded-md bg-amber-50 p-3 text-amber-950">{t("submissions.judgeError")}</p>}
      {live.stopped === "gave_up" && (
        <p className="flex flex-wrap items-center gap-2">
          {t("submissions.stillChecking")}
          <button type="button" className="btn-secondary" onClick={live.retry}>
            {t("submissions.checkAgain")}
          </button>
        </p>
      )}
      {live.stopped === "expired" && <p role="alert">{t("errors.not_authenticated")}</p>}
      {live.stopped === "failed" && <p role="alert">{t("errors.not_found")}</p>}
    </div>
  );
}

function SubmissionBody({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const sub = useLoad<SubmissionDetail>(
    () => unwrap(api.GET("/api/v1/submissions/{submission_id}", { params: { path: { submission_id: id }, query: { locale } } })),
    [id, locale],
  );
  const live = useSubmissionStatus(sub.state.status === "ready" ? sub.state.data : null, locale);
  if (sub.state.status === "loading") return <Loading />;
  if (sub.state.status === "error") {
    const err = sub.state.error;
    const missing = err instanceof ApiError && (err.status === 404 || err.status === 422);
    return <ErrorState message={missing ? t("errors.not_found") : t(errorKey(err))} />;
  }
  const s = live.submission ?? sub.state.data;
  return (
    <article aria-labelledby="sub-title" className="flex flex-col gap-4">
      <Link href="/submissions" className="text-sm">
        ← {t("submissions.title")}
      </Link>
      <h1 id="sub-title" className="text-2xl font-semibold">
        {t("submissions.detailTitle")}: <Link href={`/problems/${s.problem_slug}`}>{s.problem_title}</Link>
      </h1>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Outcome s={s} />
        <span>{t(`lang.${s.language}` as MessageKey)}</span>
        <span>
          {t("submissions.when")}: {formatDate(locale, s.created_at)}
        </span>
        <span>{t("submissions.hintsUsed", { n: s.hints_revealed_count })}</span>
      </div>
      <CheckingStatus live={live} />
      {s.group_results.length > 0 && (
        <ul className="text-sm">
          {s.group_results.map((g) => (
            <li key={g.ordinal}>
              {t("problem.subtasks")} {g.ordinal}: {t(`verdict.${g.verdict}` as MessageKey)} — {g.score}/{g.points}
            </li>
          ))}
        </ul>
      )}
      <pre className="sample max-h-[32rem]">{s.source_code}</pre>
    </article>
  );
}
