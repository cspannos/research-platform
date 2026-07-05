#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${ROOT_DIR}/secrets/collective"
ENV_FILE="${SECRETS_DIR}/collective.env"

mkdir -p "${ROOT_DIR}/data/collective/exports"

if [[ -f "${ENV_FILE}" ]]; then
  echo "Collective secrets already exist: ${ENV_FILE}"
else
  cp "${SECRETS_DIR}/collective.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created ${ENV_FILE} — edit with anonymous GitHub credentials."
fi

echo
echo "Collective setup checklist:"
echo "  1. Create anonymous GitHub account (no personal email)"
echo "  2. Create repo under that account only"
echo "  3. Set COLLECTIVE_GIT_USER_EMAIL to GitHub noreply address"
echo "  4. Create fine-grained PAT scoped to that repo"
echo "  5. Keep COLLECTIVE_PUBLISH_ENABLED=false until ready"
echo "  6. Set COLLECTIVE_TELEGRAM_* in root .env"
echo
echo "Start collective tenant:"
echo "  docker compose --profile collective up -d bot-collective worker-collective"
