# Exoplanet Vetting — Implementation Roadmap Prompt

Copy this document (or a single **Phase** section) into a Cursor agent chat when you want to implement the next slice. Do one phase at a time unless explicitly told otherwise.

---

## Agent system context (always include)

You are implementing transit-candidate **vetting** for the multi-tenant research platform at `/home/delegate0x/research-platform` (deployed on Hetzner as `research-platform`, coexisting with `openclaw-mev-stack` — do not disturb MEV).

### Product goal

Turn pending transit candidates into **reviewable science objects**: diagnostic plots, neighbour/contamination checks, archive metadata, and structured checklist results — surface them on the WireGuard `/review` dashboard and feed the same facts into Telegram `/ask` + review Enrich (shared exoplanet expert LLM).

Telegram remains a **trigger + digest** channel, not a plot viewer.

### Current baseline (do not regress)

| Area | Today |
|------|--------|
| Ingest | MAST LC → cache `{slug}.npz` with `time`, `flux`, `flux_err`, `source` |
| Detect | Lomb–Scargle → `Candidate(period_days, depth_ppm, snr, flag_reason, status)` |
| Review | HTMX `/review` — list, approve/reject, comments, Enrich LLM |
| Ask | Exoplanet bot `/ask` + free-text via shared `expert.py` |
| Access | API on `127.0.0.1:8001` + `10.66.66.1:8001`; bot `/review` returns token URL |
| Caps | Cached LC ≤ ~25k points; no full mission archives; RQ workers; low resource limits |

### Hard constraints

- Prefer extending existing packages (`projects/exoplanet/`, `research_platform/api/review_dashboard.py`, workers/bots) over new microservices.
- Heavy work only in `worker-exoplanet` RQ jobs; idempotent and timeout-bounded.
- Graceful degradation: missing TPF / Gaia / API → mark metric `unavailable`, still show what you can.
- No secrets in git; recreate containers after `.env` changes on server.
- Keep Hetzner CPU/RAM modest — queue TRICERATOPS-class jobs separately, never on every `/scan`.
- Tests for new formatting / API paths; deploy pattern: commit → push main → `git pull` on server → rebuild/recreate affected services.
- Match existing code style; no drive-by refactors.

### Key paths

- `projects/exoplanet/db/models.py`
- `projects/exoplanet/pipelines/{ingest,mast_client,analysis,summaries,enrich,expert,cache_manager}.py`
- `projects/exoplanet/workers/jobs.py`
- `projects/exoplanet/config/targets.yaml`
- `research_platform/api/review_dashboard.py`
- `research_platform/templates/review/`
- `research_platform/bots/exoplanet_bot.py`
- `docker-compose.yml` / `docker-compose.hetzner.yml`

### Conceptual map (from product brief)

1. **Vetting plots** — phase-fold, odd/even, transit zooms, periodogram/window, centroid/pixel when TPF exists  
2. **Neighbour checks** — Gaia cone, aperture inspection, dilution, centroid offset  
3. **Archive metadata** — stellar params, known EBs, observing flags, prior TOI/KOI/candidates  

Use OSS where it fits: **Lightkurve**, **Astropy**, **astroquery** (Gaia / Exoplanet Archive / SIMBAD), **`vetting`** (centroid), **TRICERATOPS** (Phase C only). Prefer first-party matplotlib plots from cached LC before wrapping heavy validation stacks.

---

## Master roadmap (phases)

| Phase | Name | Outcome | Depends on |
|-------|------|---------|------------|
| **A** | Diagnostic plots + geometry from cached LC | `t0`/duration/odd-even + PNGs on `/review` | Baseline |
| **B** | Neighbours + centroid | Gaia/TIC dilution + optional TPF centroid | A (RA/Dec / geometry) |
| **C** | Statistical validation (selective) | TRICERATOPS FPP for high-SNR TESS | B |
| **D** | Workflow polish | Checklist UI, `/vet`, LLM context, digests | A–C |

Do **not** start Phase C until A and B land and are deployed.

---

## Phase A — Plots + transit geometry (implement first)

### Prompt

```text
Implement Phase A of docs/VETTING_ROADMAP_PROMPT.md for the exoplanet tenant.

Goal:
- Extend detection/analysis so candidates store transit geometry (at least epoch t0, duration estimate, odd/even depth comparison).
- Generate diagnostic PNGs from existing cached light curves (time/flux) and show them on the /review candidate detail panel.
- Feed the new numeric metrics into review Enrich / Telegram expert context.

Scope (in):
- projects/exoplanet analysis pipeline + Candidate (or sibling VettingArtifact) schema
- Persist plot files under a platform cache path (e.g. data/exoplanet/vetting/{candidate_id}/)
- API routes to serve plot images to /review (auth same as review token)
- Templates: embed phase-fold, odd/even, and periodogram (or window) plots
- RQ job or extend existing analyze/scan path so plots are created when a candidate is flagged
- Unit tests for geometry helpers and plot path wiring
- Minimal Telegram mention (e.g. “vetting plots ready” in analyze reply) — optional

Scope (out):
- TPF download, Gaia, TRICERATOPS, ExoFOP
- Redesigning the whole review UI
- Changing Hetzner networking / MEV stack

Acceptance criteria:
1. After /scan (or dedicated vet job), a pending candidate has t0 + odd/even metrics stored (or explicit null + reason).
2. /review detail shows at least 2 plots for that candidate when LC cache exists.
3. Enrich /ask context includes the new metrics when present.
4. Missing cache → clear unavailable message, no crash.
5. Tests pass; deploy rebuilds bot-exoplanet / worker-exoplanet / platform-api as needed.

Implementation notes:
- Prefer matplotlib PNGs; keep point count modest.
- Prefer ephemeral/computed plots from npz over huge DB blobs.
- Alembic/simple migrate path if models change — document how to apply on Hetzner.
```

### Done when

- [x] Geometry fields stored  
- [x] Plots visible on `/review`  
- [x] LLM context updated  
- [ ] Deployed and smoke-tested on one real candidate  

---

## Phase B — Neighbour checks + centroid

### Prompt

```text
Implement Phase B of docs/VETTING_ROADMAP_PROMPT.md.

Goal:
- Resolve target coordinates (RA/Dec from TIC/KIC or catalog).
- Run Gaia (astroquery) cone search ~1 arcmin; estimate dilution / bright-neighbour risk.
- Optionally download one TPF via lightkurve (or MAST) per target/sector; run centroid test (prefer `vetting` package or lightkurve-based equivalent).
- Persist neighbour + centroid JSON on the candidate/target; show summary on /review; inject into expert context.

Scope (in):
- TargetSpec / targets.yaml / Target model: ra, dec (or resolve at job time and cache)
- New RQ job vet_neighbours(candidate_id) or combine with Phase A artifacts
- Cache policy: one TPF per target/sector max; don’t refetch every scan
- Review UI: neighbour table + centroid pass/fail/unavailable
- Graceful skip when no network / no TPF

Scope (out):
- Full TRICERATOPS / VESPA
- Bulk FFI analysis

Acceptance criteria:
1. Curated targets get a neighbour summary (count, brightest Δmag, dilution estimate or unavailable).
2. When TPF available, centroid p-value (or offset) stored and shown.
3. Jobs are idempotent and bounded; synthetic LCs don’t pretend to have real centroids.
4. Tests mock Gaia/TPF; deploy worker + api.
```

### Done when

- [ ] Gaia (or catalog) neighbours on detail panel  
- [ ] Centroid result when TPF exists  
- [ ] Expert context includes contamination flags  

---

## Phase C — Selective statistical validation

### Prompt

```text
Implement Phase C of docs/VETTING_ROADMAP_PROMPT.md.

Goal:
- Add optional TRICERATOPS (or equivalent) job for TESS candidates above an SNR/period gate.
- Persist FPP / nearby-EB probabilities; never block /scan on this job.
- Surface scores on /review; allow manual “Run validation” from UI or Telegram /vet-validate <id>.

Constraints:
- Separate RQ queue or long timeout; CPU limit respected
- Default off unless EXOPLANET_TRICERATOPS=true (or similar)
- Document runtime expectations

Acceptance criteria:
1. High-SNR TESS candidate can obtain FPP without breaking normal scan.
2. Failures are recorded as unavailable with error snippet.
3. Scores appear in review + LLM context.
```

### Done when

- [ ] Opt-in validation job works on at least one TOI-like target  
- [ ] Feature flag documented in `.env.example`  

---

## Phase D — Workflow polish

### Prompt

```text
Implement Phase D of docs/VETTING_ROADMAP_PROMPT.md.

Goal:
- Structured vetting checklist on /review (plots / odd-even / neighbours / centroid / archive / FPP) with pass|fail|unclear|unavailable.
- Telegram /vet <candidate_id> short digest.
- Archive metadata enrichment: NASA Exoplanet Archive / TIC stellar params / SIMBAD known-EB flags (cached).
- Optional digest in /notify for newly vetted candidates.

Acceptance criteria:
1. Checklist drives human approve/reject; stored with candidate.
2. /vet returns a readable multi-line verdict.
3. Archive fields cached with TTL; LLM uses them when present.
```

### Done when

- [ ] Checklist in UI  
- [ ] `/vet` on exoplanet bot  
- [ ] Archive lookups for curated targets  

---

## Suggested session order

1. Paste **Agent system context** + **Phase A prompt** → implement, test, deploy, smoke `/review`.  
2. Same for **Phase B**.  
3. Only then **Phase C** if you want formal FPP.  
4. **Phase D** anytime after A (can partially overlap B).

## Definition of done (whole program)

A reviewer on WireGuard can open `/review`, see diagnostic plots + neighbour/centroid/archive context for a pending candidate, ask the bot intelligent follow-ups grounded in those facts, and optionally run statistical validation for TESS targets — without opening 8001 to the public internet or impacting the MEV stack.
