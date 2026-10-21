#!/bin/sh
# Make a timestamped backup of Family Blogger's data while it keeps running.
# Usage (from the folder containing compose.yaml):  ./backup.sh [backup-dir]
set -eu

cd "$(dirname "$0")"
BACKUP_DIR="${1:-./backups}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
WORK="$BACKUP_DIR/family-blogger_$STAMP"

mkdir -p "$WORK"

# Consistent SQLite snapshot taken inside the container (safe while the app is running)
docker compose exec -T family-blogger python -c "
import sqlite3
src = sqlite3.connect('/data/family_blogger.db')
dst = sqlite3.connect('/data/.backup.db')
src.backup(dst)
dst.close(); src.close()
"
mv data/.backup.db "$WORK/family_blogger.db"

cp -R data/uploads "$WORK/uploads"
cp data/secret_key "$WORK/secret_key"

tar -czf "$WORK.tar.gz" -C "$BACKUP_DIR" "family-blogger_$STAMP"
rm -rf "$WORK"

echo "Backup saved to $WORK.tar.gz"
