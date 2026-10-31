"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import {
  api,
  ApiError,
  unwrap,
  type AttendanceRow,
  type AttendanceStatus,
  type GroupMemberOut,
  type OrgGroupOut,
  type GroupSummary,
  type JoinCodeOut,
  type SessionOut,
} from "@/lib/api/client";
import { formatDate, formatDateTime } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Field } from "../Field";
import { EmptyState, ErrorState, Loading, SuccessMessage, Unauthorized } from "../states";
import { StatusBadge } from "./OrgsHome";

const STATUSES: AttendanceStatus[] = ["present", "absent", "excused"];

type Ids = { orgId: string; groupId: string };

/** A group: the teacher's attendance tools, or a student's own status per session. */
export function GroupView(ids: Ids) {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <GroupBody {...ids} />;
}

function GroupBody({ orgId, groupId }: Ids) {
  const { t } = useI18n();
  const path = { org_id: orgId, group_id: groupId };
  const group = useLoad<OrgGroupOut>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/groups/{group_id}", { params: { path } })),
    [orgId, groupId],
  );
  const sessions = useLoad<SessionOut[]>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/groups/{group_id}/sessions", { params: { path } })),
    [orgId, groupId],
  );

  if (group.state.status === "loading") return <Loading />;
  if (group.state.status === "error") {
    const err = group.state.error;
    const missing = err instanceof ApiError && (err.status === 404 || err.status === 422);
    return <ErrorState message={missing ? t("errors.not_found") : t(errorKey(err))} />;
  }
  const g = group.state.data;

  return (
    <section aria-labelledby="group-title" className="flex flex-col gap-6">
      <Link href={`/orgs/${orgId}`} className="text-sm">
        ← {t("orgs.groups")}
      </Link>
      <div>
        <h1 id="group-title" className="text-2xl font-semibold">
          {g.name}
        </h1>
        {g.lead_name && <p className="text-sm text-slate-600">{t("orgs.teacherName", { name: g.lead_name })}</p>}
      </div>

      {g.can_manage && (
        <>
          <JoinCodePanel ids={{ orgId, groupId }} expiresAt={g.join_code_expires_at} />
          <SummaryPanel ids={{ orgId, groupId }} version={sessions.state.status === "ready" ? sessions.state.data : null} />
        </>
      )}

      <section aria-labelledby="sessions-title" className="flex flex-col gap-3">
        <h2 id="sessions-title" className="text-lg font-semibold">
          {t("group.sessions")}
        </h2>
        {sessions.state.status === "loading" && <Loading />}
        {sessions.state.status === "error" && (
          <ErrorState message={t(errorKey(sessions.state.error))} onRetry={() => void sessions.reload()} />
        )}
        {sessions.state.status === "ready" && sessions.state.data.length === 0 && (
          <EmptyState>{t("group.noSessions")}</EmptyState>
        )}
        {sessions.state.status === "ready" && sessions.state.data.length > 0 && (
          <ul className="flex flex-col gap-2">
            {sessions.state.data.map((s) => (
              <SessionItem
                key={s.id}
                ids={{ orgId, groupId }}
                session={s}
                manage={g.can_manage}
                onSaved={() => void sessions.reload()}
              />
            ))}
          </ul>
        )}
        {g.can_manage && <NewSession ids={{ orgId, groupId }} onCreated={() => void sessions.reload()} />}
      </section>

      {g.can_manage ? (
        <MembersPanel ids={{ orgId, groupId }} />
      ) : (
        <LeavePanel ids={{ orgId, groupId }} />
      )}
    </section>
  );
}

function JoinCodePanel({ ids, expiresAt }: { ids: Ids; expiresAt: string | null }) {
  const { t, locale } = useI18n();
  const [code, setCode] = useState<JoinCodeOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const active = expiresAt && new Date(expiresAt) > new Date() ? expiresAt : null;

  async function onNew() {
    setBusy(true);
    setError(null);
    try {
      setCode(
        await unwrap(
          api.POST("/api/v1/orgs/{org_id}/groups/{group_id}/join-code", {
            params: { path: { org_id: ids.orgId, group_id: ids.groupId } },
          }),
        ),
      );
    } catch (err) {
      setError(t(errorKey(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="code-title" className="card flex flex-col gap-3">
      <h2 id="code-title" className="text-lg font-semibold">
        {t("group.joinCode")}
      </h2>
      {code ? (
        <p>
          {t("group.codeLabel")}: <strong className="font-mono text-xl tracking-widest">{code.code}</strong>
          <span className="block text-sm text-slate-600">
            {t("group.codeExpires", { date: formatDate(locale, code.expires_at) })}
          </span>
        </p>
      ) : (
        <p className="text-slate-700">
          {active ? t("group.codeActive", { date: formatDate(locale, active) }) : t("group.noCode")}
        </p>
      )}
      <button type="button" className="btn-secondary self-start" onClick={() => void onNew()} disabled={busy}>
        {t("group.newCode")}
      </button>
      {error && <ErrorState message={error} />}
    </section>
  );
}

function SummaryPanel({ ids, version }: { ids: Ids; version: unknown }) {
  const { t, locale } = useI18n();
  const path = { org_id: ids.orgId, group_id: ids.groupId };
  // Re-fetch whenever the session list changes (attendance saved, session added).
  const summary = useLoad<GroupSummary>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/groups/{group_id}/summary", { params: { path } })),
    [ids.orgId, ids.groupId, version],
  );
  const rate =
    summary.state.status === "ready" && summary.state.data.attendance_rate !== null
      ? new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(
          summary.state.data.attendance_rate,
        )
      : t("group.noRate");

  return (
    <section aria-labelledby="summary-title" className="card flex flex-col gap-3">
      <h2 id="summary-title" className="text-lg font-semibold">
        {t("group.summary")}
      </h2>
      {summary.state.status === "loading" && <Loading />}
      {summary.state.status === "error" && (
        <ErrorState message={t(errorKey(summary.state.error))} onRetry={() => void summary.reload()} />
      )}
      {summary.state.status === "ready" && (
        <p>
          {t("group.summaryLine", { sessions: summary.state.data.sessions, members: summary.state.data.members, rate })}
        </p>
      )}
      <a
        className="btn-secondary self-start no-underline"
        href={`/api/v1/orgs/${ids.orgId}/groups/${ids.groupId}/attendance.csv`}
        download
      >
        {t("group.downloadCsv")}
      </a>
    </section>
  );
}

function SessionItem({
  ids,
  session,
  manage,
  onSaved,
}: {
  ids: Ids;
  session: SessionOut;
  manage: boolean;
  onSaved: () => void;
}) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <li className="card flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{formatDateTime(locale, session.starts_at)}</p>
          {session.title && <p className="text-sm text-slate-600">{session.title}</p>}
        </div>
        {!manage && <StatusBadge status={session.my_status} />}
      </div>
      {manage && (
        <>
          <p className="text-sm text-slate-600">
            {t("group.counts", {
              present: session.present ?? 0,
              absent: session.absent ?? 0,
              excused: session.excused ?? 0,
              unmarked: session.unmarked ?? 0,
            })}
          </p>
          <button
            type="button"
            className="btn-secondary self-start"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? t("group.close") : t("group.takeAttendance")}
          </button>
          {open && (
            <AttendanceSheet
              ids={ids}
              sessionId={session.id}
              onSaved={() => {
                setOpen(false);
                onSaved();
              }}
            />
          )}
        </>
      )}
    </li>
  );
}

function AttendanceSheet({ ids, sessionId, onSaved }: { ids: Ids; sessionId: string; onSaved: () => void }) {
  const { t } = useI18n();
  const path = { org_id: ids.orgId, group_id: ids.groupId, session_id: sessionId };
  const rows = useLoad<AttendanceRow[]>(
    () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance", { params: { path } })),
    [sessionId],
  );
  const [marks, setMarks] = useState<Record<string, AttendanceStatus>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (rows.state.status === "loading") return <Loading />;
  if (rows.state.status === "error")
    return <ErrorState message={t(errorKey(rows.state.error))} onRetry={() => void rows.reload()} />;
  if (rows.state.data.length === 0) return <EmptyState>{t("group.noMembers")}</EmptyState>;
  const data = rows.state.data;
  const current = (r: AttendanceRow) => marks[r.user_id] ?? r.status;

  async function onSave() {
    const records = data
      .map((r) => ({ user_id: r.user_id, status: current(r) }))
      .filter((r): r is { user_id: string; status: AttendanceStatus } => r.status !== null);
    if (records.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await unwrap(
        api.PUT("/api/v1/orgs/{org_id}/groups/{group_id}/sessions/{session_id}/attendance", {
          params: { path },
          body: { records },
        }),
      );
      onSaved();
    } catch (err) {
      setError(t(errorKey(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col divide-y divide-slate-200">
        {data.map((r) => (
          <li key={r.user_id} className="flex flex-col gap-2 py-2 sm:flex-row sm:items-center sm:justify-between">
            <span className="font-medium">{r.display_name}</span>
            <fieldset className="flex gap-1">
              <legend className="sr-only">{r.display_name}</legend>
              {STATUSES.map((s) => {
                const checked = current(r) === s;
                return (
                  <label
                    key={s}
                    className={`min-h-11 flex-1 cursor-pointer rounded-md border px-3 py-2 text-center text-sm sm:flex-none ${
                      checked ? "border-teal-800 bg-teal-800 text-white" : "border-slate-400 bg-surface"
                    }`}
                  >
                    <input
                      type="radio"
                      className="sr-only"
                      name={`att-${sessionId}-${r.user_id}`}
                      value={s}
                      checked={checked}
                      onChange={() => setMarks({ ...marks, [r.user_id]: s })}
                    />
                    {t(`orgs.status.${s}`)}
                  </label>
                );
              })}
            </fieldset>
          </li>
        ))}
      </ul>
      <button type="button" className="btn-primary self-start" onClick={() => void onSave()} disabled={busy}>
        {t("group.saveAttendance")}
      </button>
      {error && <ErrorState message={error} />}
    </div>
  );
}

function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function NewSession({ ids, onCreated }: { ids: Ids; onCreated: () => void }) {
  const { t } = useI18n();
  const [startsAt, setStartsAt] = useState(() => toLocalInput(new Date()));
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(
        api.POST("/api/v1/orgs/{org_id}/groups/{group_id}/sessions", {
          params: { path: { org_id: ids.orgId, group_id: ids.groupId } },
          body: { starts_at: new Date(startsAt).toISOString(), title: title.trim() || null },
        }),
      );
      setTitle("");
      onCreated();
    } catch (err) {
      setError(t(errorKey(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="card flex flex-col gap-3" aria-labelledby="newsession-title">
      <h3 id="newsession-title" className="font-semibold">
        {t("group.newSession")}
      </h3>
      <Field
        label={t("group.startsAt")}
        type="datetime-local"
        value={startsAt}
        onChange={(e) => setStartsAt(e.target.value)}
        required
      />
      <Field label={t("group.sessionTitle")} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} />
      <button type="submit" className="btn-primary self-start" disabled={busy}>
        {t("group.addSession")}
      </button>
      {error && <ErrorState message={error} />}
    </form>
  );
}

function MembersPanel({ ids }: { ids: Ids }) {
  const { t, locale } = useI18n();
  const path = { org_id: ids.orgId, group_id: ids.groupId };
  const members = useLoad<GroupMemberOut[]>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/groups/{group_id}/members", { params: { path } })),
    [ids.orgId, ids.groupId],
  );
  const [error, setError] = useState<string | null>(null);

  async function onRemove(userId: string) {
    setError(null);
    try {
      await unwrap(
        api.DELETE("/api/v1/orgs/{org_id}/groups/{group_id}/members/{user_id}", {
          params: { path: { ...path, user_id: userId } },
        }),
      );
      void members.reload();
    } catch (err) {
      setError(t(errorKey(err)));
    }
  }

  return (
    <section aria-labelledby="members-title" className="flex flex-col gap-3">
      <h2 id="members-title" className="text-lg font-semibold">
        {t("group.members")}
      </h2>
      {members.state.status === "loading" && <Loading />}
      {members.state.status === "error" && (
        <ErrorState message={t(errorKey(members.state.error))} onRetry={() => void members.reload()} />
      )}
      {members.state.status === "ready" && members.state.data.length === 0 && (
        <EmptyState>{t("group.noMembers")}</EmptyState>
      )}
      {members.state.status === "ready" && members.state.data.length > 0 && (
        <ul className="flex flex-col divide-y divide-slate-200 rounded-xl border border-slate-200 bg-surface">
          {members.state.data.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-2">
              <span className="min-w-0">
                {m.display_name} <span className="text-sm text-slate-600">· {formatDate(locale, m.joined_at)}</span>
              </span>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void onRemove(m.user_id)}
                aria-label={`${t("orgs.remove")}: ${m.display_name}`}
              >
                {t("orgs.remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <ErrorState message={error} />}
    </section>
  );
}

function LeavePanel({ ids }: { ids: Ids }) {
  const { t } = useI18n();
  const { state } = useSession();
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(false);
  if (state.status !== "authenticated") return null;
  const userId = state.user.id;

  async function onLeave() {
    setError(null);
    try {
      await unwrap(
        api.DELETE("/api/v1/orgs/{org_id}/groups/{group_id}/members/{user_id}", {
          params: { path: { org_id: ids.orgId, group_id: ids.groupId, user_id: userId } },
        }),
      );
      setLeft(true);
      router.push("/orgs");
    } catch (err) {
      setError(t(errorKey(err)));
    }
  }

  if (left) return <SuccessMessage>{t("group.left")}</SuccessMessage>;
  return (
    <section className="flex flex-col gap-2">
      {confirming ? (
        <div role="alertdialog" aria-labelledby="leave-q" className="card flex flex-col gap-3">
          <p id="leave-q">{t("group.leaveConfirm")}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={() => void onLeave()}>
              {t("group.leaveYes")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setConfirming(false)}>
              {t("group.close")}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn-secondary self-start" onClick={() => setConfirming(true)}>
          {t("group.leave")}
        </button>
      )}
      {error && <ErrorState message={error} />}
    </section>
  );
}
