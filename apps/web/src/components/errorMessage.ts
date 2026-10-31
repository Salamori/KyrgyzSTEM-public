import { ApiError } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";

const KNOWN = new Set<string>([
  "invalid_credentials",
  "email_taken",
  "account_disabled",
  "rate_limited",
  "validation_error",
  "csrf_failed",
  "not_authenticated",
  "forbidden",
  "cannot_modify_self",
  "not_found",
  "network",
  "hint_out_of_order",
  "editorial_locked",
  "source_too_large",
  "publish_blocked",
  "reviewer_is_author",
  "not_published",
  "assistant_level_locked",
  "invalid_join_code",
  "last_org_admin",
  "not_a_member",
  "author_attestation_required",
  "invalid_reset_token",
  "admin_cannot_delete_self",
]);

/** Map an API error to a localized message key (never shows raw server text). */
export function errorKey(error: unknown): MessageKey {
  if (error instanceof ApiError && KNOWN.has(error.code)) return `errors.${error.code}` as MessageKey;
  return "errors.generic";
}

/** Field names that failed server validation, from a 422 envelope. */
export function invalidFields(error: unknown): Set<string> {
  const fields = new Set<string>();
  if (error instanceof ApiError && error.code === "validation_error" && Array.isArray(error.details)) {
    for (const item of error.details as { loc?: unknown[] }[]) {
      const last = item.loc?.[item.loc.length - 1];
      if (typeof last === "string") fields.add(last);
    }
  }
  return fields;
}
