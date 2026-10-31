"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { api, unwrap, type Band, type ProblemPage, type TopicOut } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { EmptyState, ErrorState, Loading } from "../states";
import { BandBadge, MyStatusBadge } from "./Badges";

const BANDS: Band[] = ["intro", "easy", "medium", "hard", "challenge"];
const PAGE_SIZE = 20;

export interface BankFilters {
  q?: string;
  topic?: string;
  band?: string;
}

function isBand(value: string | undefined): value is Band {
  return BANDS.includes(value as Band);
}

export function ProblemBank({ initial = {} }: { initial?: BankFilters }) {
  const { t, locale } = useI18n();
  const searchId = useId();
  const topicId = useId();
  const bandId = useId();
  const [query, setQuery] = useState(initial.q ?? "");
  const [debounced, setDebounced] = useState(initial.q ?? "");
  const [topic, setTopic] = useState(initial.topic ?? "");
  const [band, setBand] = useState<Band | "">(isBand(initial.band) ? initial.band : "");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  // Mirror filters into the address bar (no reload) so a filtered view can be shared.
  useEffect(() => {
    const params = new URLSearchParams();
    if (debounced) params.set("q", debounced);
    if (topic) params.set("topic", topic);
    if (band) params.set("band", band);
    const search = params.toString();
    window.history.replaceState(null, "", search ? `?${search}` : window.location.pathname);
  }, [debounced, topic, band]);

  const topics = useLoad<TopicOut[]>(
    () => unwrap(api.GET("/api/v1/topics", { params: { query: { locale } } })),
    [locale],
  );
  const problems = useLoad<ProblemPage>(
    () =>
      unwrap(
        api.GET("/api/v1/problems", {
          params: {
            query: {
              q: debounced || undefined,
              topic: topic || undefined,
              band: band || undefined,
              locale,
              limit: PAGE_SIZE,
              offset,
            },
          },
        }),
      ),
    [debounced, topic, band, offset, locale],
  );
  const filtered = Boolean(debounced || topic || band);

  return (
    <section aria-labelledby="bank-title" className="flex flex-col gap-4">
      <h1 id="bank-title" className="text-2xl font-semibold">
        {t("problems.title")}
      </h1>
      <p className="text-sm text-slate-600">{t("bandStatus.explain")}</p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_1fr_1fr]">
        <div className="flex flex-col gap-1">
          <label htmlFor={searchId} className="font-medium">
            {t("problems.search")}
          </label>
          <input
            id={searchId}
            type="search"
            className="input"
            maxLength={100}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={topicId} className="font-medium">
            {t("problems.topic")}
          </label>
          <select
            id={topicId}
            className="input"
            value={topic}
            onChange={(e) => {
              setTopic(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t("problems.allTopics")}</option>
            {topics.state.status === "ready" &&
              topics.state.data.map((tp) => (
                <option key={tp.slug} value={tp.slug}>
                  {tp.name}
                </option>
              ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={bandId} className="font-medium">
            {t("problems.band")}
          </label>
          <select
            id={bandId}
            className="input"
            value={band}
            onChange={(e) => {
              setBand(e.target.value as Band | "");
              setOffset(0);
            }}
          >
            <option value="">{t("problems.allBands")}</option>
            {BANDS.map((b) => (
              <option key={b} value={b}>
                {t(`band.${b}` as MessageKey)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {problems.state.status === "loading" && <Loading />}
      {problems.state.status === "error" && (
        <ErrorState message={t(errorKey(problems.state.error))} onRetry={() => void problems.reload()} />
      )}
      {problems.state.status === "ready" && problems.state.data.items.length === 0 && (
        <EmptyState>{filtered ? t("problems.empty") : t("problems.emptyAll")}</EmptyState>
      )}
      {problems.state.status === "ready" && problems.state.data.items.length > 0 && (
        <>
          <p className="text-sm text-slate-600" aria-live="polite">
            {t("problems.count", { total: problems.state.data.total })}
          </p>
          <ul className="flex flex-col gap-3">
            {problems.state.data.items.map((p) => (
              <li key={p.id} className="card flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Link href={`/problems/${p.slug}`} className="text-lg font-semibold">
                    {p.title}
                  </Link>
                  <MyStatusBadge status={p.my_status} />
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <BandBadge band={p.difficulty_band} status={p.difficulty_status} />
                  <span className="text-slate-700">{p.topics.map((tp) => tp.name).join(" · ")}</span>
                  {p.status !== "published" && (
                    <span className="badge border border-slate-300 bg-slate-100 text-slate-900">
                      {t(`problemStatus.${p.status}` as MessageKey)}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <nav aria-label={t("admin.pagination")} className="flex items-center justify-between gap-3 text-sm">
            <button
              type="button"
              className="btn-secondary"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              {t("admin.prev")}
            </button>
            <span>
              {t("admin.showing", {
                from: offset + 1,
                to: offset + problems.state.data.items.length,
                total: problems.state.data.total,
              })}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={offset + problems.state.data.items.length >= problems.state.data.total}
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
