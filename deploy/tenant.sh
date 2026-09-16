#!/usr/bin/env bash
#
# Tenant administration, with the environment already loaded.
#
#   sudo bash tenant.sh list
#   sudo bash tenant.sh create --name "PAMA"
#   sudo bash tenant.sh issue-key --tenant t-xxxx --label "server"
#   sudo bash tenant.sh revoke-key --key-id abc123
#
# A thin wrapper over `python -m app.tenants`. It exists so the database
# password never has to appear on a command line: the CLI needs
# SEO_AGENT_DATABASE_DSN, and the alternative is pasting it, which puts it in
# the shell history of two machines and in the terminal scrollback of one.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
APP_USER="seoagent"
VENV="/opt/seoagent/venv"
SRC="/opt/seoagent/current"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the database password lives in a root-readable file)." >&2
  exit 1
fi
for path in "$ENV_FILE" "${VENV}/bin/python" "${SRC}/app/tenants.py"; do
  if [[ ! -e "$path" ]]; then
    echo "Missing ${path}. Deploy a release that includes the tenancy code first." >&2
    exit 1
  fi
done

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$SRC"
exec sudo -u "$APP_USER" \
  env SEO_AGENT_DATABASE_DSN="$SEO_AGENT_DATABASE_DSN" \
  "${VENV}/bin/python" -m app.tenants "$@"
