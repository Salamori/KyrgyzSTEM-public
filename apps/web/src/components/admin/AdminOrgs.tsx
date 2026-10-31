"use client";

import { useId, useState, type FormEvent } from "react";

import { api, unwrap, type OrgAdminView } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Field } from "../Field";
import { EmptyState, ErrorState, Loading, SuccessMessage } from "../states";

type Kind = OrgAdminView["kind"];

/** Platform admins create organizations and name org admins; they see counts, never rosters. */
export function AdminOrgs() {
  const { t } = useI18n();
  const kindId = useId();
  const orgs = useLoad<OrgAdminView[]>(() => unwrap(api.GET("/api/v1/admin/orgs")), []);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("school");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await unwrap(api.POST("/api/v1/admin/orgs", { body: { name, kind } }));
      setName("");
      setMessage({ ok: true, text: t("admin.orgCreated") });
      void orgs.reload();
    } catch (err) {
      setMessage({ ok: false, text: t(errorKey(err)) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="aorgs-title" className="flex flex-col gap-4">
      <h1 id="aorgs-title" className="text-2xl font-semibold">
        {t("admin.orgsTitle")}
      </h1>
      <p className="text-slate-700">{t("admin.orgsLead")}</p>

      <form onSubmit={(e) => void onCreate(e)} className="card flex flex-col gap-3">
        <Field label={t("admin.orgName")} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
        <div className="flex flex-col gap-1">
          <label htmlFor={kindId} className="font-medium">
            {t("orgs.groupKind")}
          </label>
          <select id={kindId} className="input" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            <option value="school">{t("orgs.kind.school")}</option>
            <option value="club">{t("orgs.kind.club")}</option>
            <option value="other">{t("orgs.kind.other")}</option>
          </select>
        </div>
        <button type="submit" className="btn-primary self-start" disabled={busy}>
          {t("admin.createOrg")}
        </button>
        {message &&
          (message.ok ? <SuccessMessage>{message.text}</SuccessMessage> : <ErrorState message={message.text} />)}
      </form>

      {orgs.state.status === "loading" && <Loading />}
      {orgs.state.status === "error" && (
        <ErrorState message={t(errorKey(orgs.state.error))} onRetry={() => void orgs.reload()} />
      )}
      {orgs.state.status === "ready" && orgs.state.data.length === 0 && <EmptyState>{t("admin.noOrgs")}</EmptyState>}
      {orgs.state.status === "ready" && orgs.state.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {orgs.state.data.map((o) => (
            <OrgRow key={o.id} org={o} onChanged={() => void orgs.reload()} />
          ))}
        </ul>
      )}
    </section>
  );
}

function OrgRow({ org, onChanged }: { org: OrgAdminView; onChanged: () => void }) {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function onAssign(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await unwrap(api.POST("/api/v1/admin/orgs/{org_id}/admins", { params: { path: { org_id: org.id } }, body: { email } }));
      setEmail("");
      setMessage({ ok: true, text: t("admin.assigned") });
      onChanged();
    } catch (err) {
      setMessage({ ok: false, text: t(errorKey(err)) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="card flex flex-col gap-2">
      <p className="font-medium">{org.name}</p>
      <p className="text-sm text-slate-600">
        {t(`orgs.kind.${org.kind}` as MessageKey)}
        {org.is_synthetic && ` · ${t("orgs.synthetic")}`} ·{" "}
        {t("admin.orgCounts", { admins: org.admins, teachers: org.teachers, students: org.students, groups: org.groups })}
      </p>
      <form onSubmit={(e) => void onAssign(e)} className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <Field label={t("admin.assignAdmin")} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <button type="submit" className="btn-secondary" disabled={busy}>
          {t("admin.assign")}
        </button>
      </form>
      {message &&
        (message.ok ? <SuccessMessage>{message.text}</SuccessMessage> : <ErrorState message={message.text} />)}
    </li>
  );
}
