import { describe, expect, it, vi } from "vitest";

import { mockApi } from "@/test/utils";

import { api, ApiError, CSRF_HEADER, unwrap } from "./client";

describe("unwrap", () => {
  it("returns data for 2xx", async () => {
    mockApi({ "GET /api/v1/health": { status: 200, body: { status: "ok", database: "ok" } } });
    await expect(unwrap(api.GET("/api/v1/health"))).resolves.toEqual({
      status: "ok",
      database: "ok",
    });
  });

  it("maps the error envelope to ApiError", async () => {
    mockApi({
      "POST /api/v1/auth/login": {
        status: 401,
        body: { error: { code: "invalid_credentials", message: "nope", details: null } },
      },
    });
    const err = await unwrap(
      api.POST("/api/v1/auth/login", { body: { email: "a@example.com", password: "x" } }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 401, code: "invalid_credentials" });
  });

  it("maps network failures to code 'network'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(unwrap(api.GET("/api/v1/health"))).rejects.toMatchObject({
      status: 0,
      code: "network",
    });
  });
});

describe("CSRF header", () => {
  it("is sent on unsafe methods from the ks_csrf cookie, never on GET", async () => {
    document.cookie = "ks_csrf=token-123; path=/";
    const fetchMock = mockApi({
      "GET /api/v1/users/me": { status: 200, body: {} },
      "PATCH /api/v1/users/me": { status: 200, body: {} },
    });

    await api.GET("/api/v1/users/me");
    await api.PATCH("/api/v1/users/me", { body: { display_name: "A" } });

    const [getReq, patchReq] = fetchMock.mock.calls.map(([req]) => req as Request);
    expect(getReq?.headers.get(CSRF_HEADER)).toBeNull();
    expect(patchReq?.headers.get(CSRF_HEADER)).toBe("token-123");
  });
});
