"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, unwrap } from "@/lib/api/client";
import { clearDrafts } from "@/lib/drafts";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { errorKey } from "./errorMessage";
import { Field } from "./Field";
import { ErrorState } from "./states";

/** Sign out everywhere, and the permanent account deletion. */
export function AccountActions() {
  const { t } = useI18n();
  const { refresh } = useSession();
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function leave() {
    clearDrafts();
    await refresh(); // the session is gone, so this ends at "anonymous"
    router.push("/");
  }

  async function signOutEverywhere() {
    setPending(true);
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/auth/logout-all"));
      await leave();
    } catch (err) {
      setError(err);
      setPending(false);
    }
  }

  async function onDelete(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setPending(true);
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/users/me/delete", { body: { password: String(form.get("password")) } }));
      await leave();
    } catch (err) {
      setError(err);
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="account-title" className="card flex flex-col gap-4">
      <h2 id="account-title" className="text-xl font-semibold">
        {t("profile.accountTitle")}
      </h2>
      {error !== null && <ErrorState message={t(errorKey(error))} />}
      <p className="text-slate-700">{t("profile.sessionsBody")}</p>
      <div>
        <button type="button" className="btn-secondary" disabled={pending} onClick={() => void signOutEverywhere()}>
          {t("profile.signOutEverywhere")}
        </button>
      </div>
      <form onSubmit={(e) => void onDelete(e)} className="flex flex-col gap-4 border-t border-slate-200 pt-4">
        <p className="text-slate-700">{t("profile.deleteBody")}</p>
        <Field
          label={t("profile.deletePassword")}
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
        <label className="flex items-start gap-2">
          <input type="checkbox" name="confirm" required className="mt-1" />
          <span>{t("profile.deleteConfirm")}</span>
        </label>
        <div>
          <button type="submit" className="btn-secondary" disabled={pending}>
            {t("profile.deleteSubmit")}
          </button>
        </div>
      </form>
    </section>
  );
}
