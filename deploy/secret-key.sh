#!/usr/bin/env bash
#
# Give the server the key that encrypts customers' site credentials.
#
#   sudo bash secret-key.sh            add a key if there is none
#   sudo bash secret-key.sh rotate     put a new key first, keep the old one
#
# The key is generated and written to /etc/seoagent/env inside one Python
# process, so it never appears on a screen, in a shell history, or on any
# command line (where `ps` could see it). Without it the API refuses
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
MODE="${1:-add}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ "$MODE" != "add" && "$MODE" != "rotate" ]]; then
  echo "usage: $0 [add|rotate]" >&2
  exit 2
fi

set +e
"${VENV}/bin/python" - "$ENV_FILE" "$MODE" <<'PY'
import base64, os, re, sys

path, mode = sys.argv[1], sys.argv[2]
name = "SEO_AGENT_SECRET_KEYS"
lines = open(path, encoding="utf-8").read().splitlines()
pattern = re.compile(rf"^{name}=(.*)$")
index = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
new_key = base64.urlsafe_b64encode(os.urandom(32)).decode()

if mode == "add":
    if index is not None:
        print(f"{name} is already set; nothing to do. Use 'rotate' to add a new key.")
        sys.exit(3)
    lines += ["", "# Encrypts customers' site credentials in the database. See deploy/secret-key.sh.",
              f"{name}={new_key}"]
    print("added")
else:
    if index is None:
        print(f"{name} is not set yet; run without arguments first.", file=sys.stderr)
        sys.exit(1)
    current = pattern.match(lines[index]).group(1).strip().strip("'\"")
    # New key first (used for writes), old keys after it (still readable).
    lines[index] = f"{name}={new_key},{current}"
    print("rotated; existing credentials stay readable with the old key")

temporary = path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.chmod(temporary, 0o640)
os.replace(temporary, path)
PY
STATUS=$?
set -e

case "$STATUS" in
  0) ;;
  3) exit 0 ;;   # already set, nothing changed
  *) exit "$STATUS" ;;
esac

chown root:seoagent "$ENV_FILE"
chmod 640 "$ENV_FILE"
systemctl restart seoagent.service
echo "service restarted"
