#!/usr/bin/env bash
#
# Dashboard accounts, with the environment already loaded.
#
#   sudo bash account.sh create --tenant t-xxxx --email you@example.com
#   sudo bash account.sh create --tenant t-xxxx --email you@example.com --google-only
#   sudo bash account.sh set-password --email you@example.com
#   sudo bash account.sh disable --email you@example.com
#   sudo bash account.sh list --tenant t-xxxx
#
# A thin wrapper over `python -m app.accounts`, like tenant.sh, so the
# database password never appears on a command line. Passwords are asked at
# a hidden prompt; run it from a real terminal (ssh -t) so the prompt works.
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

# Only the DSN is read from the env file, and it reaches Python through the
# environment, never as an argument: `sudo ... env NAME=value` put the
# database password in the argv of a process that stays alive for the whole
# interactive run, readable by any local user through `ps`. runuser passes
# the environment on unchanged and needs no sudo rules.
SEO_AGENT_DATABASE_DSN="$(sed -n 's/^SEO_AGENT_DATABASE_DSN=//p' "$ENV_FILE" | head -n1)"
SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN%\"}"; SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN#\"}"
[[ -n "$SEO_AGENT_DATABASE_DSN" ]] || { echo "SEO_AGENT_DATABASE_DSN is not in $ENV_FILE." >&2; exit 1; }
export SEO_AGENT_DATABASE_DSN

cd "$SRC"
exec runuser -u "$APP_USER" -- "${VENV}/bin/python" -m app.accounts "$@"
