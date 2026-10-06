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

# Only the DSN is read from the env file, and it reaches Python through the
# environment, never as an argument: `sudo ... env NAME=value` put the
# database password in the argv of a process that stays alive for the whole
# run, readable by any local user through `ps`. runuser passes the
# environment on unchanged and needs no sudo rules.
SEO_AGENT_DATABASE_DSN="$(sed -n 's/^SEO_AGENT_DATABASE_DSN=//p' "$ENV_FILE" | head -n1)"
SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN%\"}"; SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN#\"}"
[[ -n "$SEO_AGENT_DATABASE_DSN" ]] || { echo "SEO_AGENT_DATABASE_DSN is not in $ENV_FILE." >&2; exit 1; }
export SEO_AGENT_DATABASE_DSN

cd "$SRC"
exec runuser -u "$APP_USER" -- "${VENV}/bin/python" -m app.tenants "$@"
