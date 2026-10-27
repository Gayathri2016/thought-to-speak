#!/usr/bin/env bash
# Ship a new version: back up, pull the latest code, restart, and roll back automatically if it doesn't start.
#   sudo bash /opt/thought-to-speak/app/deploy/oracle/update.sh
set -euo pipefail
ROOT="${ROOT:-/opt/thought-to-speak}"
APP="$ROOT/app"
BRANCH="${BRANCH:-main}"

[ "$(id -u)" -eq 0 ] || [ -n "${TTS_UPDATE_TEST:-}" ] || { echo "run with sudo"; exit 1; }
healthy() {
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

echo "==> Backing up first"
systemctl start tts-backup

PREV=$(sudo -u tts git -C "$APP" rev-parse HEAD)
echo "==> Updating from $(sudo -u tts git -C "$APP" log -1 --format='%h %s')"
sudo -u tts git -C "$APP" fetch --quiet origin "$BRANCH"
sudo -u tts git -C "$APP" checkout --quiet "$BRANCH"
sudo -u tts git -C "$APP" merge --quiet --ff-only "origin/$BRANCH"
NEW=$(sudo -u tts git -C "$APP" rev-parse HEAD)
if [ "$PREV" = "$NEW" ]; then echo "Already up to date."; exit 0; fi

sudo -u tts "$ROOT/venv/bin/pip" install --quiet -r "$APP/requirements.txt"
for unit in thought-to-speak.service tts-backup.service tts-backup.timer; do
  install -m 644 "$APP/deploy/oracle/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl restart thought-to-speak

if healthy; then
  echo "==> Live: $(sudo -u tts git -C "$APP" log -1 --format='%h %s')"
else
  echo "==> The new version didn't start — rolling back to ${PREV:0:7}"
  journalctl -u thought-to-speak -n 30 --no-pager || true
  sudo -u tts git -C "$APP" reset --quiet --hard "$PREV"
  sudo -u tts "$ROOT/venv/bin/pip" install --quiet -r "$APP/requirements.txt"
  systemctl restart thought-to-speak
  healthy && echo "Rolled back; the previous version is running." || echo "Still not healthy — check: journalctl -u thought-to-speak"
  exit 1
fi
