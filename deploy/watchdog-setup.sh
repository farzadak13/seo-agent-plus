#!/usr/bin/env bash
#
# Notice when the service stops answering, restart it, and say so.
#
#   sudo bash watchdog-setup.sh
#
# systemd already restarts the process when it exits. What it cannot see is a
# process that is still running but no longer answers: a stuck worker, a lost
# database connection, a full disk. This asks /readyz every two minutes, and
# after three failures in a row restarts the service and writes a warning to
# the journal.
#
# It is the inside half. A machine that is down cannot report that it is down,
# so an outside check of https://hoshyarseo.ir/readyz (any uptime service) is
# still needed for the case where the whole server is gone.
#
# Optional: put ALERT_WEBHOOK_URL in /etc/seoagent/watchdog.env and every
# restart is also POSTed there as JSON {"text": "..."}. Bale, Slack, and most
# chat bots accept that shape or close to it.
#
set -euo pipefail

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

STATE_DIR="/var/lib/seoagent-watchdog"
install -d -m 700 "$STATE_DIR"

say "watchdog command"
cat > /usr/local/sbin/seoagent-watchdog <<EOF
#!/usr/bin/env bash
set -uo pipefail
STATE="${STATE_DIR}/failures"
LIMIT=3

# Stopped on purpose (maintenance, a release in progress): not ours to undo.
# "failed" is different: that is systemd having given up, and is restarted.
if [[ "\$(systemctl is-active seoagent.service)" == "inactive" ]]; then
  rm -f "\$STATE"
  exit 0
fi

# Loopback and the internal port, not the public name: the server cannot
# reach its own public address (no hairpin NAT), and a hang there would read
# as an outage that is not happening.
if curl -fsS --max-time 10 http://127.0.0.1:8000/readyz > /dev/null; then
  rm -f "\$STATE"
  exit 0
fi

COUNT=\$(( \$(cat "\$STATE" 2>/dev/null || echo 0) + 1 ))
echo "\$COUNT" > "\$STATE"
echo "readyz failed (\$COUNT/\$LIMIT)"
if (( COUNT < LIMIT )); then
  exit 0
fi

rm -f "\$STATE"
MESSAGE="seoagent on \$(hostname) did not answer /readyz \$LIMIT times in a row; restarting it."
logger -p daemon.warning -t seoagent-watchdog "\$MESSAGE"
echo "\$MESSAGE"
systemctl restart seoagent.service

if [[ -f /etc/seoagent/watchdog.env ]]; then
  # shellcheck disable=SC1091
  source /etc/seoagent/watchdog.env
fi
if [[ -n "\${ALERT_WEBHOOK_URL:-}" ]]; then
  curl -fsS --max-time 10 -H 'Content-Type: application/json' \\
    -d "{\"text\": \"\$MESSAGE\"}" "\$ALERT_WEBHOOK_URL" > /dev/null \\
    || logger -p daemon.err -t seoagent-watchdog "alert webhook failed"
fi
EOF
chmod 750 /usr/local/sbin/seoagent-watchdog
echo "installed"

say "systemd timer"
cat > /etc/systemd/system/seoagent-watchdog.service <<'EOF'
[Unit]
Description=SEO Agent readiness watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/seoagent-watchdog
EOF

cat > /etc/systemd/system/seoagent-watchdog.timer <<'EOF'
[Unit]
Description=Check SEO Agent readiness every two minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now seoagent-watchdog.timer >/dev/null
echo "enabled"

say "first check, now"
/usr/local/sbin/seoagent-watchdog && echo "ok"

cat <<EOF

Restarts it made:  journalctl -t seoagent-watchdog
Every check:       journalctl -u seoagent-watchdog.service
EOF
