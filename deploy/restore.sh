#!/bin/sh
# Restore Family Blogger's data from a backup made by backup.sh.
# Usage (from the folder containing compose.yaml):  ./restore.sh <archive>
# The current data/ is first copied to data.before-restore-<time>, so
# nothing is lost. You are asked to confirm before anything changes.
set -eu
umask 077

cd "$(dirname "$0")"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: ./restore.sh path/to/family-blogger_<date>.tar.gz" >&2
    exit 1
fi
ARCHIVE="$1"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
SAFETY="data.before-restore-$STAMP"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/fb-restore.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
trap 'exit 1' INT TERM HUP

# Read PUID/PGID from .env if present (no code is run from the file);
# default to 1000:1000, matching compose.yaml
PUID=""; PGID=""
if [ -f .env ]; then
    PUID="$(sed -n 's/^PUID=//p' .env | tail -n 1)"
    PGID="$(sed -n 's/^PGID=//p' .env | tail -n 1)"
fi
PUID="${PUID:-1000}"; PGID="${PGID:-1000}"

tar -xzf "$ARCHIVE" -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SRC" ] || [ ! -f "$SRC/family_blogger.db" ] \
        || [ ! -f "$SRC/secret_key" ] || [ ! -d "$SRC/uploads" ]; then
    echo "This does not look like a Family Blogger backup." >&2
    exit 1
fi

echo "Restoring from: $ARCHIVE"
echo "The app will be stopped. Current data/ will be kept as:"
echo "  $SAFETY"
printf "Type 'restore' to continue: "
read -r ANSWER
if [ "$ANSWER" != "restore" ]; then
    echo "Cancelled. Nothing was changed."
    exit 1
fi

# Stop the app first so no write can land mid-copy. From here on,
# always start it again, even if a step fails.
trap 'docker compose start family-blogger >/dev/null 2>&1 || \
    echo "Could not start the app: run docker compose up -d" >&2; \
    rm -rf "$TMP"' EXIT
docker compose stop family-blogger

# Safety copy of the stopped data. If it fails, nothing was changed.
mkdir -p data
if ! cp -Rp data "$SAFETY"; then
    rm -rf "$SAFETY"
    echo "Could not copy data/ to $SAFETY. Nothing was changed." >&2
    exit 1
fi
echo "Safety copy saved to $SAFETY"

rm -rf data/uploads
cp "$SRC/family_blogger.db" data/family_blogger.db
cp -R "$SRC/uploads" data/uploads
cp "$SRC/secret_key" data/secret_key
# Stale write-ahead-log files would be replayed over the restored database
rm -f data/family_blogger.db-wal data/family_blogger.db-shm
rm -f data/.backup.db

# Hand the files to the user the container runs as
if ! chown -R "$PUID:$PGID" data 2>/dev/null; then
    echo "Need sudo to set the owner of data/ to $PUID:$PGID."
    sudo chown -R "$PUID:$PGID" data || {
        echo "WARNING: could not chown data/. The app may not be" >&2
        echo "able to write. Run: sudo chown -R $PUID:$PGID data" >&2
    }
fi

echo "Restored. Starting the app. Check the site, then delete"
echo "$SAFETY when happy."
