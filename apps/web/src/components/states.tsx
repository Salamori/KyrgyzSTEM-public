"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useI18n } from "@/lib/i18n/context";

export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-8 text-slate-600">
      <span aria-hidden className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-teal-700" />
      <span>{label ?? t("state.loading")}</span>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-slate-600">{children}</p>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-900">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary mt-3">
          {t("state.retry")}
        </button>
      )}
    </div>
  );
}

export function SuccessMessage({ children }: { children: ReactNode }) {
  return (
    <p role="status" aria-live="polite" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-emerald-900">
      {children}
    </p>
  );
}

export function Unauthorized() {
  const { t } = useI18n();
  return (
    <section aria-labelledby="unauth-title" className="card">
      <h1 id="unauth-title" className="text-xl font-semibold">{t("state.unauthorizedTitle")}</h1>
      <p className="mt-2 text-slate-700">{t("state.unauthorizedBody")}</p>
      <Link href="/login" className="btn-primary mt-4 inline-block">
        {t("nav.signIn")}
      </Link>
    </section>
  );
}

export function Forbidden() {
  const { t } = useI18n();
  return (
    <section aria-labelledby="forbidden-title" className="card">
      <h1 id="forbidden-title" className="text-xl font-semibold">{t("state.forbiddenTitle")}</h1>
      <p className="mt-2 text-slate-700">{t("state.forbiddenBody")}</p>
    </section>
  );
}
