#!/usr/bin/env bash
# Run on YOUR computer (not the server): copy the latest nightly backup off the server, so a lost or reclaimed
# server never means lost accounts. Do it weekly.
#   bash deploy/oracle/pull-backup.sh ubuntu@<server-ip> [~/.ssh/oracle_key]
set -euo pipefail
HOST="${1:?usage: pull-backup.sh ubuntu@<server-ip> [ssh-key]}"
KEY="${2:-}"
DEST="${BACKUP_DIR:-$HOME/thought-to-speak-backups}/$(date +%Y-%m-%d)"
mkdir -p "$DEST"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
[ -n "$KEY" ] && SSH_OPTS+=(-i "$KEY")
rsync -az -e "ssh ${SSH_OPTS[*]}" "$HOST:/var/lib/thought-to-speak/backups/latest/" "$DEST/"
echo "Saved to $DEST ($(du -sh "$DEST" | cut -f1))"
