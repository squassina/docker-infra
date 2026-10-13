#!/usr/bin/env bash
set -euo pipefail

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/mnt/backup/restic-homelab}"   # ou sftp:user@host:/caminho
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/root/.restic-pass}"      # chmod 600

CONFIG_DIR="${CONFIG_DIR:-/var/lib/dockerdata}"
INFRA_DIR="${INFRA_DIR:-/opt/docker-infra}"
MEDIA_COMPOSE="$INFRA_DIR/stack-media/docker-compose.yml"

# Bancos SQLite (arrs, Jellyfin) só ficam consistentes com o app parado.
docker compose -f "$MEDIA_COMPOSE" stop
trap 'docker compose -f "$MEDIA_COMPOSE" start' EXIT

restic backup "$CONFIG_DIR" "$INFRA_DIR" \
  --exclude "$CONFIG_DIR/jellyfin/cache" \
  --exclude "$CONFIG_DIR/prometheus/data" \
  --exclude '*/MediaCover' \
  --exclude '*/logs'

restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
restic check --read-data-subset=5%
