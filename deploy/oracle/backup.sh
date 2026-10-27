#!/usr/bin/env bash
# Nightly backup: a consistent copy of the database (SQLite's online backup — safe while the app is running)
# plus the saved recordings, kept for 7 days. Run by tts-backup.timer; run by hand with:
#   sudo systemctl start tts-backup
set -euo pipefail
DATA="${DATA:-/var/lib/thought-to-speak}"
DB="${TTS_DB_PATH:-$DATA/thought_to_speak.db}"
KEEP_DAYS="${KEEP_DAYS:-7}"
DEST="$DATA/backups/$(date -u +%Y-%m-%d)"
PY="${PY:-/opt/thought-to-speak/venv/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3

mkdir -p "$DEST"
"$PY" - "$DB" "$DEST/thought_to_speak.db" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
    a.backup(b)
ok = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
if ok != "ok":
    sys.exit(f"backup integrity check failed: {ok}")
EOF
# recordings never change once saved: hard-link copies cost no extra space
if [ -d "$DATA/audio" ]; then
  rm -rf "$DEST/audio"
  cp -al "$DATA/audio" "$DEST/audio"
fi
ln -sfn "$DEST" "$DATA/backups/latest"
find "$DATA/backups" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +
echo "Backup written to $DEST ($(du -sh "$DEST" | cut -f1))"
