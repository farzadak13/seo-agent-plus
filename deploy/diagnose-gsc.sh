#!/usr/bin/env bash
#
# What Google actually says, unedited.
#
#   sudo bash diagnose-gsc.sh
#
# The application classifies Google's errors into kinds, which is right for
# deciding whether to retry but throws away the detail when something
# unexpected happens. This prints the raw exchange: the token request, then the
# sites.list response with its status, headers and body.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VENV="/opt/seoagent/venv"
SRC="/opt/seoagent/current"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$SRC"
exec "${VENV}/bin/python" - <<'PY'
import json
import os
import sys

raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON", "")
if not raw:
    sys.exit("GSC_SERVICE_ACCOUNT_JSON is not set")
info = json.loads(raw)
print("service account:", info["client_email"])
print("project:        ", info.get("project_id", "(none in key)"))
print()

from app.gsc.credentials import ServiceAccountTokenProvider
from app.gsc.transport import EgressConfig, GoogleTransport

transport = GoogleTransport(EgressConfig(timeout_seconds=30))
provider = ServiceAccountTokenProvider(key_json=raw, session=transport.session)

print("--- minting a token ---")
try:
    token = provider.token()
except Exception as exc:
    print("FAILED:", exc)
    raise SystemExit(1)
print("ok, length", len(token))
print()

print("--- GET /webmasters/v3/sites ---")
response = transport(
    "GET",
    "/webmasters/v3/sites",
    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    json=None,
    timeout=30,
)
print("status:", response.status_code)
for name in ("content-type", "www-authenticate", "x-debug-tracking-id", "server", "alt-svc"):
    if name in response.headers:
        print(f"{name}: {response.headers[name]}")
print("body:")
print(response.text[:4000] or "(empty)")
PY
