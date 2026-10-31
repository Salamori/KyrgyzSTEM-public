"use client";

import Link from "next/link";

import { api, unwrap, type ProgressOut, type SubmissionPage } from "@/lib/api/client";
import { formatDate, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { EmptyState, ErrorState, Loading, Unauthorized } from "../states";
import { Outcome } from "./Submissions";

export function Dashboard() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <DashboardBody name={state.user.display_name} />;
}

function Card({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="card flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function DashboardBody({ name }: { name: string }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-2xl font-semibold">{t("dashboard.title", { name })}</h1>
      <Card id="dash-path" title={t("dashboard.pathTitle")}>
        {/* Learning paths need proposal 0002 (milestone 4C); no endpoint exists yet. */}
        <p className="text-slate-700">{t("dashboard.pathNone")}</p>
        <p>
          <Link href="/topics">{t("paths.browseTopics")} →</Link>
        </p>
      </Card>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <RecentAttempts />
        <TopicProgress />
      </div>
    </div>
  );
}

function RecentAttempts() {
  const { t, locale } = useI18n();
  const recent = useLoad<SubmissionPage>(
    () => unwrap(api.GET("/api/v1/submissions", { params: { query: { locale, limit: 5, offset: 0 } } })),
    [locale],
  );
  return (
    <Card id="dash-recent" title={t("dashboard.recentTitle")}>
      {recent.state.status === "loading" && <Loading />}
      {recent.state.status === "error" && (
        <ErrorState message={t(errorKey(recent.state.error))} onRetry={() => void recent.reload()} />
      )}
      {recent.state.status === "ready" && recent.state.data.items.length === 0 && (
        <EmptyState>
          {t("submissions.empty")} <Link href="/problems">{t("dashboard.findProblem")}</Link>
        </EmptyState>
      )}
      {recent.state.status === "ready" && recent.state.data.items.length > 0 && (
        <>
          <ul className="flex flex-col divide-y divide-slate-200">
            {recent.state.data.items.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <Link href={`/submissions/${s.id}`} className="font-medium">
                    {s.problem_title}
                  </Link>
                  <p className="text-xs text-slate-600">
                    {t(`lang.${s.language}` as MessageKey)} · {formatDate(locale, s.created_at)}
                  </p>
                </div>
                <Outcome s={s} />
              </li>
            ))}
          </ul>
          <p className="text-sm">
            <Link href="/submissions">{t("dashboard.recentAll")} →</Link>
          </p>
        </>
      )}
    </Card>
  );
}

function TopicProgress() {
  const { t, locale } = useI18n();
  const progress = useLoad<ProgressOut>(
    () => unwrap(api.GET("/api/v1/me/progress", { params: { query: { locale } } })),
    [locale],
  );
  return (
    <Card id="dash-progress" title={t("dashboard.progressTitle")}>
      {progress.state.status === "loading" && <Loading />}
      {progress.state.status === "error" && (
        <ErrorState message={t(errorKey(progress.state.error))} onRetry={() => void progress.reload()} />
      )}
      {progress.state.status === "ready" && <ProgressSummary data={progress.state.data} />}
    </Card>
  );
}

function ProgressSummary({ data }: { data: ProgressOut }) {
  const { t } = useI18n();
  // Only topics with judged evidence; never self-reports or unchecked solutions.
  const active = data.topics
    .map((tp) => ({
      ...tp,
      demonstrated: tp.stages.filter((s) => s.demonstrated).length,
      evidence: tp.stages.some((s) => s.with_hints > 0 || Object.keys(s.unaided_by_band).length > 0),
    }))
    .filter((tp) => tp.evidence);
  return (
    <>
      {data.pending_not_judged > 0 && (
        <p className="text-sm text-slate-700">{t("dashboard.pending", { n: data.pending_not_judged })}</p>
      )}
      {active.length === 0 ? (
        <EmptyState>{t("dashboard.progressEmpty")}</EmptyState>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {active.map((tp) => (
            <li key={tp.slug} className="flex justify-between gap-3">
              <span>{tp.name}</span>
              <span className="text-slate-700">{t("dashboard.stagesDemonstrated", { n: tp.demonstrated })}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-sm">
        <Link href="/progress">{t("dashboard.progressFull")} →</Link>
      </p>
    </>
  );
}
