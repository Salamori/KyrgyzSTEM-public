#!/usr/bin/env bash
# Post-deployment smoke test. Read-only except for failed logins on a random,
# non-existent @example.com address (they consume 11 of this machine's 50 per-IP login
# attempts for 15 minutes).
#
#   deploy/smoke-test.sh https://kyrgyzstem.example.org
#
# Checks: readiness through the proxy, security headers, API docs disabled in production,
# and audit F-01 (X-Forwarded-For must not let a client choose its rate-limit identity).
set -euo pipefail

BASE="${1:?usage: smoke-test.sh BASE_URL}"
BASE="${BASE%/}"
fail=0
pass() { echo "PASS  $*"; }
bad() { echo "FAIL  $*"; fail=1; }

status=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/health/ready")
[[ "$status" == 200 ]] && pass "readiness 200" || bad "readiness returned $status"

headers=$(curl -s -D - -o /dev/null "$BASE/")
for h in content-security-policy x-content-type-options x-frame-options referrer-policy; do
  grep -qi "^$h:" <<<"$headers" && pass "header $h" || bad "missing header $h"
done
if [[ "$BASE" == https://* ]]; then
  grep -qi '^strict-transport-security:' <<<"$headers" && pass "HSTS" || bad "missing HSTS"
fi

if [[ "${EXPECT_DOCS:-0}" != 1 ]]; then
  status=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/docs")
  [[ "$status" == 404 ]] && pass "API docs disabled" || bad "API docs returned $status (APP_ENV not production?)"
fi

# F-01: 11 failed logins for one address, each claiming a different client IP. The limit
# is 10 per 15 minutes per IP+email, so a 429 must appear by the 11th attempt. If every
# attempt gets 401, the client chose its own IP and the proxy is not overwriting the header.
email="smoke-$(date +%s)-$RANDOM@example.com"
got429=0
for i in $(seq 1 11); do
  # X-KS-Client-IP: the Vercel gateway setup must ignore a forged copy of its header too.
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/login" \
    -H 'Content-Type: application/json' -H "X-Forwarded-For: 203.0.113.$i" \
    -H "X-KS-Client-IP: 203.0.113.$i" \
    --data "{\"email\":\"$email\",\"password\":\"not-the-password-$i\"}")
  if [[ "$status" == 429 ]]; then got429=1; break; fi
done
[[ "$got429" == 1 ]] && pass "spoofed X-Forwarded-For ignored (429 after $i attempts)" \
  || bad "F-01: 11 logins with spoofed X-Forwarded-For were never rate-limited"

# Vercel gateway setup only (deploy/docker-compose.gateway.yml): direct callers without the
# secret must be refused, whatever client IP they claim.
#   GATEWAY_URL=https://api.example.org deploy/smoke-test.sh https://your-site.vercel.app
if [[ -n "${GATEWAY_URL:-}" ]]; then
  GATEWAY_URL="${GATEWAY_URL%/}"
  status=$(curl -s -o /dev/null -w '%{http_code}' "$GATEWAY_URL/api/v1/health/ready")
  [[ "$status" == 403 ]] && pass "gateway refuses direct requests" || bad "gateway returned $status without the secret"
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/api/v1/auth/login" \
    -H 'Content-Type: application/json' -H 'X-KS-Client-IP: 203.0.113.250' \
    -H 'X-Forwarded-For: 203.0.113.250' -H 'X-KS-Gateway-Auth: not-the-secret-not-the-secret-xx' \
    --data '{"email":"smoke@example.com","password":"x"}')
  [[ "$status" == 403 ]] && pass "gateway refuses a forged client IP without the secret" \
    || bad "gateway returned $status for a forged client IP"
fi

exit "$fail"
