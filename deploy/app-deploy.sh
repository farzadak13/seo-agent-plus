#!/usr/bin/env bash
#
# Deploy or update the application. Runs on every release.
#
#   sudo bash app-deploy.sh [git-ref]
#
# What makes this safe to run on a live service: migrations are applied before
# the new code starts, the service is only restarted after they succeed, and
# the script fails loudly if /readyz does not answer afterwards. A deploy that
# leaves a dead service behind while printing "done" is worse than one that
# refuses to finish.
#
set -euo pipefail

APP_ROOT="/opt/seoagent"
SRC="${APP_ROOT}/current"
VENV="${APP_ROOT}/venv"
APP_USER="seoagent"
ENV_FILE="/etc/seoagent/env"
REF="${1:-main}"
PORT="8000"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
for path in "$SRC/.git" "$ENV_FILE"; do
  if [[ ! -e "$path" ]]; then
    echo "Missing ${path}. Clone the repository into ${SRC} and run app-setup.sh first." >&2
    exit 1
  fi
done

say "fetching ${REF}"
sudo -u "$APP_USER" git -C "$SRC" fetch --prune origin
sudo -u "$APP_USER" git -C "$SRC" checkout -q "$REF"
sudo -u "$APP_USER" git -C "$SRC" reset --hard -q "origin/${REF}" 2>/dev/null \
  || sudo -u "$APP_USER" git -C "$SRC" reset --hard -q "$REF"
echo "now at $(sudo -u "$APP_USER" git -C "$SRC" log --oneline -1)"

say "dependencies"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "$VENV"
  chown -R "${APP_USER}:${APP_USER}" "$VENV"
fi
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q -e "$SRC"
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q "uvicorn[standard]>=0.30,<1"
echo "installed"

say "migrations"
# Before the restart, deliberately. New code against an old schema fails in
# ways that look like application bugs.
# The DSN reaches Python through the environment only: `sudo … env NAME=value`
# put the database password in a process's argv, readable through `ps`.
SEO_AGENT_DATABASE_DSN="$(sed -n 's/^SEO_AGENT_DATABASE_DSN=//p' "$ENV_FILE" | head -n1)"
SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN%\"}"; SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN#\"}"
[[ -n "$SEO_AGENT_DATABASE_DSN" ]] || { echo "SEO_AGENT_DATABASE_DSN is not in $ENV_FILE." >&2; exit 1; }
export SEO_AGENT_DATABASE_DSN
( cd "$SRC" && runuser -u "$APP_USER" -- "${VENV}/bin/python" -m app.migrate )

say "restarting"
systemctl restart seoagent.service
sleep 3
systemctl is-active --quiet seoagent.service || {
  echo "The service is not running. Recent log:" >&2
  journalctl -u seoagent.service -n 40 --no-pager >&2
  exit 1
}

say "readiness"
# The real check. is-active only says a process exists; /readyz says the
# database answers and the worker holds its lease.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if BODY="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/readyz" 2>/dev/null)"; then
    echo "$BODY"
    say "done"
    echo "Deployed $(sudo -u "$APP_USER" git -C "$SRC" rev-parse --short HEAD) and ready."
    exit 0
  fi
  sleep 2
done

echo "The service started but /readyz never answered. Recent log:" >&2
journalctl -u seoagent.service -n 40 --no-pager >&2
exit 1
