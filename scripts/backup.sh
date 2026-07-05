#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ROOT_DIR}/data/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

source "${ROOT_DIR}/.env"

EXCLUDE_COLLECTIVE="${EXCLUDE_COLLECTIVE_FROM_BACKUP:-true}"

echo "Backing up Postgres to ${BACKUP_DIR}"
if [[ "${EXCLUDE_COLLECTIVE}" == "true" ]]; then
  echo "Excluding tenant_collective (EXCLUDE_COLLECTIVE_FROM_BACKUP=true)"
  DATABASES=(
    "${POSTGRES_DB}"
    tenant_demo
    tenant_mev
    tenant_anomaly
    tenant_exoplanet
  )
  for db in "${DATABASES[@]}"; do
    echo "  dumping ${db}..."
    docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" -d "${db}" \
      > "${BACKUP_DIR}/postgres-${db}.sql"
  done
  echo "Collective DB not included. Back up tenant_collective separately if needed."
else
  docker compose exec -T postgres pg_dumpall -U "${POSTGRES_USER}" > "${BACKUP_DIR}/postgres-all.sql"
fi

echo "Backing up Redis RDB snapshot"
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning SAVE
docker compose cp redis:/data/dump.rdb "${BACKUP_DIR}/redis-dump.rdb"

if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
  echo "Upload to object storage is configured but not implemented in scaffold."
  echo "Wire rclone or aws-cli here for off-box retention."
fi

echo "Backup complete: ${BACKUP_DIR}"
