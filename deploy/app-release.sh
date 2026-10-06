#!/usr/bin/env bash
#
# Install a release from a tarball produced by `git archive`.
#
#   sudo bash app-release.sh /tmp/release.tgz [label]
#
# Why a tarball and not `scp -r` of the project folder: the folder contains
# .venv, __pycache__, the local .env with its own database password, and
# secrets/. Copying it wholesale ships all of that to the server. `git archive`
# emits exactly the tracked files at a commit — the same set the repository
# would clone — so ignored files cannot leak by accident.
#
# Releases are unpacked side by side and `current` is a symlink. That is what
# makes a failed deploy recoverable: if the new code does not answer /readyz,
# the symlink goes back and the service restarts on the version that worked.
#
set -euo pipefail

APP_ROOT="/opt/seoagent"
RELEASES="${APP_ROOT}/releases"
CURRENT="${APP_ROOT}/current"
VENV="${APP_ROOT}/venv"
APP_USER="seoagent"
ENV_FILE="/etc/seoagent/env"
PORT="8000"
KEEP=5

TARBALL="${1:-}"
LABEL="${2:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ -z "$TARBALL" || ! -s "$TARBALL" ]]; then
  echo "Usage: sudo bash app-release.sh /tmp/release.tgz [label]" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "${ENV_FILE} is missing. Run app-setup.sh first." >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
NAME="${STAMP}${LABEL:+-${LABEL}}"
TARGET="${RELEASES}/${NAME}"

say "unpacking ${NAME}"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$RELEASES"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$TARGET"
tar -xzf "$TARBALL" -C "$TARGET"
chown -R "${APP_USER}:${APP_USER}" "$TARGET"

for required in pyproject.toml app migrations; do
  if [[ ! -e "${TARGET}/${required}" ]]; then
    echo "The archive has no ${required}; this is not the application." >&2
    rm -rf "$TARGET"
    exit 1
  fi
done
echo "$(find "$TARGET" -type f | wc -l) files"

say "dependencies"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "$VENV"
  chown -R "${APP_USER}:${APP_USER}" "$VENV"
fi
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q --upgrade pip
# Not editable: an editable install points at this directory, and this
# directory is replaced by the next release.
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q "$TARGET"
sudo -u "$APP_USER" "${VENV}/bin/pip" install -q "uvicorn[standard]>=0.30,<1"
echo "installed"

say "migrations"
# Before the swap, deliberately. New code against an old schema fails in ways
# that read as application bugs rather than as a deployment mistake.
# The DSN reaches Python through the environment only: `sudo … env NAME=value`
# put the database password in a process's argv, readable through `ps`.
SEO_AGENT_DATABASE_DSN="$(sed -n 's/^SEO_AGENT_DATABASE_DSN=//p' "$ENV_FILE" | head -n1)"
SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN%\"}"; SEO_AGENT_DATABASE_DSN="${SEO_AGENT_DATABASE_DSN#\"}"
[[ -n "$SEO_AGENT_DATABASE_DSN" ]] || { echo "SEO_AGENT_DATABASE_DSN is not in $ENV_FILE." >&2; exit 1; }
export SEO_AGENT_DATABASE_DSN
( cd "$TARGET" && runuser -u "$APP_USER" -- "${VENV}/bin/python" -m app.migrate )

PREVIOUS=""
if [[ -L "$CURRENT" ]]; then
  PREVIOUS="$(readlink -f "$CURRENT")"
fi

say "switching to ${NAME}"
ln -sfn "$TARGET" "${CURRENT}.new"
mv -Tf "${CURRENT}.new" "$CURRENT"
chown -h "${APP_USER}:${APP_USER}" "$CURRENT"
systemctl restart seoagent.service

say "readiness"
# The real check. `systemctl is-active` only says a process exists; /readyz
# says the database answers and the worker holds its lease.
READY=""
for _ in $(seq 1 15); do
  if READY="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/readyz" 2>/dev/null)"; then
    break
  fi
  READY=""
  sleep 2
done

if [[ -z "$READY" ]]; then
  echo "The new release never became ready." >&2
  journalctl -u seoagent.service -n 40 --no-pager >&2
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    say "rolling back to $(basename "$PREVIOUS")"
    sudo -u "$APP_USER" "${VENV}/bin/pip" install -q "$PREVIOUS" || true
    ln -sfn "$PREVIOUS" "${CURRENT}.new"
    mv -Tf "${CURRENT}.new" "$CURRENT"
    systemctl restart seoagent.service
    sleep 3
    curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/readyz" \
      && echo "Rolled back and ready. The new release is left in ${TARGET} for inspection." \
      || echo "Rollback also failed; the service is down." >&2
  else
    echo "No previous release to roll back to." >&2
  fi
  exit 1
fi

echo "$READY"

# Old releases are kept so a rollback has somewhere to go, but not forever:
# 40GB of disk and an unbounded directory is a slow outage.
say "pruning"
mapfile -t OLD < <(ls -1dt "${RELEASES}"/*/ 2>/dev/null | tail -n +$((KEEP + 1)))
for dir in "${OLD[@]:-}"; do
  [[ -z "$dir" ]] && continue
  [[ "$(readlink -f "$dir")" == "$(readlink -f "$CURRENT")" ]] && continue
  rm -rf "$dir"
  echo "removed $(basename "$dir")"
done
echo "keeping $(ls -1d "${RELEASES}"/*/ 2>/dev/null | wc -l) release(s)"

say "done"
echo "${NAME} is live."
