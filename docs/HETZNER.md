# Hetzner server catalogue & deployment prep

**Server:** `168.119.88.189` (FSN1) · `Ubuntu-2204-jammy-amd64-base`  
**Last inventoried:** 2026-07-11

---

## Hardware (matches design spec)

| Resource | Actual |
|----------|--------|
| CPU | AMD Ryzen 7 3700X — 8 cores / 16 threads |
| RAM | 62 GiB total, ~61 GiB available |
| Disk | 2× NVMe ~906 GB RAID1 (`md2`), **846 GB free** (2% used) |
| Swap | 31 GiB (unused) |
| Network | `168.119.88.189/32`, WireGuard `10.66.66.1/24` |

---

## What's already running

### Docker Compose: `openclaw-mev-stack`

Location: `/home/validator/openclaw-mev-stack`  
Repo: `git@github.com:cspannos/openclaw-mev-stack.git`  
User: `validator` (in `docker` group)

| Container | Status | Host ports |
|-----------|--------|------------|
| `mev-postgres` | Up 9+ days | `127.0.0.1:5432` |
| `mev-redis` | Up 9+ days | `127.0.0.1:6379` |

**Cron (validator)** — lean MEV monitor mode:

| Schedule | Job |
|----------|-----|
| `*/30` | Base router quotes |
| `*/15` | Base spot price scan |
| `1,31 * * * *` | Telegram hard-filter alerts |
| `15 3 * * *` | Prune old observations |
| `@reboot` | Price scan warm-up |

This is your **live MEV workload**. Keep it running; do not replace its Postgres/Redis.

### Other on-box (not Docker)

| Path | Purpose |
|------|---------|
| `/home/validator/liquidq-validator` | Validator / staking tooling |
| `/home/validator/staking-deposit-cli` | Staking deposit CLI |
| `/root/eip_slack_bot` | Legacy EIP digest bot (cron placeholder path) |
| `/root/openclaw-mev-audit` | Audit artifacts |

### Network / security

| Item | State |
|------|--------|
| UFW | Active — **allow:** 22/tcp, 51820/udp (WireGuard) |
| UFW | **Not open:** 80, 443 (needed for Traefik later) |
| fail2ban | Running |
| WireGuard `wg0` | Active (`10.66.66.1`) |
| Public listeners | SSH only (+ localhost Postgres/Redis via Docker) |

### GitHub SSH on server

`validator` SSH key authenticates as **`cspannos/openclaw-mev-stack`** (deploy key).  
To clone `research-platform`, either:

1. Add `validator`’s **account** SSH key to cspannos GitHub, or  
2. Add a **deploy key** with read access to `cspannos/research-platform`, or  
3. Clone via HTTPS + PAT once.

---

## Port plan (avoid conflicts)

| Service | MEV stack (existing) | Research platform (new) |
|---------|----------------------|-------------------------|
| Postgres | `127.0.0.1:5432` | `127.0.0.1:5433` → container 5432 |
| Redis | `127.0.0.1:6379` | `127.0.0.1:6380` → container 6379 |
| API | — | `127.0.0.1:8001` + WireGuard `10.66.66.1:8001` |
| Traefik | — | `0.0.0.0:80`, `0.0.0.0:443` (after UFW + DNS) |

Use `docker-compose.hetzner.yml` override when deploying.

---

## Deployment layout (recommended)

```
/home/validator/
├── openclaw-mev-stack/     # existing MEV — leave as-is
└── research-platform/      # new multi-tenant stack
    ├── .env                # production secrets
    ├── docker-compose.yml
    ├── docker-compose.hetzner.yml
    └── data/               # caches, exports
```

Run all `docker compose` commands as **`validator`**, not root.

---

## Pre-flight checklist

### On Hetzner (once)

```bash
# As root — open HTTP/HTTPS when DNS is ready
ufw allow 80/tcp
ufw allow 443/tcp
ufw status

# As validator — clone (after fixing GitHub access)
cd ~
git clone git@github-cspannos:cspannos/research-platform.git
cd research-platform
cp .env.example .env
# edit .env — see below

bash scripts/prepare-hetzner.sh
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml build
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml up -d postgres redis platform-api scheduler
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml --profile exoplanet up -d
```

### `.env` essentials (production)

```bash
PLATFORM_DOMAIN=your.domain.com          # DNS → 168.119.88.189
PLATFORM_ADMIN_TOKEN=<long-random>
POSTGRES_PASSWORD=<strong>
REDIS_PASSWORD=<strong>

# Per-tenant Telegram bots
DEMO_TELEGRAM_BOT_TOKEN=
EXOPLANET_TELEGRAM_BOT_TOKEN=
EXOPLANET_TELEGRAM_ALLOWED_USER_IDS=

# Optional
MAST_API_TOKEN=               # https://auth.mast.stsci.edu/
OPENROUTER_API_KEY=           # review Enrich + Telegram /ask
EXOPLANET_LLM_SUMMARIES=false
EXOPLANET_ALLOW_SYNTHETIC=true  # set false after MAST works to forbid fake curves
```

### DNS records (when going public)

| Host | Points to |
|------|-----------|
| `api.your.domain` | `168.119.88.189` |
| `review.your.domain` | `168.119.88.189` |
| `traefik.your.domain` | `168.119.88.189` |

Until DNS exists, use WireGuard (preferred) or an SSH tunnel:

```bash
# On phone/laptop with WireGuard connected to the server:
# Telegram: /review  → bot replies with http://10.66.66.1:8001/review/?token=...

# Or SSH tunnel from laptop:
ssh -L 8001:127.0.0.1:8001 validator@168.119.88.189
# Review: http://127.0.0.1:8001/review/?token=YOUR_ADMIN_TOKEN
```

---

## Phased rollout on this box

| Phase | Action | Risk to MEV stack |
|-------|--------|-------------------|
| **A** | Deploy platform skeleton (postgres, redis, api, scheduler) | None — separate ports |
| **B** | Enable demo + exoplanet profiles | Low — CPU/RAM headroom ample |
| **C** | Point MEV cron into `worker-mev` tenant (future) | Medium — migrate carefully |
| **D** | Traefik + TLS when domain ready | Opens 80/443 |

**Do not** stop `mev-postgres` / `mev-redis` until MEV is migrated into the platform tenant.

---

## Exoplanet Phase A schema (vetting columns)

`create_all` alone does **not** add columns to an existing `candidates` table. After pulling Phase A code:

```bash
cd ~/research-platform
git pull
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml build platform-api worker-exoplanet bot-exoplanet
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml up -d platform-api
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml --profile exoplanet up -d worker-exoplanet bot-exoplanet
```

Schema is applied automatically on the next `init_db()` call (API/worker start that touches the DB), via idempotent:

`ALTER TABLE candidates ADD COLUMN IF NOT EXISTS …`

Manual equivalent (optional):

```bash
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d tenant_exoplanet <<'SQL'
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS t0 DOUBLE PRECISION;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS duration_hours DOUBLE PRECISION;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS odd_depth_ppm DOUBLE PRECISION;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS even_depth_ppm DOUBLE PRECISION;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS odd_even_delta_ppm DOUBLE PRECISION;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS geometry_note TEXT;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS plots_ready BOOLEAN DEFAULT FALSE;
SQL
```

Vetting PNGs live under `data/exoplanet/cache/vetting/{candidate_id}/` (mounted read-only on `platform-api`). Re-analyze a target or enqueue `exoplanet_vet_candidate_job` to backfill geometry/plots for candidates created before Phase A.

Smoke: `/ingest` → `/analyze <slug>` → open `/review` detail; expect phase-fold + periodogram images and t0/odd-even meta.

---

## Exoplanet Phase B schema (neighbours + centroid)

After pulling Phase B, rebuild `platform-api`, `worker-exoplanet`, and `bot-exoplanet` (astroquery is a new dependency). Schema adds:

```bash
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d tenant_exoplanet <<'SQL'
ALTER TABLE targets ADD COLUMN IF NOT EXISTS ra DOUBLE PRECISION;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS dec DOUBLE PRECISION;
ALTER TABLE targets ADD COLUMN IF NOT EXISTS neighbours_json TEXT;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS neighbours_json TEXT;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS centroid_json TEXT;
SQL
```

`init_db()` applies the same ALTERs once per process. TPFs cache under `data/exoplanet/cache/tpf/{slug}/` (one file per target; not refetched on every scan). Synthetic light curves never store a real centroid.

Enqueue `exoplanet_vet_neighbours_job` (or re-analyze) to backfill Gaia/centroid for existing pending candidates.

Smoke: `/review` queue cards show Approve/Reject plus neighbour count; detail shows the neighbour table and centroid pass/fail/unavailable.

---

## Exoplanet Phase C schema (statistical validation)

After pulling Phase C, rebuild `platform-api`, `worker-exoplanet`, and `bot-exoplanet`. Schema adds:

```bash
docker compose -f docker-compose.yml -f docker-compose.hetzner.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d tenant_exoplanet <<'SQL'
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS validation_json TEXT;
SQL
```

`init_db()` applies the same ALTER once per process.

**Default off.** Set `EXOPLANET_TRICERATOPS=true` on the host `.env` and recreate `worker-exoplanet` (and `bot-exoplanet` / `platform-api` for the UI command). `worker-exoplanet` is built with `INSTALL_TRICERATOPS=true` (TRICERATOPS from GitHub, NumPy 2 shim) and stays at **1 CPU / 4 GB**; validation jobs go on the `exoplanet-validate` RQ queue (same Redis DB, same worker process, longer `job_timeout`).

Runtime (document for reviewers):

| Path | Typical time | When |
|------|----------------|------|
| Flag off / SNR or period gate fail / Kepler / synthetic | milliseconds | Job records `unavailable` + reason |
| Equivalent FPP (package not installed) | < 1 s | Uses odd/even + Gaia dilution + centroid already stored |
| Real `triceratops` (`pip install triceratops`, N=20_000, `parallel=False`) | typically **5–30 min** on 1 CPU | High-SNR TESS with TIC id; timeout `EXOPLANET_VALIDATE_TIMEOUT_S` (default 900 s) |

**Never part of `/scan`.** Trigger from `/review` “Run validation” or Telegram `/vet-validate <id>`. Failures store `status=unavailable` plus a short `error` snippet.

Smoke: pick a high-SNR TESS candidate on `/review`, click Run validation (or `/vet-validate <id>`), expect FPP/NFPP (or unavailable + reason) on the detail panel and in Enrich / `/ask` context. `/scan` must still return without waiting for this job.

---

## Resource headroom

| | Used | Available | Platform budget |
|---|------|-----------|-----------------|
| RAM | ~0.6 GB | ~61 GB | ~40 GB planned ceiling |
| Disk | 14 GB | 846 GB | Postgres + 80 GB exoplanet cache |
| CPU | Minimal idle | 16 threads | ≤4 heavy workers concurrent |

Plenty of room for research-platform alongside lean MEV monitor.

---

## VPN admin access

From laptop (WireGuard peer):

```bash
ssh validator@10.66.66.1
# Review UI (same VPN): http://10.66.66.1:8001/review/?token=YOUR_ADMIN_TOKEN
# Or ask the exoplanet bot: /review
```

WireGuard must be active on the client; UFW does not expose 8001 publicly.
