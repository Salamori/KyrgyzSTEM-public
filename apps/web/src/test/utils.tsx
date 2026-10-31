import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import { Providers } from "@/components/Providers";
import type { UserPrivate } from "@/lib/api/client";
import type { Locale } from "@/lib/i18n";

type Route = { status: number; body?: unknown };
export type Routes = Record<string, Route | ((req: Request) => Route)>;

/** Stub fetch with "METHOD /path" → response entries. Unknown routes return 404. */
export function mockApi(routes: Routes) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const req = input instanceof Request ? input : new Request(input);
    const url = new URL(req.url);
    const entry = routes[`${req.method} ${url.pathname}`];
    const route = typeof entry === "function" ? entry(req) : entry;
    const { status, body } = route ?? {
      status: 404,
      body: { error: { code: "not_found", message: "nf", details: null } },
    };
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const anonymous: Route = {
  status: 401,
  body: { error: { code: "not_authenticated", message: "x", details: null } },
};

export function makeUser(overrides: Partial<UserPrivate> = {}): UserPrivate {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    email: "student@example.com",
    display_name: "[Synthetic] Student",
    role: "student",
    preferred_locale: "en",
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

export function sessionFor(user: UserPrivate): Route {
  return {
    status: 200,
    body: { user, csrf_token: "csrf", expires_at: "2026-11-01T00:00:00Z" },
  };
}

export function renderWithProviders(ui: ReactElement, locale: Locale = "en") {
  return render(<Providers locale={locale}>{ui}</Providers>);
}
