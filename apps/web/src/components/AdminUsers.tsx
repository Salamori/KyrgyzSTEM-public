"use client";

import { useCallback, useEffect, useId, useState } from "react";

import { api, unwrap, type Role, type UserAdminView, type UserPage } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";

import { errorKey } from "./errorMessage";
import { EmptyState, ErrorState, Forbidden, Loading, SuccessMessage, Unauthorized } from "./states";

const ROLES: Role[] = ["student", "teacher", "admin"];
const PAGE_SIZE = 20;

type Load =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; page: UserPage };

type UserUpdate = { role?: Role; is_active?: boolean };

export function AdminUsers() {
  const { state } = useSession();
  if (state.status === "loading") return <Loading />;
  if (state.status !== "authenticated") return <Unauthorized />;
  if (state.user.role !== "admin") return <Forbidden />;
  return <UserDirectory currentUserId={state.user.id} />;
}

function UserDirectory({ currentUserId }: { currentUserId: string }) {
  const { t } = useI18n();
  const searchId = useId();
  const roleId = useId();
  const resetId = useId();
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [offset, setOffset] = useState(0);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [notice, setNotice] = useState<{ ok: true } | { ok: false; error: unknown } | null>(null);
  const [resetLink, setResetLink] = useState<{ name: string; url: string } | null>(null);

  const fetchPage = useCallback(async () => {
    setLoad({ status: "loading" });
    try {
      const page = await unwrap(
        api.GET("/api/v1/admin/users", {
          params: {
            query: { q: query || undefined, role: role || undefined, limit: PAGE_SIZE, offset },
          },
        }),
      );
      setLoad({ status: "ready", page });
    } catch (error) {
      setLoad({ status: "error", error });
    }
  }, [query, role, offset]);

  useEffect(() => {
    const timer = setTimeout(() => void fetchPage(), 250); // debounce typing
    return () => clearTimeout(timer);
  }, [fetchPage]);

  async function issueReset(user: UserAdminView) {
    setNotice(null);
    setResetLink(null);
    try {
      const issued = await unwrap(
        api.POST("/api/v1/admin/users/{user_id}/password-reset", {
          params: { path: { user_id: user.id } },
        }),
      );
      setResetLink({
        name: user.display_name,
        url: `${window.location.origin}/reset-password#token=${issued.token}`,
      });
    } catch (error) {
      setNotice({ ok: false, error });
    }
  }

  async function update(user: UserAdminView, body: UserUpdate) {
    setNotice(null);
    try {
      const updated = await unwrap(
        api.PATCH("/api/v1/admin/users/{user_id}", {
          params: { path: { user_id: user.id } },
          body,
        }),
      );
      setLoad((prev) =>
        prev.status === "ready"
          ? {
              ...prev,
              page: {
                ...prev.page,
                items: prev.page.items.map((u) => (u.id === updated.id ? updated : u)),
              },
            }
          : prev,
      );
      setNotice({ ok: true });
    } catch (error) {
      setNotice({ ok: false, error });
    }
  }

  return (
    <section aria-labelledby="users-title" className="flex flex-col gap-4">
      <h1 id="users-title" className="text-2xl font-semibold">
        {t("admin.usersTitle")}
      </h1>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor={searchId} className="font-medium">
            {t("admin.search")}
          </label>
          <input
            id={searchId}
            type="search"
            className="input"
            value={query}
            maxLength={100}
            onChange={(e) => {
              setQuery(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={roleId} className="font-medium">
            {t("admin.roleFilter")}
          </label>
          <select
            id={roleId}
            className="input"
            value={role}
            onChange={(e) => {
              setRole(e.target.value as Role | "");
              setOffset(0);
            }}
          >
            <option value="">{t("admin.allRoles")}</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(`roles.${r}` as MessageKey)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {notice?.ok === true && <SuccessMessage>{t("admin.updated")}</SuccessMessage>}
      {notice?.ok === false && <ErrorState message={t(errorKey(notice.error))} />}
      {resetLink && (
        <div className="card flex flex-col gap-2">
          <label htmlFor={resetId} className="font-medium">
            {t("admin.resetLinkFor", { name: resetLink.name })}
          </label>
          <input id={resetId} className="input" readOnly value={resetLink.url} />
          <p className="text-sm text-slate-600">{t("admin.resetLinkHelp")}</p>
        </div>
      )}

      {load.status === "loading" && <Loading />}
      {load.status === "error" && (
        <ErrorState message={t(errorKey(load.error))} onRetry={() => void fetchPage()} />
      )}
      {load.status === "ready" && load.page.items.length === 0 && (
        <EmptyState>{t("admin.empty")}</EmptyState>
      )}
      {load.status === "ready" && load.page.items.length > 0 && (
        <>
          <ul className="flex flex-col gap-3">
            {load.page.items.map((user) => (
              <UserRow
                key={user.id}
                user={user}
                isSelf={user.id === currentUserId}
                onUpdate={(body) => void update(user, body)}
                onReset={() => void issueReset(user)}
              />
            ))}
          </ul>
          <Pagination
            offset={offset}
            total={load.page.total}
            count={load.page.items.length}
            onChange={setOffset}
          />
        </>
      )}
    </section>
  );
}

function UserRow({
  user,
  isSelf,
  onUpdate,
  onReset,
}: {
  user: UserAdminView;
  isSelf: boolean;
  onUpdate: (body: UserUpdate) => void;
  onReset: () => void;
}) {
  const { t } = useI18n();
  const selectId = useId();
  const statusClass = user.is_active
    ? "bg-emerald-100 text-emerald-900"
    : "bg-slate-200 text-slate-800";
  return (
    <li className="card flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="font-medium">
          {user.display_name}{" "}
          {isSelf && <span className="text-slate-600">{t("admin.you")}</span>}
        </p>
        <p className="break-all text-sm text-slate-600">{user.email}</p>
        <p className="mt-1 flex flex-wrap gap-2 text-xs">
          <span className={`rounded px-2 py-0.5 ${statusClass}`}>
            {user.is_active ? t("admin.active") : t("admin.inactive")}
          </span>
          {user.is_synthetic && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-900">
              {t("admin.synthetic")}
            </span>
          )}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={selectId} className="sr-only">
          {t("admin.changeRole", { name: user.display_name })}
        </label>
        <select
          id={selectId}
          className="rounded-md border border-slate-400 bg-surface px-2 py-1.5"
          value={user.role}
          disabled={isSelf}
          onChange={(e) => onUpdate({ role: e.target.value as Role })}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {t(`roles.${r}` as MessageKey)}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn-secondary"
          disabled={isSelf}
          onClick={() => onUpdate({ is_active: !user.is_active })}
        >
          {user.is_active ? t("admin.deactivate") : t("admin.reactivate")}
        </button>
        {user.role !== "admin" && user.is_active && (
          <button type="button" className="btn-secondary" onClick={onReset}>
            {t("admin.resetLink")}
          </button>
        )}
      </div>
    </li>
  );
}

function Pagination({
  offset,
  total,
  count,
  onChange,
}: {
  offset: number;
  total: number;
  count: number;
  onChange: (offset: number) => void;
}) {
  const { t } = useI18n();
  return (
    <nav aria-label={t("admin.pagination")} className="flex items-center justify-between gap-3 text-sm">
      <button
        type="button"
        className="btn-secondary"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}
      >
        {t("admin.prev")}
      </button>
      <span>{t("admin.showing", { from: offset + 1, to: offset + count, total })}</span>
      <button
        type="button"
        className="btn-secondary"
        disabled={offset + count >= total}
        onClick={() => onChange(offset + PAGE_SIZE)}
      >
        {t("admin.next")}
      </button>
    </nav>
  );
}
