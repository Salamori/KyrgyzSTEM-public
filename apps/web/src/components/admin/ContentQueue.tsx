"use client";

import Link from "next/link";
import { useId, useState } from "react";

import { api, unwrap, type ProblemPage, type ProblemSummary } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { BandBadge } from "../olympiad/Badges";
import { EmptyState, ErrorState, Loading } from "../states";

type Status = ProblemSummary["status"];
const STATUSES: Status[] = ["in_review", "draft", "published", "archived"];
const PAGE_SIZE = 50;

/** Content-moderation queue. Reviewing and publishing happen on the problem page. */
export function ContentQueue() {
  const { t, locale } = useI18n();
  const statusId = useId();
  const [status, setStatus] = useState<Status | "">("in_review");
  const page = useLoad<ProblemPage>(
    () =>
      unwrap(
        api.GET("/api/v1/problems", {
          params: { query: { status: status || undefined, locale, limit: PAGE_SIZE, offset: 0 } },
        }),
      ),
    [status, locale],
  );

  return (
    <section aria-labelledby="content-title" className="flex flex-col gap-4">
      <h1 id="content-title" className="text-2xl font-semibold">
        {t("admin.contentTitle")}
      </h1>
      <p className="text-slate-700">{t("admin.contentLead")}</p>
      <div className="flex flex-col gap-1 sm:max-w-xs">
        <label htmlFor={statusId} className="font-medium">
          {t("admin.statusFilter")}
        </label>
        <select
          id={statusId}
          className="input"
          value={status}
          onChange={(e) => setStatus(e.target.value as Status | "")}
        >
          <option value="">{t("admin.allStatuses")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`problemStatus.${s}` as MessageKey)}
            </option>
          ))}
        </select>
      </div>
      {page.state.status === "loading" && <Loading />}
      {page.state.status === "error" && (
        <ErrorState message={t(errorKey(page.state.error))} onRetry={() => void page.reload()} />
      )}
      {page.state.status === "ready" && page.state.data.items.length === 0 && (
        <EmptyState>{t("admin.contentEmpty")}</EmptyState>
      )}
      {page.state.status === "ready" && page.state.data.items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {page.state.data.items.map((p) => (
            <li key={p.id} className="card flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <Link href={`/problems/${p.slug}`} className="font-medium">
                {p.title}
              </Link>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <BandBadge band={p.difficulty_band} status={p.difficulty_status} />
                <span className="badge bg-slate-200 text-slate-800">
                  {t(`problemStatus.${p.status}` as MessageKey)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
