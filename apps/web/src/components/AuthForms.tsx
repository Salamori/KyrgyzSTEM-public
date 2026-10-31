"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { api, unwrap, type SessionInfo } from "@/lib/api/client";
import { isLocale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { errorKey, invalidFields } from "./errorMessage";
import { Field } from "./Field";
import { ErrorState, Loading, SuccessMessage } from "./states";

function useCompleteSignIn() {
  const { setUser } = useSession();
  const { setLocale } = useI18n();
  const router = useRouter();
  return (info: SessionInfo) => {
    setUser(info.user);
    if (isLocale(info.user.preferred_locale)) setLocale(info.user.preferred_locale);
    router.push("/dashboard");
  };
}

function AlreadySignedIn() {
  const { t } = useI18n();
  const { state } = useSession();
  if (state.status !== "authenticated") return null;
  return (
    <section className="card">
      <p>{t("auth.alreadySignedIn", { name: state.user.display_name })}</p>
      <Link href="/dashboard" className="btn-primary mt-4 inline-block">
        {t("home.ctaDashboard")}
      </Link>
    </section>
  );
}

export function LoginForm() {
  const { t } = useI18n();
  const { state } = useSession();
  const complete = useCompleteSignIn();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (state.status === "loading") return <Loading />;
  if (state.status === "authenticated") return <AlreadySignedIn />;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setPending(true);
    setError(null);
    try {
      complete(
        await unwrap(
          api.POST("/api/v1/auth/login", {
            body: { email: String(form.get("email")), password: String(form.get("password")) },
          }),
        ),
      );
    } catch (err) {
      setError(err);
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="login-title" className="card mx-auto max-w-md">
      <h1 id="login-title" className="mb-4 text-2xl font-semibold">
        {t("auth.signInTitle")}
      </h1>
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
        {error !== null && <ErrorState message={t(errorKey(error))} />}
        <Field label={t("auth.email")} name="email" type="email" autoComplete="email" required />
        <Field label={t("auth.password")} name="password" type="password" autoComplete="current-password" required />
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? t("auth.signingIn") : t("auth.submitSignIn")}
        </button>
      </form>
      <p className="mt-4 text-sm">
        {t("auth.noAccount")} <Link href="/register">{t("nav.register")}</Link>
      </p>
      <p className="mt-2 text-sm">
        <Link href="/reset-password">{t("auth.forgotPassword")}</Link>
      </p>
    </section>
  );
}

/** With `#token=...` in the URL sets a new password; without it explains how to get a link. */
export function ResetPasswordForm() {
  const { t } = useI18n();
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const found = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!found) return;
    // The fragment never reaches a server; keep it out of browser history as well.
    window.history.replaceState(null, "", window.location.pathname);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(found);
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setPending(true);
    setError(null);
    try {
      await unwrap(
        api.POST("/api/v1/auth/password/reset", {
          body: { token, new_password: String(form.get("new_password")) },
        }),
      );
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="reset-title" className="card mx-auto flex max-w-md flex-col gap-4">
      <h1 id="reset-title" className="text-2xl font-semibold">
        {t("auth.resetTitle")}
      </h1>
      {done && (
        <>
          <SuccessMessage>{t("auth.resetDone")}</SuccessMessage>
          <Link href="/login">{t("nav.signIn")}</Link>
        </>
      )}
      {!done && !token && <p className="text-slate-700">{t("auth.resetHelp")}</p>}
      {!done && token && (
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
          {error !== null && <ErrorState message={t(errorKey(error))} />}
          <Field
            label={t("profile.newPassword")}
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
            required
            hint={t("auth.passwordHint")}
            error={invalidFields(error).has("new_password") ? t("errors.validation_error") : undefined}
          />
          <button type="submit" className="btn-primary" disabled={pending}>
            {t("auth.resetSubmit")}
          </button>
        </form>
      )}
    </section>
  );
}

export function RegisterForm() {
  const { t, locale } = useI18n();
  const { state } = useSession();
  const complete = useCompleteSignIn();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [consentMissing, setConsentMissing] = useState(false);

  if (state.status === "loading") return <Loading />;
  if (state.status === "authenticated") return <AlreadySignedIn />;

  const invalid = invalidFields(error);
  const fieldError = (name: string) => (invalid.has(name) ? t("errors.validation_error") : undefined);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    if (form.get("accepted_terms") === null) {
      setConsentMissing(true);
      return;
    }
    setConsentMissing(false);
    setPending(true);
    setError(null);
    try {
      complete(
        await unwrap(
          api.POST("/api/v1/auth/register", {
            body: {
              email: String(form.get("email")),
              password: String(form.get("password")),
              display_name: String(form.get("display_name")),
              preferred_locale: locale,
              accepted_terms: true,
            },
          }),
        ),
      );
    } catch (err) {
      setError(err);
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="register-title" className="card mx-auto max-w-md">
      <h1 id="register-title" className="mb-4 text-2xl font-semibold">
        {t("auth.registerTitle")}
      </h1>
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
        {error !== null && <ErrorState message={t(errorKey(error))} />}
        <Field
          label={t("auth.displayName")}
          name="display_name"
          autoComplete="nickname"
          maxLength={80}
          required
          error={fieldError("display_name")}
        />
        <Field
          label={t("auth.email")}
          name="email"
          type="email"
          autoComplete="email"
          required
          error={fieldError("email")}
        />
        <Field
          label={t("auth.password")}
          name="password"
          type="password"
          autoComplete="new-password"
          minLength={10}
          maxLength={128}
          required
          hint={t("auth.passwordHint")}
          error={fieldError("password")}
        />
        <div className="flex flex-col gap-1">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              name="accepted_terms"
              required
              className="mt-1"
              aria-invalid={consentMissing ? true : undefined}
            />
            <span>{t("auth.consent")}</span>
          </label>
          <p className="flex flex-wrap gap-x-4 text-sm">
            <Link href="/terms">{t("nav.terms")}</Link>
            <Link href="/privacy">{t("nav.privacyPolicy")}</Link>
            <Link href="/minors">{t("nav.minors")}</Link>
          </p>
          {consentMissing && <p className="text-sm text-red-700">{t("auth.consentRequired")}</p>}
        </div>
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? t("auth.creating") : t("auth.submitRegister")}
        </button>
      </form>
      <p className="mt-4 text-sm">
        {t("auth.haveAccount")} <Link href="/login">{t("nav.signIn")}</Link>
      </p>
    </section>
  );
}
