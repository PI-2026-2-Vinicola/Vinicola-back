#!/usr/bin/env bash
# Backup da OASIS em produção: banco PostgreSQL (pg_dump) e fotos/miniaturas (volume "storage").
#   deploy/backup.sh                 # grava em backups/ (ao lado do docker-compose.prod.yml)
#   deploy/backup.sh /mnt/backup     # outra pasta
# Mantém os últimos OASIS_BACKUP_KEEP_DAYS dias (padrão 14). Agende no cron e copie a pasta para fora
# do servidor: um backup no mesmo disco não protege contra a perda do servidor (docs/producao.md).
set -euo pipefail
umask 077  # o dump tem dados pessoais e hashes de senha: só o dono lê

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/backups}"
KEEP_DAYS="${OASIS_BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DB_FILE="$DEST/oasis-banco-$STAMP.dump"
PHOTOS_FILE="$DEST/oasis-fotos-$STAMP.tar.gz"

compose() { docker compose -f "$ROOT/docker-compose.prod.yml" "$@"; }

mkdir -p "$DEST"
trap 'rm -f "$DB_FILE.partial" "$PHOTOS_FILE.partial"' EXIT

compose exec -T db pg_dump -U oasis -d oasis --format=custom > "$DB_FILE.partial"
mv "$DB_FILE.partial" "$DB_FILE"
compose exec -T api tar -C /srv/storage -czf - . > "$PHOTOS_FILE.partial"
mv "$PHOTOS_FILE.partial" "$PHOTOS_FILE"

find "$DEST" -maxdepth 1 -type f -name 'oasis-*' -mtime "+$KEEP_DAYS" -delete
echo "$(date '+%F %T') backup concluído: $DB_FILE ($(du -h "$DB_FILE" | cut -f1)) e $PHOTOS_FILE ($(du -h "$PHOTOS_FILE" | cut -f1))"
