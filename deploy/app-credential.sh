#!/usr/bin/env bash
#
# Install the Google service account key into the environment file.
#
#   sudo bash app-credential.sh /path/to/hoshyarseo-service-account.json
#
# The key goes in as a single line because systemd's EnvironmentFile has no
# notion of a multi-line value: a pasted JSON file would be read as one
# assignment followed by several lines of garbage, and the failure appears
# later as "invalid_grant" rather than as a parse error here.
#
# It is written single-quoted so systemd does no expansion on the contents,
# and the script reads it back and parses it before declaring success.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VAR_NAME="GSC_SERVICE_ACCOUNT_JSON"
SOURCE="${1:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ -z "$SOURCE" || ! -s "$SOURCE" ]]; then
  echo "Usage: sudo bash app-credential.sh /path/to/service-account.json" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "${ENV_FILE} does not exist. Run app-setup.sh first." >&2
  exit 1
fi

say "checking the key"
CLIENT_EMAIL="$(python3 - "$SOURCE" <<'PY'
import json, sys
info = json.load(open(sys.argv[1], encoding="utf-8"))
for field in ("type", "client_email", "private_key", "token_uri"):
    if field not in info:
        sys.exit(f"missing '{field}' — is this a service account key?")
if info["type"] != "service_account":
    sys.exit(f"type is '{info['type']}', expected 'service_account'")
print(info["client_email"])
PY
)"
echo "service account: ${CLIENT_EMAIL}"

say "writing ${VAR_NAME}"
MINIFIED="$(python3 -c "
import json,sys
print(json.dumps(json.load(open(sys.argv[1], encoding='utf-8')), separators=(',',':')))
" "$SOURCE")"

if [[ "$MINIFIED" == *"'"* ]]; then
  echo "The key contains a single quote, which this writer cannot escape." >&2
  exit 1
fi

# Replace any previous value rather than appending a second one: systemd keeps
# the last assignment, so a stale line above is harmless but confusing, and a
# stale line below would silently win.
sed -i "/^${VAR_NAME}=/d" "$ENV_FILE"
printf "%s='%s'\n" "$VAR_NAME" "$MINIFIED" >> "$ENV_FILE"
chown root:seoagent "$ENV_FILE"
chmod 640 "$ENV_FILE"

say "reading it back"
# Parse it the way the service will see it, not the way we wrote it.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
python3 -c "
import json, os, sys
raw = os.environ.get('${VAR_NAME}', '')
if not raw:
    sys.exit('the variable is not set after sourcing the env file')
info = json.loads(raw)
print('parsed ok:', info['client_email'])
print('private key length:', len(info['private_key']))
"

say "done"
cat <<EOF

The key is in ${ENV_FILE} and parses. When you connect a site, use this as
the credential reference (the NAME of the variable, never the key itself):

    ${VAR_NAME}

The customer still has to add ${CLIENT_EMAIL}
as a user on their Search Console property until OAuth verification lands.

EOF
