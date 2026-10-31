import { describe, expect, it } from "vitest";

import { CLIENT_IP_HEADER, GATEWAY_AUTH_HEADER, decideGateway, type GatewayEnv } from "./apiGateway";

const SECRET = "a".repeat(20) + "B9_-" + "c".repeat(20);
const ON_VERCEL: GatewayEnv = { API_GATEWAY_URL: "https://api.example.org", API_GATEWAY_SECRET: SECRET, VERCEL: "1" };
const URL_IN = new URL("https://preview.example.app/api/v1/problems?locale=ru&limit=20");

function headers(init: Record<string, string> = {}) {
  return new Headers({ "x-forwarded-for": "203.0.113.7", cookie: "ks_session=s; ks_csrf=c", ...init });
}

describe("decideGateway", () => {
  it("leaves local and Docker setups alone when no gateway is configured", () => {
    expect(decideGateway({}, URL_IN, headers({ [CLIENT_IP_HEADER]: "198.51.100.1" }))).toEqual({ kind: "local" });
    expect(decideGateway({ VERCEL: "1" }, URL_IN, headers())).toEqual({ kind: "local" });
  });

  it("forwards to the gateway with the Vercel client IP and the secret", () => {
    const d = decideGateway(ON_VERCEL, URL_IN, headers({ "x-csrf-token": "c", "content-type": "application/json" }));
    expect(d.kind).toBe("forward");
    if (d.kind !== "forward") return;
    expect(d.url.toString()).toBe("https://api.example.org/api/v1/problems?locale=ru&limit=20");
    expect(d.headers.get(CLIENT_IP_HEADER)).toBe("203.0.113.7");
    expect(d.headers.get(GATEWAY_AUTH_HEADER)).toBe(SECRET);
    expect(d.headers.get("cookie")).toBe("ks_session=s; ks_csrf=c");
    expect(d.headers.get("x-csrf-token")).toBe("c");
    expect(d.headers.get("content-type")).toBe("application/json");
  });

  it("replaces forged client IP and gateway headers sent by the browser", () => {
    const d = decideGateway(
      ON_VERCEL,
      URL_IN,
      headers({
        [CLIENT_IP_HEADER]: "198.51.100.1",
        "X-KS-Gateway-Auth": "guessed-secret-guessed-secret-guessed",
        "x-ks-anything": "x",
      }),
    );
    expect(d.kind).toBe("forward");
    if (d.kind !== "forward") return;
    expect(d.headers.get(CLIENT_IP_HEADER)).toBe("203.0.113.7");
    expect(d.headers.get(GATEWAY_AUTH_HEADER)).toBe(SECRET);
    expect(d.headers.get("x-ks-anything")).toBeNull();
  });

  it("accepts an IPv6 client address", () => {
    const d = decideGateway(ON_VERCEL, URL_IN, headers({ "x-forwarded-for": "2001:db8::42" }));
    expect(d.kind === "forward" && d.headers.get(CLIENT_IP_HEADER)).toBe("2001:db8::42");
  });

  it.each([
    ["outside Vercel", { ...ON_VERCEL, VERCEL: undefined }],
    ["plain http gateway", { ...ON_VERCEL, API_GATEWAY_URL: "http://api.example.org" }],
    ["gateway URL with a path", { ...ON_VERCEL, API_GATEWAY_URL: "https://api.example.org/base" }],
    ["gateway URL with credentials", { ...ON_VERCEL, API_GATEWAY_URL: "https://u:p@api.example.org" }],
    ["unparsable gateway URL", { ...ON_VERCEL, API_GATEWAY_URL: "not a url" }],
    ["missing secret", { ...ON_VERCEL, API_GATEWAY_SECRET: undefined }],
    ["short secret", { ...ON_VERCEL, API_GATEWAY_SECRET: "short" }],
    ["secret with a wildcard", { ...ON_VERCEL, API_GATEWAY_SECRET: "*".repeat(40) }],
  ])("refuses when misconfigured: %s", (_name, env) => {
    expect(decideGateway(env, URL_IN, headers()).kind).toBe("refuse");
  });

  it.each([
    ["missing", undefined],
    ["a list of addresses", "203.0.113.7, 198.51.100.1"],
    ["not an IP", "evil"],
  ])("refuses when the client IP is %s", (_name, value) => {
    const h = headers();
    if (value === undefined) h.delete("x-forwarded-for");
    else h.set("x-forwarded-for", value);
    expect(decideGateway(ON_VERCEL, URL_IN, h).kind).toBe("refuse");
  });
});
