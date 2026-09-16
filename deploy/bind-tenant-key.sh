#!/usr/bin/env bash
#
# Give the server-side checks a key of their own, belonging to a real tenant.
#
#   sudo bash bind-tenant-key.sh t-xxxxxxxx
#
# smoke.sh and first-run.sh were using SEO_AGENT_API_KEY, which authenticates
# as the tenant "admin". Anything they create belongs to admin rather than to
# the customer, which is not what either script is meant to be exercising.
#
# The obvious fix — issue a key, read it, paste it into the env file — puts the
# key on a screen and in two shell histories. So the key is issued and stored
# without ever being printed: this script captures it straight from the CLI.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VAR_NAME="SEO_AGENT_TENANT_KEY"
TENANT="${1:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ -z "$TENANT" ]]; then
  echo "Usage: sudo bash bind-tenant-key.sh <tenant-id>" >&2
  echo "       (sudo bash tenant.sh list  shows the ids)" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say "issuing a key for ${TENANT}"
TOKEN="$(bash "${HERE}/tenant.sh" issue-key --tenant "$TENANT" --label "server checks" --bare)"
if [[ -z "$TOKEN" || "$TOKEN" != seo_* ]]; then
  echo "The CLI did not return a key. Nothing was written." >&2
  exit 1
fi
echo "issued (not shown)"

say "storing it in ${ENV_FILE}"
sed -i "/^${VAR_NAME}=/d" "$ENV_FILE"
printf "%s=%s\n" "$VAR_NAME" "$TOKEN" >> "$ENV_FILE"
chown root:seoagent "$ENV_FILE"
chmod 640 "$ENV_FILE"
unset TOKEN

say "checking it works"
# Read it back the way the scripts will, and use it against the running
# service. Writing a key that does not authenticate would look like success
# and fail later as a puzzling 401.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
CODE="$(curl --resolve "hoshyarseo.ir:443:127.0.0.1" -s -o /dev/null -w '%{http_code}' \
        --max-time 15 "https://hoshyarseo.ir/v1/sites" -X POST \
        -H "authorization: bearer ${SEO_AGENT_TENANT_KEY}" \
        -H 'content-type: application/json' \
        -d '{"name":"key check","base_url":"https://example.com"}')"
if [[ "$CODE" != "201" ]]; then
  echo "The new key was refused (HTTP ${CODE})." >&2
  exit 1
fi
echo "authenticated as the tenant (HTTP ${CODE})"

say "done"
echo "smoke.sh and first-run.sh will now act as ${TENANT} rather than admin."
