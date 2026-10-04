#!/usr/bin/env bash
#
# Daily database backups, and a check that a backup actually restores.
#
#   sudo bash backup-setup.sh
#
# Safe to re-run. Installs:
#
#   /usr/local/sbin/seoagent-backup        one dump, verified, old ones pruned
#   seoagent-backup.timer                  runs it every night at 03:30
#   /usr/local/sbin/seoagent-restore-test  restores the newest dump into a
#                                          scratch database and compares counts
#
# A backup nobody has restored is a hope, not a backup. Run the restore test
# once now and again after any PostgreSQL upgrade.
#
# The dumps live on this machine, which protects against a bad migration or a
# deleted row but not against losing the machine. For that, put an rclone
# remote name in /etc/seoagent/backup.env (BACKUP_RCLONE_REMOTE=arvan:bucket/path)
# and every dump is copied there as well. ArvanCloud Object Storage speaks S3,
# which rclone supports directly.
#
set -euo pipefail

DB_NAME="seoagent"
BACKUP_DIR="/var/backups/seoagent"
KEEP_DAYS=14

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

say "backup directory: ${BACKUP_DIR}"
# Owned by postgres, readable by nobody else: a dump holds every tenant's
# data and every API key hash.
install -d -o postgres -g postgres -m 700 "$BACKUP_DIR"

say "backup command"
cat > /usr/local/sbin/seoagent-backup <<EOF
#!/usr/bin/env bash
set -euo pipefail
STAMP="\$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/${DB_NAME}-\${STAMP}.dump"
PARTIAL="\${TARGET}.partial"

# Written under a temporary name and renamed only once complete and readable,
# so a crash mid-dump never leaves something that looks like a good backup.
sudo -u postgres pg_dump --format=custom --compress=6 --file="\$PARTIAL" ${DB_NAME}
sudo -u postgres pg_restore --list "\$PARTIAL" > /dev/null
mv "\$PARTIAL" "\$TARGET"
chmod 600 "\$TARGET"
echo "backup written: \$TARGET (\$(du -h "\$TARGET" | cut -f1))"

find ${BACKUP_DIR} -name '${DB_NAME}-*.dump' -mtime +${KEEP_DAYS} -print -delete
find ${BACKUP_DIR} -name '*.partial' -mmin +120 -delete

if [[ -f /etc/seoagent/backup.env ]]; then
  # shellcheck disable=SC1091
  source /etc/seoagent/backup.env
fi
if [[ -n "\${BACKUP_RCLONE_REMOTE:-}" ]]; then
  rclone copy "\$TARGET" "\$BACKUP_RCLONE_REMOTE"
  echo "copied off-site to \$BACKUP_RCLONE_REMOTE"
fi
EOF
chmod 750 /usr/local/sbin/seoagent-backup
echo "installed"

say "restore test command"
cat > /usr/local/sbin/seoagent-restore-test <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRATCH="${DB_NAME}_restore_test"
LATEST="\$(ls -1t ${BACKUP_DIR}/${DB_NAME}-*.dump 2>/dev/null | head -n1 || true)"
if [[ -z "\$LATEST" ]]; then
  echo "No backup found in ${BACKUP_DIR}. Run seoagent-backup first." >&2
  exit 1
fi
echo "restoring \$LATEST into \$SCRATCH"
sudo -u postgres dropdb --if-exists "\$SCRATCH"
sudo -u postgres createdb "\$SCRATCH"
trap 'sudo -u postgres dropdb --if-exists "\$SCRATCH"' EXIT
sudo -u postgres pg_restore --no-owner --exit-on-error --dbname="\$SCRATCH" "\$LATEST"

COUNT_SQL="SELECT aggregate_type || ' ' || count(*) FROM persistence_records GROUP BY 1 ORDER BY 1"
echo
echo "records in the backup:"
sudo -u postgres psql -tA -d "\$SCRATCH" -c "\$COUNT_SQL" | sed 's/^/  /'
echo "records in the live database now (newer writes make these larger):"
sudo -u postgres psql -tA -d ${DB_NAME} -c "\$COUNT_SQL" | sed 's/^/  /'
echo
echo "restore OK"
EOF
chmod 750 /usr/local/sbin/seoagent-restore-test
echo "installed"

say "systemd timer"
cat > /etc/systemd/system/seoagent-backup.service <<'EOF'
[Unit]
Description=SEO Agent database backup
After=postgresql.service
Requires=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/seoagent-backup
EOF

cat > /etc/systemd/system/seoagent-backup.timer <<'EOF'
[Unit]
Description=Nightly SEO Agent database backup

[Timer]
OnCalendar=*-*-* 03:30:00
# A machine that was off at 03:30 backs up when it comes back.
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now seoagent-backup.timer >/dev/null
echo "enabled: $(systemctl list-timers seoagent-backup.timer --no-legend | awk '{print $1, $2, $3}')"

say "first backup, now"
/usr/local/sbin/seoagent-backup

say "done"
cat <<EOF

Check that it restores:
    sudo seoagent-restore-test

Backups: ${BACKUP_DIR}, ${KEEP_DAYS} days kept.
Logs:    journalctl -u seoagent-backup.service
EOF
