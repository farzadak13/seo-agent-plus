#!/usr/bin/env bash
#
# Issue the TLS certificate, but only once DNS actually points here.
#
# Let's Encrypt rate-limits failures, so this checks first rather than trying
# and burning quota. Separate from bootstrap.sh for that reason: base setup
# succeeds immediately, certificates wait for DNS propagation.
#
#   sudo bash enable-tls.sh
#
set -euo pipefail

DOMAIN="hoshyarseo.ir"
# Let's Encrypt sends expiry warnings here. A mailbox that does not exist yet
# means no warning when renewal starts failing, so override it with one you
# actually read:  sudo EMAIL=you@example.com bash enable-tls.sh
EMAIL="${EMAIL:-info@hoshyarseo.ir}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

say "checking DNS"
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org || true)"
if [[ -z "$SERVER_IP" ]]; then
  echo "Could not determine this server's public address." >&2
  exit 1
fi
echo "this server: ${SERVER_IP}"

FAILED=0
for NAME in "${DOMAIN}" "www.${DOMAIN}"; do
  RESOLVED="$(dig +short "$NAME" A | tail -1)"
  if [[ -z "$RESOLVED" ]]; then
    echo "  ${NAME} -> (no A record yet)"
    FAILED=1
  elif [[ "$RESOLVED" != "$SERVER_IP" ]]; then
    echo "  ${NAME} -> ${RESOLVED}   MISMATCH"
    FAILED=1
  else
    echo "  ${NAME} -> ${RESOLVED}   ok"
  fi
done

if [[ $FAILED -eq 1 ]]; then
  cat >&2 <<EOF

Stopping before certbot runs.

A name that resolves elsewhere usually means one of two things:
  * DNS has not propagated yet — wait and re-run
  * the CDN/proxy toggle is ON in the DNS panel, so the name resolves to the
    CDN rather than here, and the HTTP challenge cannot reach this machine

Each failed certbot attempt counts against Let's Encrypt's rate limit, which
is why this check exists.
EOF
  exit 1
fi

say "checking the challenge path is reachable"
mkdir -p /var/www/html/.well-known/acme-challenge
TOKEN="probe-$(date +%s)"
echo "$TOKEN" > "/var/www/html/.well-known/acme-challenge/${TOKEN}"
FETCHED="$(curl -fsS --max-time 15 "http://${DOMAIN}/.well-known/acme-challenge/${TOKEN}" || true)"
rm -f "/var/www/html/.well-known/acme-challenge/${TOKEN}"

if [[ "$FETCHED" != "$TOKEN" ]]; then
  cat >&2 <<EOF

The challenge path is not reachable over HTTP from the public internet.
certbot would fail the same way, so stopping here.

Check that port 80 is open in both the ArvanCloud firewall and ufw, and that
nginx is serving ${DOMAIN}.
EOF
  exit 1
fi
echo "challenge path reachable"

say "issuing the certificate"
certbot --nginx \
  -d "${DOMAIN}" -d "www.${DOMAIN}" \
  --agree-tos -m "${EMAIL}" \
  --redirect --non-interactive

say "renewal"
systemctl enable --now certbot.timer
certbot renew --dry-run

say "done"
echo "https://${DOMAIN} should now answer. Renewal is automatic every 90 days."
