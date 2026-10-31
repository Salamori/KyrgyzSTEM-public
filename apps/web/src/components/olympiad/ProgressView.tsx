"use client";

import Link from "next/link";

import { api, unwrap, type Band, type ProgressOut } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { ErrorState, Loading, Unauthorized } from "../states";

export function ProgressView() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <ProgressBody />;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card flex flex-col gap-1">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="text-xl font-semibold">{value}</dd>
    </div>
  );
}

function ProgressBody() {
  const { t, locale } = useI18n();
  const progress = useLoad<ProgressOut>(
    () => unwrap(api.GET("/api/v1/me/progress", { params: { query: { locale } } })),
    [locale],
  );
  if (progress.state.status === "loading") return <Loading />;
  if (progress.state.status === "error") {
    return <ErrorState message={t(errorKey(progress.state.error))} onRetry={() => void progress.reload()} />;
  }
  const p = progress.state.data;
  const none = t("progress.notEnoughData");

  return (
    <section aria-labelledby="progress-title" className="flex flex-col gap-5">
      <h1 id="progress-title" className="text-2xl font-semibold">
        {t("progress.title")}
      </h1>
      <p className="text-slate-700">{t("progress.intro")}</p>
      {!p.judge_available && (
        <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">
          {t("progress.noJudge", { n: p.pending_not_judged })}
        </p>
      )}

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat
          label={t("progress.breadth")}
          value={t("progress.breadthValue", { n: p.demonstrated_topics, total: p.total_topics })}
        />
        <Stat
          label={t("progress.hintReliance")}
          value={p.hint_reliance_30d === null ? none : `${Math.round(p.hint_reliance_30d * 100)}%`}
        />
        <Stat
          label={t("progress.medianAttempts")}
          value={p.median_attempts_to_accept === null ? none : String(p.median_attempts_to_accept)}
        />
      </dl>

      <p className="text-sm text-slate-600">{t("progress.threshold", { n: p.demonstrated_threshold })}</p>

      <ul className="flex flex-col gap-3">
        {p.topics.map((topic) => (
          <li key={topic.slug} className="card flex flex-col gap-2">
            <h2 className="font-semibold">{topic.name}</h2>
            <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {topic.stages.map((st) => {
                const unaided = Object.entries(st.unaided_by_band) as [Band, number][];
                return (
                  <li
                    key={st.stage}
                    className={`rounded-md border p-2 text-sm ${st.demonstrated ? "border-emerald-300 bg-emerald-50" : "border-slate-200"}`}
                  >
                    <p className="font-medium">{t("progress.stage", { stage: st.stage })}</p>
                    <p>{st.demonstrated ? t("progress.demonstrated") : t("progress.notYet")}</p>
                    {unaided.length > 0 && (
                      <p className="text-slate-700">
                        {t("progress.unaided")}:{" "}
                        {unaided.map(([band, n]) => `${t(`band.${band}` as MessageKey)} ${n}`).join(", ")}
                      </p>
                    )}
                    {st.with_hints > 0 && <p className="text-slate-700">{t("progress.withHints", { n: st.with_hints })}</p>}
                  </li>
                );
              })}
            </ol>
          </li>
        ))}
      </ul>

      {p.self_reported.length > 0 && (
        <section aria-labelledby="self-title">
          <h2 id="self-title" className="mb-2 font-semibold">
            {t("progress.selfReported")}
          </h2>
          <ul className="flex flex-wrap gap-2 text-sm">
            {p.self_reported.map((r) => (
              <li key={r.problem_slug}>
                <Link href={`/problems/${r.problem_slug}`}>{r.problem_slug}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
