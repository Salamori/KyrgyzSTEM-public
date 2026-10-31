import { isIP } from "node:net";

// Vercel → API gateway (docs/DEPLOYMENT.md, "Vercel frontend with an API gateway").
// On Vercel there is no reachable `api` host, so `/api/*` is forwarded to a Caddy gateway
// in front of the API. The gateway accepts only requests that carry the shared secret, and
// takes the client's address from CLIENT_IP_HEADER, which only this code sets: from the
// `x-forwarded-for` that Vercel itself overwrites with the client's public IP. Without
// API_GATEWAY_URL nothing changes and next.config.ts keeps proxying to API_INTERNAL_URL.

export const GATEWAY_AUTH_HEADER = "x-ks-gateway-auth";
export const CLIENT_IP_HEADER = "x-ks-client-ip";
const RESERVED_PREFIX = "x-ks-";
// Letters, digits, '-' and '_' only: Caddy's header matcher treats '*' as a wildcard.
const SECRET_PATTERN = /^[A-Za-z0-9_-]{32,}$/;

export interface GatewayEnv {
  API_GATEWAY_URL?: string;
  API_GATEWAY_SECRET?: string;
  VERCEL?: string;
}

export type GatewayDecision =
  | { kind: "local" }
  | { kind: "forward"; url: URL; headers: Headers }
  | { kind: "refuse"; reason: string };

/** Decide how an `/api/*` request leaves the web app. Never throws; refuses when unsure. */
export function decideGateway(env: GatewayEnv, requestUrl: URL, incoming: Headers): GatewayDecision {
  if (!env.API_GATEWAY_URL) return { kind: "local" };
  // Only Vercel guarantees that x-forwarded-for is the platform-observed client address.
  if (env.VERCEL !== "1") return { kind: "refuse", reason: "gateway outside Vercel" };

  let base: URL;
  try {
    base = new URL(env.API_GATEWAY_URL);
  } catch {
    return { kind: "refuse", reason: "invalid gateway URL" };
  }
  if (base.protocol !== "https:" || base.username || base.password || base.pathname !== "/" || base.search) {
    return { kind: "refuse", reason: "gateway URL must be a bare https origin" };
  }
  const secret = env.API_GATEWAY_SECRET ?? "";
  if (!SECRET_PATTERN.test(secret)) return { kind: "refuse", reason: "missing or weak gateway secret" };

  const clientIp = (incoming.get("x-forwarded-for") ?? "").trim();
  if (!clientIp || isIP(clientIp) === 0) return { kind: "refuse", reason: "no single client IP" };

  const headers = new Headers(incoming);
  for (const name of [...headers.keys()]) {
    if (name.startsWith(RESERVED_PREFIX)) headers.delete(name);
  }
  headers.set(CLIENT_IP_HEADER, clientIp);
  headers.set(GATEWAY_AUTH_HEADER, secret);

  const url = new URL(requestUrl.pathname + requestUrl.search, base);
  return { kind: "forward", url, headers };
}
