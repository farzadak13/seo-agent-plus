#!/usr/bin/env bash
#
# One-time application setup on the hoshyarseo server.
#
# Safe to re-run: it creates what is missing and leaves what exists alone.
# In particular it never regenerates the database password or the API key
# once /etc/seoagent/env exists — rotating those silently would break a
# running service in a way that looks like a database outage.
#
#   sudo bash app-setup.sh
#
# Deploying code is a separate script (app-deploy.sh), because this one
# needs to run once and that one runs on every release.
#
set -euo pipefail

DOMAIN="hoshyarseo.ir"
APP_USER="seoagent"
APP_ROOT="/opt/seoagent"
ENV_DIR="/etc/seoagent"
ENV_FILE="${ENV_DIR}/env"
WEBROOT="/var/www/hoshyarseo"
DB_NAME="seoagent"
DB_USER="seoagent"
PORT="8000"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

# --- service account --------------------------------------------------------
# A system user with no login and no home of its own. The application never
# needs to be a person.
say "service user: ${APP_USER}"
if id -u "$APP_USER" >/dev/null 2>&1; then
  echo "already exists"
else
  adduser --system --group --no-create-home --home "$APP_ROOT" "$APP_USER"
  echo "created"
fi
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_ROOT"

# --- database ---------------------------------------------------------------
# peer authentication would tie the app to a matching OS user; a password over
# loopback keeps the DSN the same shape as everywhere else, which matters
# because the same code runs locally against docker compose.
say "database"
DB_PASSWORD=""
if [[ -f "$ENV_FILE" ]] && grep -q "^SEO_AGENT_DATABASE_DSN=" "$ENV_FILE"; then
  echo "env file exists; keeping the current credentials"
else
  DB_PASSWORD="$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-40)"
fi

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  echo "role ${DB_USER} exists"
  if [[ -n "$DB_PASSWORD" ]]; then
    # The env file is gone but the role is not: reset the password so the two
    # agree again, rather than writing a DSN that cannot connect.
    sudo -u postgres psql -qc "ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD}'"
    echo "password reset to match the new env file"
  fi
else
  sudo -u postgres psql -qc "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}'"
  echo "role ${DB_USER} created"
fi

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  echo "database ${DB_NAME} exists"
else
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
  echo "database ${DB_NAME} created"
fi

# --- environment ------------------------------------------------------------
say "environment: ${ENV_FILE}"
install -d -o root -g "$APP_USER" -m 750 "$ENV_DIR"

if [[ -f "$ENV_FILE" ]]; then
  echo "already exists; not touching it"
else
  API_KEY="$(openssl rand -hex 32)"
  cat > "$ENV_FILE" <<EOF
# Written by app-setup.sh. Secrets live here and nowhere else.
SEO_AGENT_ENV=production
SEO_AGENT_API_KEY=${API_KEY}
SEO_AGENT_DATABASE_DSN=postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}

SEO_AGENT_WORKER_ENABLED=true
SEO_AGENT_WORKER_POLL_INTERVAL_SECONDS=1.0
SEO_AGENT_OBSERVABILITY_ENABLED=true

# Search Console. Live means real calls to Google; this server can reach it
# directly, so no proxy.
SEO_AGENT_GSC_MODE=live
SEO_AGENT_GSC_TIMEOUT_SECONDS=30
SEO_AGENT_MAX_WINDOW_DAYS=90
SEO_AGENT_GSC_DATA_LAG_DAYS=3
SEO_AGENT_GOOGLE_VERIFY_TLS=true

# The title path stays off until a SERP source is chosen. Turning it on with
# these unset fails at startup rather than running half-wired.
SEO_AGENT_TITLE_WORKFLOW_ENABLED=false
SEO_AGENT_SERP_MODE=none
SEO_AGENT_LLM_MODE=none

# The service account key goes in as one line, added by app-credential.sh.
EOF
  chown root:"$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  echo "created"
  echo
  echo "  API key (store this; it is not shown again):"
  echo "    ${API_KEY}"
  echo
fi

# --- systemd ----------------------------------------------------------------
say "systemd unit"
install -m 644 "${HERE}/seoagent.service" /etc/systemd/system/seoagent.service
systemctl daemon-reload
systemctl enable seoagent.service >/dev/null
echo "installed and enabled (not started; app-deploy.sh starts it)"

# --- nginx ------------------------------------------------------------------
# Written whole rather than patched. certbot edited this file when it issued
# the certificate; regenerating it from a template that already knows the
# certificate paths keeps it readable and predictable. Renewal runs
# `certbot renew`, which reloads nginx but does not rewrite the config.
say "nginx"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
if [[ ! -s "${CERT_DIR}/fullchain.pem" ]]; then
  echo "No certificate at ${CERT_DIR}. Run enable-tls.sh first." >&2
  exit 1
fi

cat > /etc/nginx/sites-available/hoshyarseo <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN} www.${DOMAIN};

    # Left on HTTP so certificate renewal keeps working.
    location /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${DOMAIN} www.${DOMAIN};

    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    root ${WEBROOT};
    index index.html;

    # The API. Separate location blocks rather than one catch-all proxy, so a
    # typo in a path returns 404 from nginx instead of reaching the app.
    location /v1/ {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;

        # Credentials travel in the Authorization header; keeping them out of
        # the access log is the difference between a log file and a key store.
        access_log /var/log/nginx/hoshyarseo.api.log;
    }

    location = /readyz {
        proxy_pass http://127.0.0.1:${PORT}/readyz;
        proxy_set_header Host \$host;
        access_log off;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    access_log /var/log/nginx/hoshyarseo.access.log;
    error_log  /var/log/nginx/hoshyarseo.error.log;
}
EOF

ln -sf /etc/nginx/sites-available/hoshyarseo /etc/nginx/sites-enabled/hoshyarseo
nginx -t
systemctl reload nginx
echo "reloaded"

say "done"
cat <<EOF

Next:

  1. Install the Google service account key:
         sudo bash app-credential.sh /path/to/service-account.json

  2. Deploy the code:
         sudo bash app-deploy.sh

EOF
