#!/usr/bin/env bash
#
# Put the dashboard on the site, once, after a release that contains frontend/.
#
#   sudo bash frontend-setup.sh
#
# Later releases need nothing from here: nginx serves whatever release is
# current. The exception is deploy/nginx-app.conf itself, which is copied: run
# this again after changing it. Safe to run again. If nginx refuses the result,
# the previous configuration is put back and nothing changes for visitors.
#
set -euo pipefail

SITE="/etc/nginx/sites-available/hoshyarseo"
SNIPPET="/etc/nginx/snippets/hoshyarseo-app.conf"
ENV_FILE="/etc/seoagent/env"
WEBROOT="/var/www/hoshyarseo"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INCLUDE="include ${SNIPPET};"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ ! -f /opt/seoagent/current/frontend/index.html ]]; then
  echo "The live release has no frontend/. Ship a release that contains it first." >&2
  exit 1
fi

say "nginx"
BACKUP="$(mktemp)"
cp -p "$SITE" "$BACKUP"
SNIPPET_BACKUP=""
if [[ -f "$SNIPPET" ]]; then
  SNIPPET_BACKUP="$(mktemp)"
  cp -p "$SNIPPET" "$SNIPPET_BACKUP"
fi
install -d /etc/nginx/snippets
install -m 644 "${HERE}/nginx-app.conf" "$SNIPPET"
# app-setup.sh writes this include as a glob, so the name's stem is what counts:
# a second include of the same snippet is a duplicate location, and nginx refuses.
if ! grep -q 'snippets/hoshyarseo-app' "$SITE"; then
  # Into the HTTPS server block, before its catch-all location.
  python3 - "$SITE" "$INCLUDE" <<'PY'
import sys
path, include = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
anchor = "    location / {\n        try_files"
if text.count(anchor) != 1:
    sys.exit("Could not find the HTTPS catch-all location; nothing changed.")
open(path, "w", encoding="utf-8").write(text.replace(anchor, f"    {include}\n\n{anchor}"))
PY
fi
if ! nginx -t; then
  cp -p "$BACKUP" "$SITE"
  if [[ -n "$SNIPPET_BACKUP" ]]; then
    cp -p "$SNIPPET_BACKUP" "$SNIPPET"
  else
    rm -f "$SNIPPET"
  fi
  echo "nginx refused the new configuration; the old one is back." >&2
  exit 1
fi
systemctl reload nginx
rm -f "$BACKUP" "$SNIPPET_BACKUP"

say "landing page"
install -m 644 -o www-data -g www-data "${HERE}/landing/index.html" "${WEBROOT}/index.html"

say "where Google sign-in lands"
if ! grep -q '^SEO_AGENT_DASHBOARD_URL=' "$ENV_FILE"; then
  # A file edited by hand may not end in a newline; this must not join its last line.
  [[ -z "$(tail -c1 "$ENV_FILE")" ]] || printf '\n' >> "$ENV_FILE"
  printf 'SEO_AGENT_DASHBOARD_URL=/app/\n' >> "$ENV_FILE"
  chown root:seoagent "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  systemctl restart seoagent.service
  echo "set, service restarted"
else
  echo "already set"
fi

say "done"
echo "The dashboard is at /app/ on the site."
