#!/usr/bin/env bash
#
# The first real analysis: production, real Search Console data, one URL.
#
#   sudo bash first-run.sh <property-url> <page-url> <query> [days]
#
# The property URL must be spelled exactly as Google spells it — smoke.sh
# prints the list. Configuration refuses a property Google does not report
# rather than storing it and failing on the first run.
#
# The window ends three days back, not yesterday. Search Console keeps
# revising recent days, and a window that reaches into them reads as a traffic
# drop that never happened.
#
set -euo pipefail

# Every request below goes to the public name so that nginx and the
# certificate are genuinely exercised, but --resolve pins it to the loopback
# address. Reaching your own public IP from inside the machine depends on the
# network doing hairpin NAT, and on DNS answering — neither is guaranteed, and
# when it fails it fails as a hang rather than as an error. The first run of
# this check timed out for exactly that reason while the thing it was checking
# was fine.
RESOLVE=(--resolve "hoshyarseo.ir:443:127.0.0.1")

ENV_FILE="/etc/seoagent/env"
BASE="https://hoshyarseo.ir"
CRED_REF="GSC_SERVICE_ACCOUNT_JSON"
LAG_DAYS=3
WORK="/tmp/seoagent-first-run"

PROPERTY="${1:-}"
PAGE="${2:-}"
QUERY="${3:-}"
DAYS="${4:-7}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { echo "FAILED: $1" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the API key lives in a root-readable file)." >&2
  exit 1
fi
if [[ -z "$PROPERTY" || -z "$PAGE" || -z "$QUERY" ]]; then
  echo "Usage: sudo bash first-run.sh <property-url> <page-url> <query|@file> [days]" >&2
  exit 1
fi

# A Persian query typed through a Windows console and an ssh command line
# passes through several layers that each get to decide what the bytes mean.
# '@path' reads it from a file instead, so the bytes that reach Google are the
# bytes on disk. A mangled query is not an error — it is an empty result that
# looks like the page ranks for nothing.
if [[ "$QUERY" == @* ]]; then
  QUERY_FILE="${QUERY#@}"
  [[ -s "$QUERY_FILE" ]] || fail "no such query file: ${QUERY_FILE}"
  QUERY="$(python3 -c '
import sys
print(open(sys.argv[1], encoding="utf-8").read().strip())' "$QUERY_FILE")"
fi
printf "query: %s\n" "$QUERY"
python3 -c '
import sys
q = sys.argv[1]
print("        codepoints:", " ".join(f"U+{ord(c):04X}" for c in q[:12]), "..." if len(q) > 12 else "")' "$QUERY"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
# Prefer a real tenant's key over the admin identity, so what these scripts
# create belongs to the customer rather than to the operator. bind-tenant-key.sh
# puts one here.
KEY="${SEO_AGENT_TENANT_KEY:-${SEO_AGENT_API_KEY:-}}"
[[ -n "$KEY" ]] || fail "neither SEO_AGENT_TENANT_KEY nor SEO_AGENT_API_KEY is set"
[[ -n "${SEO_AGENT_TENANT_KEY:-}" ]] \
  && echo "acting as a tenant key" \
  || echo "acting as the admin key (run bind-tenant-key.sh to use a tenant)"

AUTH="authorization: bearer ${KEY}"
JSON="content-type: application/json"
mkdir -p "$WORK"

END="$(date -u -d "${LAG_DAYS} days ago" +%F)"
START="$(date -u -d "$((LAG_DAYS + DAYS - 1)) days ago" +%F)"

# Request bodies are built by python rather than by string interpolation: the
# query is Persian and will contain characters a shell quotes badly.
body() { python3 -c 'import json,sys; print(json.dumps(json.loads(sys.stdin.read())))'; }
field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1],""))' "$1"; }

say "site"
SITE="$(python3 -c 'import json,sys; print(json.dumps({"name":"PAMA","base_url":sys.argv[1]}))' "$PROPERTY" \
  | curl "${RESOLVE[@]}" -fsS --max-time 15 "${BASE}/v1/sites" -X POST -H "$AUTH" -H "$JSON" --data-binary @-)" \
  || fail "could not create a site"
SITE_ID="$(field site_id <<<"$SITE")"
echo "site_id: ${SITE_ID}"

say "connecting the property"
python3 -c 'import json,sys; print(json.dumps({"property_url":sys.argv[1],"credential_ref":sys.argv[2],"auth_mode":"service_account"}))' \
  "$PROPERTY" "$CRED_REF" > "${WORK}/connect.json"
CODE="$(curl "${RESOLVE[@]}" -sS -o "${WORK}/connect-response.json" -w '%{http_code}' --max-time 45 \
        "${BASE}/v1/sites/${SITE_ID}/connections/gsc" \
        -X PUT -H "$AUTH" -H "$JSON" --data-binary "@${WORK}/connect.json")"
if [[ "$CODE" != "200" ]]; then
  cat "${WORK}/connect-response.json"
  echo
  fail "the property was refused (HTTP ${CODE}); the response above says what Google reports"
fi
echo "connected: $(field gsc_property_url < "${WORK}/connect-response.json")"

say "window"
echo "  current  ${START} .. ${END}"
echo "  baseline is the ${DAYS} days before that, chosen by the engine"

say "starting the run"
python3 -c 'import json,sys; print(json.dumps({"start_date":sys.argv[1],"end_date":sys.argv[2],"normalized_url":sys.argv[3],"normalized_query":sys.argv[4],"candidate_id":"first-real-run"}))' \
  "$START" "$END" "$PAGE" "$QUERY" > "${WORK}/run.json"
RUN="$(curl "${RESOLVE[@]}" -fsS --max-time 20 "${BASE}/v1/sites/${SITE_ID}/runs" -X POST -H "$AUTH" -H "$JSON" \
       --data-binary "@${WORK}/run.json")" || fail "could not create a run"
RUN_ID="$(field run_id <<<"$RUN")"
echo "run_id: ${RUN_ID}"

say "waiting for the worker"
for _ in $(seq 1 60); do
  curl "${RESOLVE[@]}" -fsS --max-time 10 "${BASE}/v1/runs/${RUN_ID}" -H "$AUTH" > "${WORK}/current.json" || true
  STATUS="$(field status < "${WORK}/current.json" 2>/dev/null || echo "")"
  case "$STATUS" in
    succeeded|failed|cancelled) break ;;
  esac
  printf "."
  sleep 3
done
echo

say "result"
python3 - "${WORK}/current.json" <<'PY'
import json, sys

run = json.load(open(sys.argv[1], encoding="utf-8"))
print("status:", run.get("status"))
if run.get("error"):
    print("error:", run["error"])
result = run.get("result")
if not result:
    print("(no result payload)")
    raise SystemExit(0)
text = json.dumps(result, ensure_ascii=False, indent=2)
print(text[:5000])
if len(text) > 5000:
    print(f"... truncated, {len(text)} characters total")
PY

say "done"
echo "site_id ${SITE_ID}, run_id ${RUN_ID}"
# The API key is deliberately not printed. An earlier version echoed a ready
# made curl command with the header filled in; it was pasted into a chat
# window within the minute, and the key had to be rotated. A convenience that
# puts a credential on someone's screen is not a convenience.
cat <<'HINT'

For the full result, on this server — with the same key this run used, not
the admin one, which owns none of this and would be refused:
    sudo bash -c 'source /etc/seoagent/env; curl -sS \
      --resolve hoshyarseo.ir:443:127.0.0.1 \
      -H "authorization: bearer ${SEO_AGENT_TENANT_KEY:-$SEO_AGENT_API_KEY}" \
      https://hoshyarseo.ir/v1/runs/RUN_ID' | python3 -m json.tool
HINT
