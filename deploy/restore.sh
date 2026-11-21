#!/usr/bin/env bash
# Restaura um backup feito por deploy/backup.sh. SUBSTITUI o banco e as fotos atuais.
#   deploy/restore.sh backups/oasis-banco-AAAAMMDD-HHMMSS.dump backups/oasis-fotos-AAAAMMDD-HHMMSS.tar.gz
# A API fica parada durante a restauração. Para não pedir confirmação: OASIS_RESTORE_CONFIRM=sim
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose() { docker compose -f "$ROOT/docker-compose.prod.yml" "$@"; }

if [ $# -ne 2 ] || [ ! -f "$1" ] || [ ! -f "$2" ]; then
  echo "uso: deploy/restore.sh <oasis-banco-....dump> <oasis-fotos-....tar.gz>" >&2
  exit 2
fi
DB_FILE="$1"
PHOTOS_FILE="$2"

if [ "${OASIS_RESTORE_CONFIRM:-}" != "sim" ]; then
  read -r -p "Isto substitui o banco e as fotos atuais pelos do backup. Digite RESTAURAR para continuar: " answer
  [ "$answer" = "RESTAURAR" ] || { echo "Cancelado."; exit 1; }
fi

compose up -d --wait db
compose stop api
trap 'compose up -d api >/dev/null' EXIT

compose exec -T db pg_restore -U oasis -d oasis --clean --if-exists --no-owner --single-transaction < "$DB_FILE"
compose run --rm --no-deps -T api sh -c 'find /srv/storage -mindepth 1 -delete && tar -C /srv/storage -xzf -' < "$PHOTOS_FILE"

compose up -d --wait api
trap - EXIT
echo "Restauração concluída a partir de $DB_FILE e $PHOTOS_FILE."
