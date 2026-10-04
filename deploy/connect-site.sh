#!/usr/bin/env bash
#
# Connect a WordPress site: create it, store its application password
# encrypted, prove it is yours through the connector plugin, then link its
# Search Console property. Only the property of this same domain is offered:
# the shared service account can see every customer's property.
#
#   sudo bash connect-site.sh <site-url> <search-console-property> [name]
#   sudo bash connect-site.sh https://tennisino.com/ https://tennisino.com/ Tennisino
#
# The property must be spelled exactly as Google spells it (smoke.sh lists
# what the service account can see); a domain property looks like
# sc-domain:tennisino.com.
#
# The WordPress username and application password are asked for here, with
# the password not echoed, and go straight to the API over loopback. They are
# never an argument (arguments show up in `ps`), never printed, and never in
# a shell history. Needs the HoshyarSEO Connector plugin active on the site
# and SEO_AGENT_SECRET_KEYS set (secret-key.sh).
#
set -euo pipefail

RESOLVE=(--resolve "hoshyarseo.ir:443:127.0.0.1")
ENV_FILE="/etc/seoagent/env"
BASE="https://hoshyarseo.ir"
CRED_REF="GSC_SERVICE_ACCOUNT_JSON"

SITE_URL="${1:-}"
PROPERTY="${2:-}"
NAME="${3:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { echo "FAILED: $1" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the API key lives in a root-readable file)." >&2
  exit 1
fi
if [[ -z "$SITE_URL" || -z "$PROPERTY" ]]; then
  echo "Usage: sudo bash connect-site.sh <site-url> <search-console-property> [name]" >&2
  exit 1
fi
NAME="${NAME:-$(python3 -c 'import sys,urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).hostname)' "$SITE_URL")}"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
[[ -n "${SEO_AGENT_SECRET_KEYS:-}" ]] || fail "SEO_AGENT_SECRET_KEYS is not set; run secret-key.sh first"
KEY="${SEO_AGENT_TENANT_KEY:-}"
[[ -n "$KEY" ]] || fail "SEO_AGENT_TENANT_KEY is not set; run bind-tenant-key.sh first"

# The key goes to curl through a private file, not as an argument: arguments
# are visible to every user on the machine through `ps`.
AUTH_FILE="$(mktemp)"
chmod 600 "$AUTH_FILE"
trap 'rm -f "$AUTH_FILE"' EXIT
printf 'authorization: bearer %s\n' "$KEY" > "$AUTH_FILE"
AUTH="@${AUTH_FILE}"
JSON="content-type: application/json"
field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1],""))' "$1"; }
api() {
  # api METHOD PATH [body-file|-] : prints the body, fails with it on non-2xx.
  local method="$1" path="$2" data="${3:-}" out code
  out="$(mktemp)"
  if [[ -n "$data" ]]; then
    code="$(curl "${RESOLVE[@]}" -sS -o "$out" -w '%{http_code}' --max-time 60 \
      -X "$method" -H "$AUTH" -H "$JSON" --data-binary "@${data}" "${BASE}${path}")"
  else
    code="$(curl "${RESOLVE[@]}" -sS -o "$out" -w '%{http_code}' --max-time 60 \
      -X "$method" -H "$AUTH" "${BASE}${path}")"
  fi
  cat "$out"; rm -f "$out"
  [[ "$code" =~ ^2 ]] || { echo; fail "${method} ${path} returned HTTP ${code}"; }
}

say "site: ${NAME} (${SITE_URL})"
SITE="$(python3 -c 'import json,sys; print(json.dumps({"name":sys.argv[1],"base_url":sys.argv[2]}))' "$NAME" "$SITE_URL" \
  | api POST /v1/sites -)"
SITE_ID="$(field site_id <<<"$SITE")"
echo "site_id: ${SITE_ID}"

say "WordPress credentials"
read -rp  "WordPress username: " WP_USER
read -rsp "Application password (not shown): " WP_PASS
echo
[[ -n "$WP_USER" && -n "$WP_PASS" ]] || fail "both are required"

# Built from stdin, not from arguments, so the password is never in `ps`.
printf '%s\n%s' "$WP_USER" "$WP_PASS" | python3 -c '
import json, sys
user, password = sys.stdin.read().split("\n", 1)
print(json.dumps({
    "adapter_type": "hoshyarseo",
    "config": {},
    "secrets": {"username": user, "application_password": password},
}))' | api PUT "/v1/sites/${SITE_ID}/connections/site-adapter" - > /dev/null
unset WP_PASS
echo "stored, encrypted"

say "talking to the site"
CHECK="$(api POST "/v1/sites/${SITE_ID}/connections/site-adapter/check")"
python3 -c 'import json,sys; d=json.load(sys.stdin); print("ok" if d["ok"] else "NOT OK"); print(json.dumps(d["detail"], ensure_ascii=False, indent=2))' <<<"$CHECK"
python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["ok"] else 1)' <<<"$CHECK" \
  || fail "the site did not accept the connection; the message above says why"

say "proving the site is yours"
OWNED="$(api POST "/v1/sites/${SITE_ID}/ownership/verify")"
python3 -c 'import json,sys; d=json.load(sys.stdin); print("verified by", d["method"]) if d["verified"] else print("NOT verified:", "; ".join(d["reasons"]))' <<<"$OWNED"
python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["verified"] else 1)' <<<"$OWNED" \
  || fail "ownership could not be proved; use an Editor or Administrator account"

say "Search Console property: ${PROPERTY}"
python3 -c 'import json,sys; print(json.dumps({"property_url":sys.argv[1],"credential_ref":sys.argv[2],"auth_mode":"service_account"}))' \
  "$PROPERTY" "$CRED_REF" | api PUT "/v1/sites/${SITE_ID}/connections/gsc" - > /dev/null
echo "connected"

say "done"
echo "site_id ${SITE_ID}"
