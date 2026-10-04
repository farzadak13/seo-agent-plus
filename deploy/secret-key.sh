#!/usr/bin/env bash
#
# Give the server the key that encrypts customers' site credentials.
#
#   sudo bash secret-key.sh            add a key if there is none
#   sudo bash secret-key.sh rotate     put a new key first, keep the old one
#
# The key is generated here and written straight into /etc/seoagent/env, so it
# never appears on a screen or in a shell history. Without it the API refuses
# credential values and accepts only environment references, as before.
#
# Losing this key makes every stored credential unreadable: customers would
# have to connect their sites again. It is in /etc/seoagent/env, which is NOT
# in the database backup by design. Keep a copy of that file somewhere safe
# and separate from the backups.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VENV="/opt/seoagent/venv"
NAME="SEO_AGENT_SECRET_KEYS"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

NEW_KEY="$("${VENV}/bin/python" -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
CURRENT="$(grep -E "^${NAME}=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"

case "${1:-add}" in
  add)
    if [[ -n "$CURRENT" ]]; then
      echo "${NAME} is already set; nothing to do. Use 'rotate' to add a new key."
      exit 0
    fi
    printf '\n# Encrypts customers'"'"' site credentials in the database. See deploy/secret-key.sh.\n%s=%s\n' \
      "$NAME" "$NEW_KEY" >> "$ENV_FILE"
    echo "added"
    ;;
  rotate)
    if [[ -z "$CURRENT" ]]; then
      echo "${NAME} is not set yet; run without arguments first." >&2
      exit 1
    fi
    # New key first (used for writes), old keys after it (still readable).
    sed -i "s|^${NAME}=.*|${NAME}=${NEW_KEY},${CURRENT}|" "$ENV_FILE"
    echo "rotated; existing credentials stay readable with the old key"
    ;;
  *)
    echo "usage: $0 [add|rotate]" >&2
    exit 2
    ;;
esac

chown root:seoagent "$ENV_FILE"
chmod 640 "$ENV_FILE"
systemctl restart seoagent.service
echo "service restarted"
