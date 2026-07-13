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
| API | — | `127.0.0.1:8001` |
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

Until DNS exists, use SSH tunnels:

```bash
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
# or
ssh root@168.119.88.189
```

WireGuard was **not** active on the local machine during inventory; use public IP or bring up `wg0` first.
