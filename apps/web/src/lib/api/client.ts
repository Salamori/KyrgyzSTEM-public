import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type UserPrivate = Schemas["UserPrivate"];
export type UserAdminView = Schemas["UserAdminView"];
export type SessionInfo = Schemas["SessionInfo"];
export type Role = UserPrivate["role"];
export type UserPage = Schemas["Page_UserAdminView_"];
export type Band = Schemas["Band"];
export type Language = Schemas["Language"];
export type TopicOut = Schemas["TopicOut"];
export type ProblemSummary = Schemas["ProblemSummary"];
export type ProblemPage = Schemas["Page_ProblemSummary_"];
export type ProblemDetail = Schemas["ProblemDetail"];
export type HintsOut = Schemas["HintsOut"];
export type EditorialOut = Schemas["EditorialOut"];
export type SubmissionSummary = Schemas["SubmissionSummary"];
export type SubmissionPage = Schemas["Page_SubmissionSummary_"];
export type SubmissionDetail = Schemas["SubmissionDetail"];
export type ProgressOut = Schemas["ProgressOut"];
export type AssistantAnswer = Schemas["AssistantAnswer"];
export type AssistantStatus = Schemas["AssistantStatus"];
export type OrgOut = Schemas["OrgOut"];
export type OrgAdminView = Schemas["OrgAdminView"];
export type MemberOut = Schemas["MemberOut"];
export type OrgGroupOut = Schemas["OrgGroupOut"];
export type JoinCodeOut = Schemas["JoinCodeOut"];
export type GroupMemberOut = Schemas["GroupMemberOut"];
export type SessionOut = Schemas["SessionOut"];
export type AttendanceRow = Schemas["AttendanceRow"];
export type AttendanceStatus = Schemas["AttendanceStatus"];
export type GroupSummary = Schemas["GroupSummary"];
export type MyAttendanceRow = Schemas["MyAttendanceRow"];

export const CSRF_COOKIE = "ks_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Error carrying the API's `{error: {code, message, details}}` envelope. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (UNSAFE_METHODS.has(request.method)) {
      const token = readCookie(CSRF_COOKIE);
      if (token) request.headers.set(CSRF_HEADER, token);
    }
    return request;
  },
};

function baseUrl(): string {
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

export const api = createClient<paths>({
  baseUrl: baseUrl(),
  credentials: "same-origin",
  // Resolve fetch per call (not at import time) so it can be stubbed in tests.
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrfMiddleware);

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function isEnvelope(value: unknown): value is { error: { code: string; message: string; details?: unknown } } {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const inner = (value as { error: unknown }).error;
  return typeof inner === "object" && inner !== null && "code" in inner && "message" in inner;
}

/** Await an openapi-fetch call and return its data, or throw ApiError. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch {
    throw new ApiError(0, "network", "Network error");
  }
  if (result.response.ok) return result.data as T;
  if (isEnvelope(result.error)) {
    const { code, message, details } = result.error.error;
    throw new ApiError(result.response.status, code, message, details ?? null);
  }
  throw new ApiError(result.response.status, "generic", result.response.statusText);
}
