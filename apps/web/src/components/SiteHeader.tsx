"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { LanguageSwitcher } from "./LanguageSwitcher";

function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname();
  const current = pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));
  return (
    <Link
      href={href}
      aria-current={current ? "page" : undefined}
      className={current ? "font-medium !text-slate-900" : undefined}
    >
      {children}
    </Link>
  );
}

export function SiteHeader() {
  const { t } = useI18n();
  const { state, signOut } = useSession();
  const router = useRouter();

  async function onSignOut() {
    await signOut();
    router.push("/");
  }

  return (
    <header className="glass-nav sticky top-0 z-40">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <Link href="/" className="text-base font-semibold tracking-tight text-slate-950 no-underline hover:no-underline">
          {t("app.name")}
        </Link>
        <nav aria-label={t("nav.menu")} className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm [&_a]:text-slate-600 [&_a]:no-underline [&_a:hover]:text-slate-900">
          <NavLink href="/problems">{t("nav.problems")}</NavLink>
          <NavLink href="/topics">{t("nav.topics")}</NavLink>
          <NavLink href="/about">{t("nav.about")}</NavLink>
          {state.status === "authenticated" ? (
            <>
              <NavLink href="/dashboard">{t("nav.dashboard")}</NavLink>
              <NavLink href="/orgs">{t("nav.clubs")}</NavLink>
              <NavLink href="/profile">{t("nav.profile")}</NavLink>
              {state.user.role === "admin" && <NavLink href="/admin">{t("nav.adminArea")}</NavLink>}
              <button
                type="button"
                onClick={() => void onSignOut()}
                className="text-slate-600 transition-colors hover:text-slate-900"
              >
                {t("nav.signOut")}
              </button>
            </>
          ) : state.status === "anonymous" ? (
            <>
              <NavLink href="/login">{t("nav.signIn")}</NavLink>
              <NavLink href="/register">{t("nav.register")}</NavLink>
            </>
          ) : null}
          <LanguageSwitcher />
        </nav>
      </div>
    </header>
  );
}
