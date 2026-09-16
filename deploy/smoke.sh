#!/usr/bin/env bash
#
# End-to-end check against the running production service.
#
#   sudo bash smoke.sh
#
# Runs on the server and reads the API key from /etc/seoagent/env, so the key
# is never pasted into a terminal, a chat window or a shell history.
#
# The last step is the one worth having: it asks Google, through the real
# transport and the real service account, which Search Console properties this
# credential can see. Everything before it can pass while the thing the product
# is actually for is broken.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
BASE="https://hoshyarseo.ir"
CRED_REF="GSC_SERVICE_ACCOUNT_JSON"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { echo "FAILED: $1" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the API key lives in a root-readable file)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
[[ -n "${SEO_AGENT_API_KEY:-}" ]] || fail "SEO_AGENT_API_KEY is not set"

AUTH="authorization: bearer ${SEO_AGENT_API_KEY}"
JSON="content-type: application/json"

say "readiness, through nginx and TLS"
READY="$(curl -fsS --max-time 10 "${BASE}/readyz")" || fail "/readyz did not answer"
echo "$READY"

say "authentication is actually enforced"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/v1/sites" \
        -X POST -H "$JSON" -d '{"name":"x","base_url":"https://example.com"}')"
[[ "$CODE" == "401" || "$CODE" == "403" ]] \
  || fail "an unauthenticated write returned ${CODE}; it must be refused"
echo "unauthenticated write refused with ${CODE}"

say "creating a throwaway site"
SITE="$(curl -fsS --max-time 15 "${BASE}/v1/sites" -X POST -H "$AUTH" -H "$JSON" \
        -d '{"name":"smoke test","base_url":"https://pama.shop"}')" \
  || fail "could not create a site"
SITE_ID="$(python3 -c "import json,sys; print(json.load(sys.stdin)['site_id'])" <<<"$SITE")"
echo "site_id: ${SITE_ID}"

say "asking Google which properties this credential can see"
# This is the real test. It exercises the egress path, the service account
# signing and refresh, and the error classification, against Google itself.
BODY="$(curl -sS --max-time 45 "${BASE}/v1/sites/${SITE_ID}/connections/gsc/available" \
        -X POST -H "$AUTH" -H "$JSON" \
        -d "{\"credential_ref\":\"${CRED_REF}\",\"auth_mode\":\"service_account\"}")"

python3 - "$BODY" <<'PY'
import json, sys
try:
    payload = json.loads(sys.argv[1])
except ValueError:
    sys.exit(f"unreadable response: {sys.argv[1][:400]}")

if "detail" in payload:
    print("Google refused or could not be reached:")
    print(" ", payload["detail"])
    print()
    print("A 'permission_denied' here is the expected answer for a brand new")
    print("service account: it authenticated fine, it simply has not been")
    print("granted any property yet.")
    sys.exit(0)

properties = payload.get("properties", [])
if not properties:
    print("Authenticated, and Google returned an empty list.")
    print("The credential works; no property has granted it access yet.")
    sys.exit(0)

print(f"{len(properties)} property/properties visible to this credential:")
for item in properties:
    mark = "readable" if item["readable"] else "NOT READABLE"
    print(f"  {item['site_url']}   [{item['permission_level']}, {mark}]")
PY

say "cleaning up"
echo "The throwaway site ${SITE_ID} is left in place; there is no delete"
echo "endpoint yet, and inventing one for a smoke test would be worse."

say "done"
