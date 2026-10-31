"use client";

import { useId, useState } from "react";

import { api, unwrap, type Schemas } from "@/lib/api/client";
import { en } from "@/lib/i18n/en";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { EmptyState, ErrorState, Loading } from "../states";

type AuditPage = Schemas["Page_AuditLogEntry_"];
type Entry = Schemas["AuditLogEntry"];
const PAGE_SIZE = 20;

// Every action the API records, derived from the "audit.*" message keys.
const ACTIONS = Object.keys(en)
  .filter((k) => k.startsWith("audit."))
  .map((k) => k.slice("audit.".length));

function label(t: (k: MessageKey) => string, action: string): string {
  const key = `audit.${action}` as MessageKey;
  return key in en ? t(key) : action;
}

/** Read-only view of the append-only audit log. */
export function AuditLog() {
  const { t } = useI18n();
  const actionId = useId();
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const page = useLoad<AuditPage>(
    () =>
      unwrap(
        api.GET("/api/v1/admin/audit-logs", {
          params: { query: { action: action || undefined, limit: PAGE_SIZE, offset } },
        }),
      ),
    [action, offset],
  );

  return (
    <section aria-labelledby="audit-title" className="flex flex-col gap-4">
      <h1 id="audit-title" className="text-2xl font-semibold">
        {t("admin.auditTitle")}
      </h1>
      <p className="text-slate-700">{t("admin.auditLead")}</p>
      <div className="flex flex-col gap-1 sm:max-w-xs">
        <label htmlFor={actionId} className="font-medium">
          {t("admin.auditAction")}
        </label>
        <select
          id={actionId}
          className="input"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">{t("admin.auditAllActions")}</option>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {label(t, a)}
            </option>
          ))}
        </select>
      </div>
      {page.state.status === "loading" && <Loading />}
      {page.state.status === "error" && (
        <ErrorState message={t(errorKey(page.state.error))} onRetry={() => void page.reload()} />
      )}
      {page.state.status === "ready" && page.state.data.items.length === 0 && (
        <EmptyState>{t("admin.auditEmpty")}</EmptyState>
      )}
      {page.state.status === "ready" && page.state.data.items.length > 0 && (
        <>
          <ol className="flex flex-col gap-2">
            {page.state.data.items.map((e) => (
              <AuditRow key={e.id} entry={e} />
            ))}
          </ol>
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
                to: offset + page.state.data.items.length,
                total: page.state.data.total,
              })}
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

function AuditRow({ entry }: { entry: Entry }) {
  const { t, locale } = useI18n();
  const when = new Date(entry.created_at).toLocaleString(locale === "ky" ? "ru" : locale);
  const details = Object.entries(entry.details)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
  return (
    <li className="card flex flex-col gap-1 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{label(t, entry.action)}</span>
        <time dateTime={entry.created_at} className="text-slate-600">
          {when}
        </time>
      </div>
      {entry.actor_id && <p className="text-slate-600">{t("admin.auditBy", { id: entry.actor_id.slice(0, 8) })}</p>}
      {entry.target_type && (
        <p className="text-slate-600">
          {t("admin.auditTarget", { type: entry.target_type, id: (entry.target_id ?? "").slice(0, 8) })}
        </p>
      )}
      {details && <p className="break-words font-mono text-xs text-slate-700">{details}</p>}
    </li>
  );
}
