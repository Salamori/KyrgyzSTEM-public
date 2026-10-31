"use client";

import { useId, useState, type FormEvent } from "react";

import { api, unwrap, type UserPrivate } from "@/lib/api/client";
import { LOCALES, LOCALE_NAMES, formatDate, isLocale, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { AccountActions } from "./AccountActions";
import { errorKey, invalidFields } from "./errorMessage";
import { Field } from "./Field";
import { PrivacyPanel } from "./PrivacyPanel";
import { ErrorState, Loading, SuccessMessage, Unauthorized } from "./states";

export function ProfileView() {
  const { state, refresh } = useSession();
  const { t } = useI18n();

  if (state.status === "loading") return <Loading />;
  if (state.status === "anonymous") return <Unauthorized />;
  if (state.status === "error") return <ErrorState message={t(errorKey(state.error))} onRetry={() => void refresh()} />;

  return (
    <div className="flex flex-col gap-6">
      <ProfileForm user={state.user} />
      <PrivacyPanel />
      <PasswordForm />
      <AccountActions />
    </div>
  );
}

function ProfileForm({ user }: { user: UserPrivate }) {
  const { t, locale: uiLocale, setLocale } = useI18n();
  const { setUser } = useSession();
  const localeId = useId();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const preferred = String(form.get("preferred_locale"));
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await unwrap(
        api.PATCH("/api/v1/users/me", {
          body: {
            display_name: String(form.get("display_name")),
            preferred_locale: isLocale(preferred) ? preferred : undefined,
          },
        }),
      );
      setUser(updated);
      if (isLocale(updated.preferred_locale) && updated.preferred_locale !== uiLocale) {
        setLocale(updated.preferred_locale);
      }
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const memberSince = formatDate(uiLocale, user.created_at);

  return (
    <section aria-labelledby="profile-title" className="card">
      <h1 id="profile-title" className="mb-4 text-2xl font-semibold">
        {t("profile.title")}
      </h1>
      <dl className="mb-5 grid grid-cols-1 gap-2 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-6">
        <dt className="font-medium text-slate-600">{t("auth.email")}</dt>
        <dd className="break-all">{user.email}</dd>
        <dt className="font-medium text-slate-600">{t("profile.role")}</dt>
        <dd>{t(`roles.${user.role}` as MessageKey)}</dd>
        <dt className="font-medium text-slate-600">{t("profile.memberSince")}</dt>
        <dd>{memberSince}</dd>
      </dl>
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
        {error !== null && <ErrorState message={t(errorKey(error))} />}
        {saved && <SuccessMessage>{t("profile.saved")}</SuccessMessage>}
        <Field
          label={t("auth.displayName")}
          name="display_name"
          defaultValue={user.display_name}
          maxLength={80}
          required
          error={invalidFields(error).has("display_name") ? t("errors.validation_error") : undefined}
        />
        <div className="flex flex-col gap-1">
          <label htmlFor={localeId} className="font-medium">
            {t("profile.locale")}
          </label>
          <select id={localeId} name="preferred_locale" defaultValue={user.preferred_locale} className="input">
            {LOCALES.map((l) => (
              <option key={l} value={l} lang={l}>
                {LOCALE_NAMES[l]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <button type="submit" className="btn-primary" disabled={pending}>
            {pending ? t("profile.saving") : t("profile.save")}
          </button>
        </div>
      </form>
    </section>
  );
}

function PasswordForm() {
  const { t } = useI18n();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const form = new FormData(formEl);
    setPending(true);
    setError(null);
    setDone(false);
    try {
      await unwrap(
        api.POST("/api/v1/auth/password", {
          body: {
            current_password: String(form.get("current_password")),
            new_password: String(form.get("new_password")),
          },
        }),
      );
      formEl.reset();
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="password-title" className="card">
      <h2 id="password-title" className="mb-4 text-xl font-semibold">
        {t("profile.passwordTitle")}
      </h2>
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
        {error !== null && <ErrorState message={t(errorKey(error))} />}
        {done && <SuccessMessage>{t("profile.passwordChanged")}</SuccessMessage>}
        <Field
          label={t("profile.currentPassword")}
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
        />
        <Field
          label={t("profile.newPassword")}
          name="new_password"
          type="password"
          autoComplete="new-password"
          minLength={10}
          maxLength={128}
          hint={t("auth.passwordHint")}
          required
          error={invalidFields(error).has("new_password") ? t("errors.validation_error") : undefined}
        />
        <div>
          <button type="submit" className="btn-primary" disabled={pending}>
            {t("profile.changePassword")}
          </button>
        </div>
      </form>
    </section>
  );
}
