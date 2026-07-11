#!/usr/bin/env bash
# Run on Hetzner as validator after cloning research-platform.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Research platform — Hetzner prep ==="

if [[ "$(id -un)" != "validator" ]]; then
  echo "Warning: run as user 'validator' (has docker group). Current: $(id -un)"
fi

# Directories
mkdir -p data/exoplanet/cache data/collective/exports data/backups
mkdir -p secrets/collective

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — edit secrets before production use."
fi

# Collective secrets template
if [[ ! -f secrets/collective/collective.env ]]; then
  cp secrets/collective/collective.env.example secrets/collective/collective.env
  chmod 600 secrets/collective/collective.env
  echo "Created secrets/collective/collective.env"
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.hetzner.yml)

echo
echo "Checking for port conflicts with openclaw-mev-stack..."
for port in 5432 6379; do
  if ss -tln | grep -q "127.0.0.1:${port} "; then
    echo "  OK: ${port} in use by MEV stack (expected)"
  fi
done
for port in 5433 6380 8000; do
  if ss -tln | grep -q ":${port} "; then
    echo "  WARN: port ${port} already bound — check before deploy"
  else
    echo "  OK: port ${port} free for research-platform"
  fi
done

echo
echo "Build and start core stack:"
echo "  ${COMPOSE[*]} build"
echo "  ${COMPOSE[*]} up -d postgres redis platform-api scheduler"
echo
echo "Optional tenants:"
echo "  ${COMPOSE[*]} --profile exoplanet up -d bot-exoplanet worker-exoplanet"
echo "  ${COMPOSE[*]} --profile collective up -d bot-collective worker-collective"
echo
echo "Health check:"
echo "  curl -s http://127.0.0.1:8000/health/live"
echo
echo "See docs/HETZNER.md for full catalogue and DNS/UFW steps."
