"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { api, unwrap, type MyAttendanceRow, type OrgOut } from "@/lib/api/client";
import { formatDateTime, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Field } from "../Field";
import { EmptyState, ErrorState, Loading, SuccessMessage, Unauthorized } from "../states";

export function StatusBadge({ status }: { status: string | null }) {
  const { t } = useI18n();
  const tone =
    status === "present"
      ? "bg-emerald-600 text-white"
      : status === "absent"
        ? "bg-rose-100 text-rose-900"
        : status === "excused"
          ? "bg-amber-100 text-amber-900"
          : "bg-slate-200 text-slate-800";
  return <span className={`badge ${tone}`}>{t(`orgs.status.${status ?? "unmarked"}` as MessageKey)}</span>;
}

/** Student and staff entry point: join with a code, see your organizations and your own attendance. */
export function OrgsHome() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <OrgsHomeBody />;
}

function OrgsHomeBody() {
  const { t, locale } = useI18n();
  const orgs = useLoad<OrgOut[]>(() => unwrap(api.GET("/api/v1/orgs")), []);
  const mine = useLoad<MyAttendanceRow[]>(() => unwrap(api.GET("/api/v1/me/attendance")), []);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [joined, setJoined] = useState<string | null>(null);
  const [joinError, setJoinError] = useState<string | null>(null);

  async function onJoin(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setJoinError(null);
    setJoined(null);
    try {
      const res = await unwrap(api.POST("/api/v1/groups/join", { body: { code } }));
      setJoined(res.group_name);
      setCode("");
      void orgs.reload();
    } catch (err) {
      setJoinError(t(errorKey(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="orgs-title" className="flex flex-col gap-6">
      <div>
        <h1 id="orgs-title" className="text-2xl font-semibold">
          {t("orgs.title")}
        </h1>
        <p className="mt-1 text-slate-700">{t("orgs.lead")}</p>
      </div>

      <form onSubmit={(e) => void onJoin(e)} className="card flex flex-col gap-3" aria-labelledby="join-title">
        <h2 id="join-title" className="text-lg font-semibold">
          {t("orgs.joinTitle")}
        </h2>
        <Field
          label={t("orgs.joinCode")}
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          minLength={4}
          maxLength={16}
          required
          error={joinError ?? undefined}
        />
        <button type="submit" className="btn-primary self-start" disabled={busy}>
          {t("orgs.join")}
        </button>
        {joined && <SuccessMessage>{t("orgs.joined", { group: joined })}</SuccessMessage>}
      </form>

      <section aria-labelledby="myorgs-title" className="flex flex-col gap-3">
        <h2 id="myorgs-title" className="text-lg font-semibold">
          {t("orgs.myOrgs")}
        </h2>
        {orgs.state.status === "loading" && <Loading />}
        {orgs.state.status === "error" && (
          <ErrorState message={t(errorKey(orgs.state.error))} onRetry={() => void orgs.reload()} />
        )}
        {orgs.state.status === "ready" && orgs.state.data.length === 0 && <EmptyState>{t("orgs.noOrgs")}</EmptyState>}
        {orgs.state.status === "ready" && orgs.state.data.length > 0 && (
          <ul className="flex flex-col gap-2">
            {orgs.state.data.map((o) => (
              <li key={o.id} className="card flex flex-col gap-1">
                <Link href={`/orgs/${o.id}`} className="font-medium">
                  {o.name}
                </Link>
                <p className="text-sm text-slate-600">
                  {t(`orgs.kind.${o.kind}` as MessageKey)}
                  {o.my_role && ` · ${t(`orgs.role.${o.my_role}` as MessageKey)}`}
                  {o.is_synthetic && ` · ${t("orgs.synthetic")}`}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="myatt-title" className="flex flex-col gap-3">
        <h2 id="myatt-title" className="text-lg font-semibold">
          {t("orgs.myAttendance")}
        </h2>
        {mine.state.status === "loading" && <Loading />}
        {mine.state.status === "error" && (
          <ErrorState message={t(errorKey(mine.state.error))} onRetry={() => void mine.reload()} />
        )}
        {mine.state.status === "ready" && mine.state.data.length === 0 && (
          <EmptyState>{t("orgs.noAttendance")}</EmptyState>
        )}
        {mine.state.status === "ready" && mine.state.data.length > 0 && (
          <ul className="flex flex-col gap-2">
            {mine.state.data.map((r, i) => (
              <li key={`${r.starts_at}-${i}`} className="card flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium">{r.group_name}</p>
                  <p className="text-sm text-slate-600">
                    {formatDateTime(locale, r.starts_at)}
                    {r.title && ` · ${r.title}`} · {r.org_name}
                  </p>
                </div>
                <StatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
