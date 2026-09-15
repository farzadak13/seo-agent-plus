#!/usr/bin/env bash
#
# Base configuration for the hoshyarseo server. Safe to re-run.
#
# Deliberately stops before TLS: a certificate cannot be issued until DNS
# resolves to this machine, and a failed attempt spends Let's Encrypt quota.
# Run enable-tls.sh once `dig` agrees, not before.
#
#   sudo bash bootstrap.sh
#
set -euo pipefail

DOMAIN="hoshyarseo.ir"
ADMIN_USER="deploy"
WEBROOT="/var/www/hoshyarseo"
APP_ROOT="/opt/seoagent"
SWAP_SIZE="4G"
SWAP_FILE="/swapfile"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

# --- swap -------------------------------------------------------------------
# 2GB of RAM has to hold PostgreSQL, the API, a worker and nginx. Without swap
# the first spike kills a process and the symptom appears somewhere unrelated:
# nginx reports "upstream prematurely closed connection" with no traceback.
say "swap"
if swapon --show | grep -q "$SWAP_FILE"; then
  echo "already active"
else
  fallocate -l "$SWAP_SIZE" "$SWAP_FILE"
  chmod 600 "$SWAP_FILE"
  mkswap "$SWAP_FILE"
  swapon "$SWAP_FILE"
  grep -q "$SWAP_FILE" /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
  echo "created $SWAP_SIZE"
fi
# Prefer RAM, but use swap rather than killing something.
sysctl -q -w vm.swappiness=10
grep -q "^vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" >> /etc/sysctl.conf

# --- packages ---------------------------------------------------------------
say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  nginx certbot python3-certbot-nginx \
  ufw fail2ban unattended-upgrades \
  git curl ca-certificates gnupg \
  python3-venv python3-pip \
  postgresql postgresql-contrib

# --- automatic security updates ---------------------------------------------
say "unattended security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# --- firewall ---------------------------------------------------------------
# PostgreSQL is not listed on purpose: it stays on loopback and is reached by
# the app on the same machine. A database on a public port is how a small
# service becomes someone else's.
say "firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

say "fail2ban"
systemctl enable --now fail2ban

# --- admin user -------------------------------------------------------------
# This image logs in as root. Working as root is avoidable, but locking root
# out in the same run that creates the replacement is how a server becomes
# unreachable forever: if the key copy silently failed, there is no second way
# in. So this creates the user and verifies the key landed; disabling root is
# a separate script, run only after you have logged in as the new user.
say "admin user: ${ADMIN_USER}"
if id -u "$ADMIN_USER" >/dev/null 2>&1; then
  echo "already exists"
else
  adduser --disabled-password --gecos "" "$ADMIN_USER"
fi
usermod -aG sudo "$ADMIN_USER"
echo "${ADMIN_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-${ADMIN_USER}"
chmod 440 "/etc/sudoers.d/90-${ADMIN_USER}"

SOURCE_KEYS=""
for CANDIDATE in /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys; do
  if [[ -s "$CANDIDATE" ]]; then SOURCE_KEYS="$CANDIDATE"; break; fi
done

if [[ -z "$SOURCE_KEYS" ]]; then
  echo "WARNING: found no authorized_keys to copy. Add one to /home/${ADMIN_USER}/.ssh/" >&2
else
  install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/${ADMIN_USER}/.ssh"
  install -m 600 -o "$ADMIN_USER" -g "$ADMIN_USER" "$SOURCE_KEYS" "/home/${ADMIN_USER}/.ssh/authorized_keys"
  echo "copied $(wc -l < "$SOURCE_KEYS") key(s) from ${SOURCE_KEYS}"
fi

# --- ssh: passwords off, root left alone for now -----------------------------
say "ssh"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t && systemctl reload ssh
echo "password login disabled; root login is still allowed until you verify ${ADMIN_USER}"

# --- directories ------------------------------------------------------------
say "directories"
mkdir -p "$WEBROOT" "$APP_ROOT"
chown -R "${ADMIN_USER}:${ADMIN_USER}" "$APP_ROOT"
chown -R www-data:www-data "$WEBROOT"

# --- nginx ------------------------------------------------------------------
say "nginx"
cat > /etc/nginx/sites-available/hoshyarseo <<EOF
# HTTP only for now. certbot rewrites this file when the certificate is
# issued; it is not hand-edited afterwards.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};

    root ${WEBROOT};
    index index.html;

    # Let's Encrypt writes its challenge here.
    location /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    access_log /var/log/nginx/hoshyarseo.access.log;
    error_log  /var/log/nginx/hoshyarseo.error.log;
}
EOF

ln -sf /etc/nginx/sites-available/hoshyarseo /etc/nginx/sites-enabled/hoshyarseo
rm -f /etc/nginx/sites-enabled/default

if [[ ! -f "${WEBROOT}/index.html" ]]; then
  cat > "${WEBROOT}/index.html" <<'EOF'
<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">
<title>هوشیار سئو</title><body style="font-family:system-ui;padding:40px">
<h1>هوشیار سئو</h1><p>در حال راه‌اندازی.</p></body></html>
EOF
  chown www-data:www-data "${WEBROOT}/index.html"
fi

nginx -t
systemctl enable --now nginx
systemctl reload nginx

# --- postgres ---------------------------------------------------------------
# Tuned for 2GB of RAM. The defaults assume a machine with room to spare.
say "postgresql"
PG_VERSION="$(ls /etc/postgresql | sort -V | tail -1)"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/conf.d/seoagent.conf"
mkdir -p "$(dirname "$PG_CONF")"
cat > "$PG_CONF" <<'EOF'
listen_addresses = 'localhost'
shared_buffers = 256MB
effective_cache_size = 768MB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 40
EOF
systemctl enable --now postgresql
systemctl restart postgresql
sudo -u postgres psql -tAc "SELECT version();"

say "done"
cat <<EOF

Next, in order:

  0. From your own machine, confirm the new user works BEFORE closing this
     session, then lock root out:
         ssh ${ADMIN_USER}@$(curl -fsS --max-time 5 https://api.ipify.org || echo "<server-ip>")
         sudo bash harden-ssh.sh
     Keep this root session open until that succeeds. A server you cannot log
     into is not recoverable from here.

  1. Point DNS at this machine, with any CDN/proxy toggle OFF, then confirm:
         dig +short ${DOMAIN}
     It must print this server's address. certbot fails otherwise, and each
     failed attempt spends Let's Encrypt quota.

  2. Upload the real landing page and privacy policy into ${WEBROOT}.

  3. Issue the certificate:
         sudo bash enable-tls.sh

Free memory now:
$(free -h | sed 's/^/     /')
EOF
