#!/bin/sh
# Make a timestamped backup of Family Blogger's data while it keeps running.
# Usage (from the folder containing compose.yaml):  ./backup.sh [backup-dir]
# backup-dir: a relative path is relative to where you run the command;
#      the default is ./backups next to this script.
# Env: BACKUP_KEEP  number of newest archives to keep (default 14).
#      Only family-blogger_*.tar.gz files in the backup dir are pruned.
set -eu
umask 077

CALLER_DIR="$PWD"
cd "$(dirname "$0")"
case "${1:-}" in
    '') BACKUP_DIR="./backups" ;;
    /*) BACKUP_DIR="$1" ;;
    *) BACKUP_DIR="$CALLER_DIR/$1" ;;
esac
KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
WORK="$BACKUP_DIR/family-blogger_$STAMP"
ARCHIVE="$WORK.tar.gz"
OK=0

case "$KEEP" in
    ''|*[!0-9]*|0) echo "BACKUP_KEEP must be a positive number" >&2; exit 1 ;;
esac

if [ -e "$ARCHIVE" ] || [ -e "$WORK" ]; then
    echo "A backup with this timestamp already exists; wait a second." >&2
    exit 1
fi

cleanup() {
    rm -rf "$WORK"
    rm -f data/.backup.db
    if [ "$OK" -ne 1 ]; then
        rm -f "$ARCHIVE"
        echo "Backup FAILED - nothing was kept or deleted." >&2
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

# Clear any leftover snapshot from an earlier failed run
rm -f data/.backup.db

mkdir -p "$WORK"

# Consistent SQLite snapshot taken inside the container with the online
# backup API (safe while the app runs, including in WAL mode), then
# checked before it is trusted.
docker compose exec -T family-blogger python -c "
import sqlite3, sys
src = sqlite3.connect('/data/family_blogger.db')
dst = sqlite3.connect('/data/.backup.db')
src.backup(dst)
result = dst.execute('PRAGMA integrity_check').fetchone()[0]
dst.close(); src.close()
if result != 'ok':
    print('integrity_check failed: ' + str(result), file=sys.stderr)
    sys.exit(1)
"
mv data/.backup.db "$WORK/family_blogger.db"

cp -R data/uploads "$WORK/uploads"
cp data/secret_key "$WORK/secret_key"

tar -czf "$ARCHIVE" -C "$BACKUP_DIR" "family-blogger_$STAMP"
rm -rf "$WORK"
OK=1

echo "Backup saved to $ARCHIVE"

# Retention: keep the newest $KEEP archives. Names sort by date, so list
# them oldest first and delete all but the last $KEEP.
COUNT=0
for f in "$BACKUP_DIR"/family-blogger_*.tar.gz; do
    [ -f "$f" ] && COUNT=$((COUNT + 1))
done
EXCESS=$((COUNT - KEEP))
if [ "$EXCESS" -gt 0 ]; then
    for f in "$BACKUP_DIR"/family-blogger_*.tar.gz; do
        [ "$EXCESS" -gt 0 ] || break
        [ -f "$f" ] || continue
        rm -f "$f"
        echo "Removed old backup $f"
        EXCESS=$((EXCESS - 1))
    done
fi
