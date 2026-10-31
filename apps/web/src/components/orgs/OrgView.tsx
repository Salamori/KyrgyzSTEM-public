"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { api, ApiError, unwrap, type OrgGroupOut, type MemberOut, type OrgOut } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Field } from "../Field";
import { EmptyState, ErrorState, Loading, SuccessMessage, Unauthorized } from "../states";

/** One organization: its groups the viewer can see, plus staff management for org admins. */
export function OrgView({ orgId }: { orgId: string }) {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  return <OrgBody orgId={orgId} />;
}

function OrgBody({ orgId }: { orgId: string }) {
  const { t } = useI18n();
  const path = { org_id: orgId };
  const org = useLoad<OrgOut>(() => unwrap(api.GET("/api/v1/orgs/{org_id}", { params: { path } })), [orgId]);
  const groups = useLoad<OrgGroupOut[]>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/groups", { params: { path } })),
    [orgId],
  );

  if (org.state.status === "loading") return <Loading />;
  if (org.state.status === "error") {
    const err = org.state.error;
    const missing = err instanceof ApiError && (err.status === 404 || err.status === 422);
    return <ErrorState message={missing ? t("errors.not_found") : t(errorKey(err))} />;
  }
  const o = org.state.data;
  const staff = o.my_role === "org_admin" || o.my_role === "teacher";

  return (
    <section aria-labelledby="org-title" className="flex flex-col gap-6">
      <Link href="/orgs" className="text-sm">
        ← {t("orgs.back")}
      </Link>
      <div>
        <h1 id="org-title" className="text-2xl font-semibold">
          {o.name}
        </h1>
        <p className="text-sm text-slate-600">
          {t(`orgs.kind.${o.kind}` as MessageKey)}
          {o.my_role && ` · ${t(`orgs.role.${o.my_role}` as MessageKey)}`}
          {o.is_synthetic && ` · ${t("orgs.synthetic")}`}
        </p>
      </div>

      <section aria-labelledby="groups-title" className="flex flex-col gap-3">
        <h2 id="groups-title" className="text-lg font-semibold">
          {t("orgs.groups")}
        </h2>
        {groups.state.status === "loading" && <Loading />}
        {groups.state.status === "error" && (
          <ErrorState message={t(errorKey(groups.state.error))} onRetry={() => void groups.reload()} />
        )}
        {groups.state.status === "ready" && groups.state.data.length === 0 && (
          <EmptyState>{t("orgs.noGroups")}</EmptyState>
        )}
        {groups.state.status === "ready" && groups.state.data.length > 0 && (
          <ul className="flex flex-col gap-2">
            {groups.state.data.map((g) => (
              <li key={g.id} className="card flex flex-col gap-1">
                <Link href={`/orgs/${orgId}/groups/${g.id}`} className="font-medium">
                  {g.name}
                </Link>
                <p className="text-sm text-slate-600">
                  {t(`orgs.gkind.${g.kind}` as MessageKey)}
                  {g.lead_name && ` · ${t("orgs.teacherName", { name: g.lead_name })}`}
                  {g.can_manage && ` · ${t("orgs.memberCount", { n: g.member_count })}`}
                </p>
              </li>
            ))}
          </ul>
        )}
        {staff && <CreateGroup orgId={orgId} onCreated={() => void groups.reload()} />}
      </section>

      {o.my_role === "org_admin" && <StaffPanel orgId={orgId} />}
    </section>
  );
}

function CreateGroup({ orgId, onCreated }: { orgId: string; onCreated: () => void }) {
  const { t } = useI18n();
  const kindId = useId();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"club" | "class">("club");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await unwrap(api.POST("/api/v1/orgs/{org_id}/groups", { params: { path: { org_id: orgId } }, body: { name, kind } }));
      setName("");
      setMessage({ ok: true, text: t("orgs.created") });
      onCreated();
    } catch (err) {
      setMessage({ ok: false, text: t(errorKey(err)) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="card flex flex-col gap-3" aria-labelledby="newgroup-title">
      <h3 id="newgroup-title" className="font-semibold">
        {t("orgs.createGroup")}
      </h3>
      <Field label={t("orgs.groupName")} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
      <div className="flex flex-col gap-1">
        <label htmlFor={kindId} className="font-medium">
          {t("orgs.groupKind")}
        </label>
        <select id={kindId} className="input" value={kind} onChange={(e) => setKind(e.target.value as "club" | "class")}>
          <option value="club">{t("orgs.gkind.club")}</option>
          <option value="class">{t("orgs.gkind.class")}</option>
        </select>
      </div>
      <button type="submit" className="btn-primary self-start" disabled={busy}>
        {t("orgs.create")}
      </button>
      {message &&
        (message.ok ? <SuccessMessage>{message.text}</SuccessMessage> : <ErrorState message={message.text} />)}
    </form>
  );
}

function StaffPanel({ orgId }: { orgId: string }) {
  const { t } = useI18n();
  const roleId = useId();
  const path = { org_id: orgId };
  const members = useLoad<MemberOut[]>(
    () => unwrap(api.GET("/api/v1/orgs/{org_id}/members", { params: { path } })),
    [orgId],
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"teacher" | "org_admin">("teacher");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const m = await unwrap(api.POST("/api/v1/orgs/{org_id}/members", { params: { path }, body: { email, role } }));
      setEmail("");
      setMessage({ ok: true, text: t("orgs.added", { name: m.display_name }) });
      void members.reload();
    } catch (err) {
      setMessage({ ok: false, text: t(errorKey(err)) });
    } finally {
      setBusy(false);
    }
  }

  async function onRemove(userId: string) {
    setMessage(null);
    try {
      await unwrap(api.DELETE("/api/v1/orgs/{org_id}/members/{user_id}", { params: { path: { ...path, user_id: userId } } }));
      void members.reload();
    } catch (err) {
      setMessage({ ok: false, text: t(errorKey(err)) });
    }
  }

  return (
    <section aria-labelledby="staff-title" className="flex flex-col gap-3">
      <h2 id="staff-title" className="text-lg font-semibold">
        {t("orgs.staff")}
      </h2>
      {members.state.status === "loading" && <Loading />}
      {members.state.status === "error" && (
        <ErrorState message={t(errorKey(members.state.error))} onRetry={() => void members.reload()} />
      )}
      {members.state.status === "ready" && (
        <ul className="flex flex-col divide-y divide-slate-200 rounded-xl border border-slate-200 bg-surface">
          {members.state.data.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-2">
              <span className="min-w-0">
                {m.display_name}{" "}
                <span className="text-sm text-slate-600">· {t(`orgs.role.${m.role}` as MessageKey)}</span>
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
      <form onSubmit={(e) => void onAdd(e)} className="card flex flex-col gap-3" aria-labelledby="addstaff-title">
        <h3 id="addstaff-title" className="font-semibold">
          {t("orgs.addStaff")}
        </h3>
        <Field label={t("orgs.staffEmail")} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <div className="flex flex-col gap-1">
          <label htmlFor={roleId} className="font-medium">
            {t("orgs.staffRole")}
          </label>
          <select
            id={roleId}
            className="input"
            value={role}
            onChange={(e) => setRole(e.target.value as "teacher" | "org_admin")}
          >
            <option value="teacher">{t("orgs.role.teacher")}</option>
            <option value="org_admin">{t("orgs.role.org_admin")}</option>
          </select>
        </div>
        <button type="submit" className="btn-primary self-start" disabled={busy}>
          {t("orgs.add")}
        </button>
      </form>
      {message &&
        (message.ok ? <SuccessMessage>{message.text}</SuccessMessage> : <ErrorState message={message.text} />)}
    </section>
  );
}
