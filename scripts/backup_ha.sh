#!/bin/bash
# =====================================================================
# Autoflow backup
# =====================================================================
#
# Replaces the original backup_ha.sh, which ran nightly at 03:00 and failed
# every time:
#
#     rclone copy ... gcp:<BUCKET_NAME>/backups/   <- "can't make bucket
#                                                       without project number"
#     rm $BACKUP_DIR/$FILENAME                       <- ran anyway
#     echo "Backup complete!"                        <- printed anyway
#
# The rclone remote was never fully configured, so probably no backup was ever
# produced. Nothing said so: no error handling, and the log went to /tmp.
#
# MUST RUN AS ROOT
#
# Home Assistant runs as root inside its container, so .storage is root-owned
# and secrets.yaml is mode 600. Running as USER, tar cannot read them -
# which is how the first attempt at this script failed. The cron entry belongs
# in root's crontab, not the user's.
#
# WHAT THIS DOES DIFFERENTLY FROM THE ORIGINAL
#
#   * set -euo pipefail - stops at the first failure
#   * reads the archive back before trusting it
#   * deletes nothing until the new archive is verified
#   * includes the HOST files - relay_api.py, screen_dim.py, watchdog.py, the
#     systemd units, the compose file - which are as hard to recreate as YAML
#   * snapshots the database with sqlite3 .backup, safe on a live file; a
#     plain copy of a database mid-write can restore into something Home
#     Assistant refuses to start with
#   * writes a status file so the watchdog notices when backups go stale
#
# WHAT IT DOES NOT COVER
#
# It writes to the same SD card it is backing up. That saves you from breaking
# the config, not from the card failing. A USB stick and a card clone are the
# other half of the job.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "This must run as root - .storage and secrets.yaml are root-owned." >&2
    echo "Try:  sudo $0" >&2
    exit 1
fi

SRC_CFG=/home/USER/homeassistant/config
DEST=/home/USER/backups
KEEP=7
STATUS="$SRC_CFG/backup_status.json"
STAMP=$(date +%Y-%m-%d_%H%M)
WORK=$(mktemp -d)
ARCHIVE="$DEST/autoflow_$STAMP.tar.gz"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

fail() {
    cat > "$STATUS" <<EOF
{"ok": false, "when": "$(date '+%Y-%m-%d %H:%M:%S')", "error": "$1", "size_mb": 0}
EOF
    chmod 644 "$STATUS" 2>/dev/null || true
    echo "BACKUP FAILED: $1" >&2
    exit 1
}

mkdir -p "$DEST" "$WORK/config" "$WORK/host"

# Errors are NOT silenced here. The first version sent them to /dev/null and
# turned "tar could not read half your config" into an unexplained one-line
# failure.
tar -cf - -C "$SRC_CFG" \
    --exclude='home-assistant_v2.db*' \
    --exclude='home-assistant.log*' \
    --exclude='.cache' \
    --exclude='tts' \
    --exclude='deps' \
    --exclude='backups' \
    . | tar -xf - -C "$WORK/config" || fail "could not read config (see errors above)"

# Long-term statistics live in the database - runtime hours, efficiency, start
# counts. Worth the extra step rather than excluding it.
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$SRC_CFG/home-assistant_v2.db" \
        ".backup '$WORK/config/home-assistant_v2.db'" \
        || echo "  (database snapshot failed - continuing without it)"
else
    echo "  (sqlite3 not installed - database not included)"
fi

for f in relay_api.py screen_dim.py screen_lock.py watchdog.py \
         brightness_bridge.sh backup_ha.sh display_diagnostic.sh \
         change_pzem_address.py test_all_pzem.py; do
    [ -f "/home/USER/$f" ] && cp -p "/home/USER/$f" "$WORK/host/"
done
cp -p /home/USER/homeassistant/docker-compose.yml "$WORK/host/" 2>/dev/null || true
mkdir -p "$WORK/host/systemd"
for u in relayapi.service screendim.service dvralarm.service \
         autoflow-watchdog.service autoflow-watchdog.timer; do
    [ -f "/etc/systemd/system/$u" ] && cp -p "/etc/systemd/system/$u" "$WORK/host/systemd/"
done
crontab -l > "$WORK/host/crontab-root.txt" 2>/dev/null || true
crontab -u USER -l > "$WORK/host/crontab-USER.txt" 2>/dev/null || true
cp -p /etc/chrony/conf.d/autoflow-camera-lan.conf "$WORK/host/" 2>/dev/null || true

tar -czf "$ARCHIVE" -C "$WORK" . || fail "could not create archive"
tar -tzf "$ARCHIVE" > /dev/null 2>&1 || fail "archive is unreadable - not keeping it"

SIZE_MB=$(( $(stat -c%s "$ARCHIVE") / 1024 / 1024 ))
[ "$SIZE_MB" -lt 1 ] && fail "archive is suspiciously small (${SIZE_MB} MB)"

# Only now is it safe to prune.
ls -1t "$DEST"/autoflow_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
COUNT=$(ls -1 "$DEST"/autoflow_*.tar.gz 2>/dev/null | wc -l)

cat > "$STATUS" <<EOF
{"ok": true, "when": "$(date '+%Y-%m-%d %H:%M:%S')", "error": null,
 "size_mb": $SIZE_MB, "kept": $COUNT, "path": "$ARCHIVE"}
EOF
chmod 644 "$STATUS"

echo "Backup complete: $ARCHIVE (${SIZE_MB} MB, keeping $COUNT)"
