#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${ROOT_DIR}/data/exoplanet/cache"

echo "Exoplanet tenant setup"
echo "  1. Set EXOPLANET_TELEGRAM_* in .env"
echo "  2. Optional: set MAST_API_TOKEN for MAST metadata queries"
echo "  3. Edit curated targets: projects/exoplanet/config/targets.yaml"
echo
echo "Start exoplanet services:"
echo "  docker compose --profile exoplanet up -d bot-exoplanet worker-exoplanet"
echo
echo "Review dashboard:"
echo "  https://review.\${PLATFORM_DOMAIN:-localhost}/review/?token=\${PLATFORM_ADMIN_TOKEN}"
echo "  local: http://127.0.0.1:8000/review/?token=YOUR_ADMIN_TOKEN"
