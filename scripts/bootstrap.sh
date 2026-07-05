#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit secrets before production use."
fi

docker compose build
docker compose up -d postgres redis platform-api worker-demo scheduler
docker compose up -d bot-demo

echo
echo "Platform skeleton is up."
echo "  API health: http://127.0.0.1:8000/health/live"
echo "  Tenants:    http://127.0.0.1:8000/tenants"
echo
echo "Next: set DEMO_TELEGRAM_BOT_TOKEN and DEMO_TELEGRAM_ALLOWED_USER_IDS in .env,"
echo "then run: docker compose up -d bot-demo"
