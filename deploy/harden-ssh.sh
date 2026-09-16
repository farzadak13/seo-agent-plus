#!/usr/bin/env bash
#
# Disable root SSH login — only after a replacement admin user is proven.
#
# Separate from bootstrap.sh on purpose. Closing root in the same run that
# creates its replacement means that if the key copy silently failed, there is
# no second way in and the machine is gone. This script refuses to run until
# it can see that the other user actually has a key.
#
#   sudo bash harden-ssh.sh
#
set -euo pipefail

ADMIN_USER="${1:-deploy}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

# Without a terminal the confirmation prompt below is never displayed and the
# script hangs forever with no output. `ssh host "cmd"` allocates no TTY, which
# is exactly how this is normally run.
if [[ ! -t 0 ]]; then
  cat >&2 <<'EOF'
This script asks for confirmation, so it needs a terminal.

    ssh -t <host> "sudo bash /root/deploy/harden-ssh.sh"

The -t is what allocates one.
EOF
  exit 1
fi

say "checking ${ADMIN_USER} can actually get in"

if ! id -u "$ADMIN_USER" >/dev/null 2>&1; then
  echo "No such user: ${ADMIN_USER}. Run bootstrap.sh first." >&2
  exit 1
fi

KEYS="/home/${ADMIN_USER}/.ssh/authorized_keys"
if [[ ! -s "$KEYS" ]]; then
  echo "${KEYS} is missing or empty — disabling root would lock you out." >&2
  exit 1
fi
echo "authorized_keys: $(grep -c . "$KEYS") key(s)"

if ! sudo -u "$ADMIN_USER" sudo -n true 2>/dev/null; then
  echo "${ADMIN_USER} cannot use sudo — fix that before closing root." >&2
  exit 1
fi
echo "sudo: ok"

cat <<EOF

You are about to disable root SSH login. From here on the only way in is:

    ssh ${ADMIN_USER}@<this-server>

Do not continue unless you have already opened a session as ${ADMIN_USER}
from your own machine in another window.

EOF
read -r -p "Type the username to confirm: " CONFIRM
if [[ "$CONFIRM" != "$ADMIN_USER" ]]; then
  echo "Not confirmed; nothing changed."
  exit 1
fi

say "disabling root login"

# The number matters more than it looks. sshd takes the FIRST value it sees
# for a keyword, and /etc/ssh/sshd_config includes sshd_config.d/*.conf in
# lexical order — so a file named 99- loses to every file that sorts before
# it. Cloud images ship exactly such files: Ubuntu's 50-cloud-init.conf, and
# on ArvanCloud an 01-arvan-root-login.conf that sets PermitRootLogin yes.
# This was originally written as 99-hardening.conf. It wrote cleanly, passed
# sshd -t, reloaded without error, printed "done" — and changed nothing.
HARDENING="/etc/ssh/sshd_config.d/00-hardening.conf"
cat > "$HARDENING" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
rm -f /etc/ssh/sshd_config.d/99-hardening.conf

if ! sshd -t; then
  echo "sshd rejected the config; reverting." >&2
  rm -f "$HARDENING"
  exit 1
fi

# Ask sshd what it will actually do rather than trusting that writing a file
# was enough. This is the check whose absence hid the bug above.
EFFECTIVE="$(sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication) ')"
echo "$EFFECTIVE" | sed 's/^/  /'
if ! grep -qx "permitrootlogin no" <<<"$EFFECTIVE" \
   || ! grep -qx "passwordauthentication no" <<<"$EFFECTIVE"; then
  echo >&2
  echo "sshd still reports the old settings, so something else wins." >&2
  echo "Look for a lower-numbered file: ls /etc/ssh/sshd_config.d/" >&2
  rm -f "$HARDENING"
  exit 1
fi

systemctl reload ssh

say "done"
echo "Root login and password login are off, and sshd confirms it."
echo "Existing sessions stay open; test a new one now, before closing this."
