"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { Forbidden, Loading, Unauthorized } from "../states";

const TABS: { href: string; label: MessageKey }[] = [
  { href: "/admin/users", label: "admin.usersTitle" },
  { href: "/admin/problems", label: "admin.contentTitle" },
  { href: "/admin/orgs", label: "admin.orgsTitle" },
  { href: "/admin/audit", label: "admin.auditTitle" },
];

/**
 * Admin-only frame with section tabs. This guard only shapes the UI: every admin
 * endpoint enforces authorization on the server.
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { state } = useSession();
  const pathname = usePathname();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  if (state.user.role !== "admin") return <Forbidden />;
  return (
    <div className="flex flex-col gap-4">
      <nav aria-label={t("nav.adminArea")} className="flex flex-wrap gap-2 border-b border-slate-200 pb-2 text-sm">
        {TABS.map((tab) => {
          const current = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              aria-current={current ? "page" : undefined}
              className={`rounded-md px-3 py-1.5 no-underline ${current ? "bg-teal-800 text-white hover:no-underline" : "hover:bg-slate-100"}`}
            >
              {t(tab.label)}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}
