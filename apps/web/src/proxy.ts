import { NextResponse, type NextRequest } from "next/server";

import { decideGateway } from "@/lib/apiGateway";

// Runs before next.config.ts rewrites. Without API_GATEWAY_URL it does nothing and the
// existing `/api/*` → API_INTERNAL_URL rewrite applies (local, Docker, self-hosted).
export function proxy(request: NextRequest) {
  const env = {
    API_GATEWAY_URL: process.env.API_GATEWAY_URL,
    API_GATEWAY_SECRET: process.env.API_GATEWAY_SECRET,
    VERCEL: process.env.VERCEL,
  };
  const decision = decideGateway(env, request.nextUrl, request.headers);
  if (decision.kind === "local") return NextResponse.next();
  if (decision.kind === "refuse") {
    return NextResponse.json(
      { error: { code: "service_unavailable", message: "The service is temporarily unavailable.", details: null } },
      { status: 503 },
    );
  }
  return NextResponse.rewrite(decision.url, { request: { headers: decision.headers } });
}

export const config = { matcher: "/api/:path*" };
