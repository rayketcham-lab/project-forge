# Project Forge

![Version](https://img.shields.io/badge/version-0.23-blue) ![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/rayketcham-lab/project-forge/actions/workflows/ci.yml/badge.svg) ![Tests](https://img.shields.io/badge/tests-1860+-passing?color=brightgreen) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**[Quickstart](#quick-start) · [Boards](#the-six-boards) · [Dashboard](#dashboard) · [Labs](#labs--autonomous-avenues-v017) · [Architecture](#architecture) · [Config](#configuration) · [Roadmap](#roadmap)**

An autonomous project idea generator. It runs an in-process scheduler inside the FastAPI app, calls an LLM (or falls back to deterministic heuristics), scores ideas on **six orthogonal axes**, deduplicates aggressively, and stores everything in SQLite. A web dashboard lets a human review, approve, and — with a single click — promote ideas into GitHub issues with full MVP specs.

**Promotion is human-gated**: the engine ranks and surfaces, you approve.

| Axis | Question it answers | Sorts |
|------|--------------------|-------|
| `feasibility_score` | Can we build it? | universal, required |
| `fundability_score` | Can we sell it? | `/money-bots`, `/crypto` |
| `ambition_score` | Does it push the frontier? | `/claude-lab` |
| `snipe_score` | Can we wedge into a proven incumbent? | `/sniper` |
| `cashflow_score` | How fast does it become actual dollars? | `/cashflow` |
| `pki_urgency_score` | Does this matter to the certificate industry? | `/pki` |

> Operating philosophy: **autonomous, human-driven**. The engine generates, scores, dedups, sweeps, and audits itself on a schedule. Anything that touches external state (GitHub issues, repos) is one click away — never autonomous. The v0.14 weekly auto-promote cadence was removed in v0.14b after a uvicorn-reload bug fired it three times. Nothing can flip an idea to `approved` without an operator.

This is a personal project that's been running for several months. It's open-sourced because some of the patterns (LLM backend abstraction, multi-stage dedup, persona-driven generation, multi-axis scoring, web-grounded competitive comps, in-process multi-cadence scheduling, admission-gated generation) might be useful to others. It's not a product — no support, no SLA, no promises about your timeline.

> [!NOTE]
> **No API key required.** The LLM backend resolver picks the best path automatically:
> 1. **Anthropic API** when `ANTHROPIC_API_KEY` is set
> 2. **Claude Code CLI** when `claude` is on `$PATH` (uses your Claude subscription)
> 3. **Static heuristics** when neither
>
> Override via `FORGE_LLM_BACKEND={api|claude_code|static}` and `FORGE_LLM_MODEL={sonnet|opus|haiku}`.
>
> **On the CLI path the cheap-path resolver returns Opus**, not Haiku — there's no per-call cost on a subscription, so the strongest model wins for generation, scoring tie-breaks, and semantic-dedup verification. API-path users get Haiku 4.5 there for cost discipline. Override via `FORGE_CLI_MODEL`.
>
> `GET /api/backend-info` returns which backend is live plus a censored view of the API-key env vars the process can see.

---

## The six boards

Each board frames the corpus with its own question, its own category family, and its own scoring axis. Board membership is centralized in `models.py` so every surface — routes, stats, churn, promotion — stays in lockstep.

| Board | Question | Categories | Sorted by |
|-------|----------|-----------|-----------|
| **/money-bots** | Can we sell it? | 8 money-friendly (automation-income, creator-tools, consumer-app, productivity, micro-saas, vertical-saas, ecommerce-tools, fintech-tools) | `fundability_score` |
| **/claude-lab** | Does it push the frontier? | 6 Claude/agent (claude-skills-agents, ai-marketplace, agent-infra, claude-evals, agent-security, context-memory) | `ambition_score` |
| **/sniper** | Can we take a slice of a proven market? | 14 hunting grounds (the money categories + fat-incumbent IT/security) | `snipe_score` |
| **/crypto** | Where are the real crypto budgets? | 5 on-chain (onchain-security, web3-infra, defi-tooling, stablecoin-payments, crypto-compliance) | `fundability_score` |
| **/cashflow** | How soon is the first invoice? | 5 folding-cash (productized-services, digital-products, commerce-ops, lead-generation, flipping-arbitrage) | `cashflow_score` |
| **/pki** | Does this matter to the industry? | 5 certificate-infra (pki-revocation, cert-lifecycle, pqc-migration, ca-operations, cert-identity) | `pki_urgency_score` |

Every board has a **Churn Now** button that fires the generator on demand against the right category family and the right axis. On /claude-lab, Churn rotates through 8 artifact shapes; on /sniper, through 7 wedge angles — so each click produces a meaningfully different starting frame.

### /pki — the selective board (v0.23)

The other five boards always produce something. The PKI board is built the opposite way, and it is the only board that is **allowed to come back empty**.

An hourly cadence runs a grounded probe over IETF Datatracker feeds (LAMPS, TLS, ACME, PQUIP) and open issues across six implementations that eat certificate pain first (cert-manager, step-ca, OpenSSL, rustls, SPIRE, cosign). It picks the **single** highest-leverage gap it found, works that one gap hard, and then applies an admission gate:

```
probe → pick ONE gap → generate one spec-grade item → gate → store or DROP
```

Three ways to fail, all deliberate: wrong board, **no concrete anchor** (an RFC, draft name, CA/B Forum ballot, CVE, or tracker URL — something a skeptic could go read), or a `pki_urgency_score` below the admit threshold. There is no fallback generation. Most hours store nothing.

`pki_urgency_score` is **deadline pressure × blast radius × how badly today's tooling fails** — deliberately not a money question, because fundability would rank a certificate dashboard above a CRL-partitioning planner.

Because an empty board is indistinguishable from a broken one, every attempt is written to a `pki_probes` table and rendered as a **probe log** on the page (also at `GET /api/pki/probes`), with the admission rate. The log doubles as the cadence watermark — keying the schedule off *stored ideas* would leave a mostly-silent cadence permanently overdue and re-firing every tick.

### /missions — operator-directed generation (v0.18)

Boards generate from the engine's own rotation. **Missions** invert that: you write a brief (plus up to 3 grounding URLs), and a 4-hourly cadence round-robins over active missions, anchoring generation to your directive. Ideas carry `mission_id` so each mission gets its own grid. Useful when you want the engine pointed at a specific thesis rather than its own priorities.

### /mechanic — autonomous self-improvement (v0.22)

The engine proposing patches to *itself*. A disarmed-by-default cadence selects a Think Tank item, clones the repo to an isolated temp dir, runs a Claude agent against it, gates the result, and opens a **PR** — never a merge. `/mechanic` is the review panel. It is off unless `FORGE_MECHANIC_ENABLED` is set, and it touches no GitHub state beyond opening a PR for human review.

---

## What it actually does

| Step | Implementation |
|------|---------------|
| **Generate** | Two paths. The **LLM-first generator** (`engine/llm_generator.py`) asks the configured cheap-path model for a whole idea using one of 5 modes (novel / inversion / bundle / microservice / adversarial), a category-specific persona, and anti-similarity injection (the 30 most-recent active names — "do NOT produce anything like these"). Claude Lab categories additionally pick one of 8 artifact shapes. The **template generator** (`cron/auto_scan.py`) is the deterministic fallback when no backend is reachable. |
| **Ground** | Several paths inject live, keyless signal into prompts before generation: `feeds/market_intel.py` (HN + GitHub challenger stars, for Sniper), `feeds/pulse.py` (HN front page + GitHub trending, for Pulse), `feeds/pki_probe.py` (IETF drafts + implementation trackers, for PKI), and the NVD / arXiv / IETF caches. All degrade to empty on a network blip. |
| **Score** | Every axis is two-stage: a free deterministic heuristic always runs; borderline scores get an LLM tie-break (~$0.001 on API, free on CLI). With no backend, the heuristic always stands — **every axis works fully keyless**. |
| **Dedup** | INSERT-time gates fired before commit: SHA-256 content hash, tagline token-overlap (Jaccard ≥ 0.7), name-token Jaccard on vertical-stripped names, super-component overlap, and a vertical-cap rejecting the Nth clone in a family. Cross-category dedup and a daily siphon keep the pool near its density cap. Filtered ideas go to an audit table with `filter_reason` and `similar_to_id` — they're signal, not silently dropped. |
| **Saturation-aware picking** | Category auto-pick is inverse-density weighted (`engine/saturation.py`), so a crowded category stops out-drawing the board's white space. |
| **Synthesize** | Cluster active ideas by category-pair theme; with `FORGE_SUPER_REASONING=1` the LLM names the unifying capability gap, otherwise slot-fill. |
| **Audit** | An approval-time coherence checker runs when a human approves an idea. A verdict meta-audit ("who watches the watcher") samples recent LLM verdicts and re-runs them with a flipped tone; divergences land in `verdict_audits`. |
| **Promote** | One click → `POST /api/promote/{id}` → `gh issue create` with the full MVP spec → status flips to `approved`, stamps `auto_promoted_at` so re-clicks return the existing issue. |
| **Issue sync** | Hourly. Pulls live GitHub state for promoted ideas. CLOSED+COMPLETED → `contributed`; CLOSED+NOT_PLANNED → `archived`; OPEN → left alone. Keeps the dashboard honest after an operator closes an issue. |
| **Scaffold** | `gh repo create`, pushes a language-appropriate template tree (Python / Rust / Go / Node), opens 3–5 starter issues from the MVP scope, applies labels. |

---

## Quick Start

```bash
git clone https://github.com/rayketcham-lab/project-forge.git
cd project-forge
pip install -e ".[dev,test]"

# Run tests (~1860 tests across 145 files)
pytest tests/ -q

# Start dashboard — the in-process scheduler boots with it
forge-serve     # http://localhost:55443

# Generate one idea (uses whatever backend resolves)
forge-generate

# Check which LLM backend is wired up right now
curl -s http://localhost:55443/api/backend-info | jq .
```

The dashboard is plain HTML + vanilla JS. No build step. Every cadence kicks in once `forge-serve` is running — there's no separate cron daemon to manage.

---

## Dashboard

Thirteen nav items:

```
Dashboard · Explore · Money Bots · Claude Lab · Sniper · Crypto
Cashflow · PKI · Missions · Labs · Projects · Think Tank · Mechanic
```

| Page | What's there |
|------|---------------|
| `/` | Stats grid, top ideas, super ideas, "Add Idea" tab (URL ingest, text ingest, 5-phase wizard), category + industry browse cards. |
| `/explore` | All ideas, two-axis filtering (industry vertical + tech category), status filter, full-text search, pagination. |
| `/money-bots` `/claude-lab` `/sniper` `/crypto` `/cashflow` `/pki` | The six boards. Per-category filter chips, in-scope totals, Churn Now. |
| `/missions` | Operator directives + per-mission idea grids. |
| `/labs` | Hub for the six autonomous avenues (below). |
| `/mechanic` | Self-improvement PR review panel. |
| `/thinktank` | Self-improvement pipeline: activity heartbeat, proposed patches, roadmap. |
| `/thinktank/audit` | Verdict-audit divergences. |
| `/ideas/{id}` | Detail view: score breakdown, related ideas, compare-to-repo, approve/reject/scaffold, challenge form, Launchpad + Recruiter buttons. |
| `/projects` | Scaffolded projects. |

### Card UX

Every idea card carries the same triage UX: a **hover tooltip** (220 ms delay, `data-*` first paint then lazy-fetch), an **in-window modal** on click (backdrop / × / ESC to close), a **per-card Reject ×**, and a **Promote ➤** button on un-promoted board cards — the only path that touches GitHub state.

---

## API

A subset of `web/routes.py` — hit `/docs` for the full OpenAPI page.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/health` | `{"status": "ok"}` |
| `GET` | `/api/stats` · `/api/categories` · `/api/ideas` | Aggregates + paginated list |
| `GET` | `/api/ideas/{id}` | JSON detail + challenges + related. Powers tooltip + modal |
| `GET` | `/api/money-bots/top` · `/api/claude-lab/top` · `/api/sniper/top` · `/api/crypto/top` · `/api/cashflow/top` · `/api/pki/top` | Top-N per board, each sorted by its own axis |
| `GET` | `/api/pki/probes` | Probe attempts + admission rate — why the PKI board is short |
| `POST` | `/api/churn` | On-demand generation. Body: `{"lab": "money"\|"claude"\|"snipe"\|"crypto"\|"cashflow"\|"pki", "category": "..."}`. `lab` switches both the allowed category set and the scoring axis |
| `POST` | `/api/promote/{id}` | Manual promote → GH issue |
| `GET` | `/api/backend-info` | Which LLM backend is live + censored key-env view |
| `GET`/`POST` | `/api/missions` · `/api/missions/{id}/generate` · `/api/missions/{id}/status` | Mission CRUD + directed generation |
| `GET`/`POST` | `/api/mechanic/status` · `/api/mechanic/prs` · `/api/mechanic/prs/{n}/approve` · `/api/mechanic/prs/{n}/reject` | Self-improvement review panel |
| `POST` | `/api/foundry/plan/{id}` · `/api/premortem/{id}` · `/api/launchpad/{id}` · `/api/recruiter/{id}` | Labs avenues, per idea |
| `POST` | `/api/ideas/from-url` · `/api/ideas/from-text` | Ingest paths (rate-limited) |
| `POST` | `/api/ideas/builder/step` · `/api/ideas/builder/save` | 5-phase wizard |
| `POST` | `/ideas/{id}/approve` · `/ideas/{id}/reject` · `/ideas/{id}/scaffold` | Lifecycle transitions |

Non-read methods require a Bearer token when `FORGE_API_TOKEN` is set. The dashboard uses an ephemeral per-process token rendered into a page meta tag — see `web/auth.py`.

---

## Configuration

### Core

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_DB_PATH` | `data/forge.db` | SQLite path |
| `FORGE_PORT` | `55443` | Web port |
| `ANTHROPIC_API_KEY` / `FORGE_ANTHROPIC_API_KEY` | (unset) | Primary API key, optional |
| `FORGE_HAIKU_API_KEY` | (unset) | Dedicated cheap-path key; falls back to the primary |
| `FORGE_LLM_BACKEND` | auto | `api` \| `claude_code` \| `static` \| `none` |
| `FORGE_LLM_MODEL` | `sonnet` | `sonnet` \| `opus` \| `haiku` |
| `FORGE_CLI_MODEL` | `opus` | Cheap-path model on the CLI backend |
| `FORGE_SUPER_REASONING` | unset | `1` to use the LLM for super-idea cluster naming |
| `FORGE_API_TOKEN` | (unset) | If set, non-read methods require Bearer auth |
| `FORGE_GITHUB_OWNER` | `rayketcham-lab` | Default org for scaffolded repos |

### Scheduler cadences

All in hours, all overridable. The in-process scheduler owns these — no systemd timers. See `web/lifespan_scheduler.py`.

| Variable | Default (h) | Cadence |
|----------|-------------|---------|
| `FORGE_EXPAND_INTERVAL_HOURS` | 1 | Cross-category + super idea generation |
| `FORGE_ISSUE_SYNC_INTERVAL_HOURS` | 1 | Sync promoted ideas with live GH issue state |
| `FORGE_PKI_INTERVAL_HOURS` | 1 | Grounded PKI probe — one gated target per fire |
| `FORGE_PULSE_INTERVAL_HOURS` | 3 | Event-driven generation from live HN/GitHub signal |
| `FORGE_MISSION_INTERVAL_HOURS` | 4 | Operator-directed generation, round-robin |
| `FORGE_SNIPE_INTERVAL_HOURS` | 6 | Grounded competitive-displacement snipes |
| `FORGE_SELF_IMPROVE_INTERVAL_HOURS` | 6 | GitHub `ci-queue` → PR loop |
| `FORGE_REVIEW_INTERVAL_HOURS` | 12 | Auto-archive sweeps over aged ideas |
| `FORGE_INTROSPECT_INTERVAL_HOURS` | 24 | Self-improvement idea proposals |
| `FORGE_VERDICT_AUDIT_INTERVAL_HOURS` | 24 | Verdict meta-audit |
| `FORGE_FEED_REFRESH_INTERVAL_HOURS` | 24 | NVD / arXiv / IETF cache refresh |
| `FORGE_SIPHON_INTERVAL_HOURS` | 24 | Density-cap trim over the pool |
| `FORGE_FUNDABILITY_INTERVAL_HOURS` | 24 | Fundability back-fill |
| `FORGE_CASHFLOW_INTERVAL_HOURS` | 24 | Cashflow back-fill |
| `FORGE_PKI_SCORE_INTERVAL_HOURS` | 24 | PKI urgency back-fill |
| `FORGE_MECHANIC_INTERVAL_HOURS` | 24 | Self-improvement PR cadence (disarmed by default) |
| `FORGE_SCOREBOARD_INTERVAL_HOURS` | 24 | Capture realized outcome signals |
| `FORGE_CARTOGRAPHER_INTERVAL_HOURS` | 168 | Corpus white-space / saturation memo |
| `FORGE_CHALLENGE_INTERVAL_HOURS` | 168 | Autonomous adversarial pass |
| `FORGE_SCHED_INITIAL_DELAY_SEC` | 60 | Boot grace period before the first tick |

### Kill switches

Both default **off**. Autonomous work that could touch code or GitHub state is opt-in.

| Variable | Gates |
|----------|-------|
| `FORGE_SELF_IMPROVE_ENABLED` | The self-improvement cadence |
| `FORGE_MECHANIC_ENABLED` | The Mechanic PR cadence |
| `FORGE_SCOREBOARD_AUTOTUNE` | Whether learned scorer nudges are applied |

---

## Architecture

```
src/project_forge/
  config.py                  Pydantic-settings
  models.py                  Idea, Mission, Challenge, IdeaCategory (42 values),
                             and the canonical board groupings: MONEY / CLAUDE_LAB /
                             SNIPER / CRYPTO / CASHFLOW / PKI_CATEGORIES
  engine/
    llm_generator.py         LLM-first generator: 5 modes, personas, anti-similarity,
                             8 artifact shapes, snipe path
    llm_backend.py           Resolver: AnthropicAPI | ClaudeCode | static
    categories.py            CATEGORY_SEEDS (42 categories; ~20+ seeds, 12+ domains each)
    prompts.py               Generation / URL-ingest / text-ingest templates
    diversity_prompts.py     Combinatoric / contrarian / persona templates
    scorer.py                novelty + specificity + scope realism → feasibility
    fundability.py           "Can we sell it"        → /money-bots, /crypto
    ambition.py              "Does it push the ceiling" → /claude-lab
    snipe.py                 "Can we wedge an incumbent" → /sniper
    cashflow.py              "How soon is the first dollar" → /cashflow
    pki.py                   "Does the industry care" + the /pki admission gate
    dedup.py                 INSERT-time gates; saturation.py  inverse-density picking
    siphon.py / audit.py / telemetry.py / quality_review.py / approval_check.py
    super_ideas.py / super_reasoning.py / si_consolidation.py
    mission.py               Operator-directed generation
    mechanic.py / mechanic_review.py / mechanic_status.py   Self-improvement engine
    scoreboard.py / foundry.py / cartographer.py / premortem.py
    launchpad.py / recruiter.py                              Labs avenues
    introspect.py / static_introspect.py / shadow.py
    url_ingest.py / text_ingest.py / idea_builder.py / compare.py
    verticals.py / router.py / repo_registry.py / bulk.py / thinktank_reconcile.py
  feeds/
    nvd.py / arxiv.py / ietf.py      Prompt-seed material
    market_intel.py                  Live incumbent intel (Sniper)
    pulse.py                         HN front page + GitHub trending (Pulse)
    pki_probe.py                     IETF WG feeds + implementation trackers (PKI)
    cache.py / health.py / _http.py
  rfc/                       RFC watcher + filters
  storage/
    db.py                    SQLite (WAL), schema, migrations, dedup queries,
                             asyncio write-lock, busy_timeout=60s
    seeds.py                 Seeded resources
  web/
    app.py                   FastAPI factory, lifespan, dashboard token, CSP middleware
    lifespan_scheduler.py    In-process multi-cadence supervisor (19 cadences)
    auth.py                  Bearer token middleware
    routes.py                All page + API routes
    templates/               22 Jinja2 templates
    static/                  14 JS/CSS assets (one per board + shared app.js)
  cron/
    runner.py                Single-shot entry (forge-generate)
    scheduler.py             Full-cycle orchestration
    auto_scan.py             No-LLM template generation
    horizontal.py            Super-idea generation
    *_runner.py              Per-cadence entry points
  scaffold/
    builder.py / github.py / templates/
```

---

## The in-process multi-cadence scheduler

There's no systemd. The runtime sandbox has no DBus, no sudo, no `/etc/systemd/` writes, so every cadence lives in the FastAPI lifespan as a single supervisor task owning N async loops, one per `Cadence`. A child failing once is logged and retried; a child crashing repeatedly does not stop its siblings; cancelling the supervisor cancels every child.

Nineteen cadences run by default. Defaults are tuned so a single host runs the full engine on roughly **$2–3/month** of LLM spend at API-path Haiku prices — and essentially $0 on a Claude subscription.

**Watermark discipline matters.** Cadences that generate are gated on their *own* watermark, not the global one — keying `pulse` off `MAX(generated_at)` left it effectively dead, because the hourly `expand` cadence kept that timestamp perpetually fresh. The PKI probe goes further: since it deliberately stores nothing most hours, it keys off its **attempt log** (`pki_probes.probed_at`), which advances whether or not an idea was admitted.

There is no `auto_promote` cadence. The weekly money-flipper was removed in v0.14b after a uvicorn-reload bug fired it three times in one session. `cron/auto_promote_runner.py` stays — it's invoked by the manual `/api/promote/{id}` endpoint.

---

## Data model

```python
class Idea(BaseModel):
    id: str                          # 12-char hex
    name: str
    tagline: str
    description: str
    category: IdeaCategory           # 42-value StrEnum
    market_analysis: str
    feasibility_score: float         # 0.0-1.0 — "can we build it?"
    mvp_scope: str
    tech_stack: list[str]
    generated_at: datetime
    status: IdeaStatus               # new | approved | scaffolded | rejected
                                     # | archived | contributed | implemented
    github_issue_url: str | None
    project_repo_url: str | None
    content_hash: str | None         # dedup
    source_url: str | None           # URL-ingest provenance
    generation_mode: str | None      # which generator mode/cadence produced it
    fundability_score: float | None  # "can we sell it?"      → /money-bots, /crypto
    ambition_score: float | None     # "frontier?"            → /claude-lab
    snipe_score: float | None        # "wedge an incumbent?"  → /sniper
    target_incumbent: str | None     # powers the "vs. X" badge
    artifact_type: str | None        # Claude Lab: artifact shape.
                                     # Sniper reuses it for the wedge angle.
    cashflow_score: float | None     # "first dollar?"        → /cashflow
    pki_urgency_score: float | None  # "industry cares?"      → /pki
    pki_anchor: str | None           # the RFC/draft/ballot/CVE/URL a PKI
                                     # finding is pinned to — required for admission
    mission_id: str | None           # the operator directive it was generated against
    auto_promoted_at: datetime | None # stamped on promote — idempotency guard
```

Filtered ideas (`FilteredIdea`) live in their own table with `filter_reason` and `similar_to_id`. Probe attempts live in `pki_probes`. Approval checks, verdict audits, challenges, missions, outcome signals, and calibration weights each have their own tables.

---

## Categories

42 as of v0.23, in `engine/categories.py` as a `dict[IdeaCategory, dict]`. The original 13 lean security / infrastructure (it's what the project was built for); each later wave opens fresh idea space once the prior seeds saturate.

```
# Original 13 — IT / security
security-tool · vulnerability-research · pqc-cryptography · nist-standards
rfc-security · crypto-infrastructure · privacy · compliance · observability
devops-tooling · automation · market-gap · self-improvement

# v0.12 — money-friendly            # v0.15 — Claude / agent frontier
automation-income · consumer-app     claude-skills-agents · ai-marketplace
productivity · creator-tools

# v0.16 — fundable product shapes   # v0.16 — rest of the agent ecosystem
micro-saas · vertical-saas           agent-infra · claude-evals
ecommerce-tools · fintech-tools      agent-security · context-memory

# v0.19 — on-chain                  # v0.20 — folding cash
onchain-security · web3-infra        productized-services · digital-products
defi-tooling · stablecoin-payments   commerce-ops · lead-generation
crypto-compliance                    flipping-arbitrage

# v0.23 — certificate infrastructure
pki-revocation · cert-lifecycle · pqc-migration · ca-operations · cert-identity
```

Each entry has `description`, `seed_concepts` (20+ strings), and `domains_to_cross` (12+ unrelated domains for cross-pollination); category-specific personas live in `engine/llm_generator.py`. Replace the dict to retarget the engine at any portfolio.

A parallel **vertical** axis is inferred at query time from idea text (government, healthcare, education, finance, retail, hospitality, manufacturing, energy, telco) via cached keyword matching.

---

## The 5 generation modes

`engine/llm_generator.py` rotates through these, preferring under-represented modes so the rotation self-balances.

| Mode | What it pitches |
|------|-----------------|
| `novel` | A fresh problem-solution pair the persona feels acutely. |
| `inversion` | A paid SaaS the persona is stuck paying for → the open-source / self-hosted version. |
| `bundle` | Three+ overlapping tools → the unified product, naming what's consolidated. |
| `microservice` | A big complex tool → one 100-line utility that does ONE thing better. |
| `adversarial` | An assumption everyone in the category takes for granted but is wrong. |

---

## The 8 artifact shapes (Claude Lab)

On Claude Lab generation the LLM additionally picks one of 8 shapes and gets a per-shape prompt section pinning down what to produce. The picker prefers under-represented shapes.

| Shape | What the LLM designs |
|-------|--------------------|
| `skill` | Reusable capability + assets loaded on demand. Trigger + win condition. |
| `sub-agent` | Delegated specialist. Invocation contract + returns-shape + scope boundary. |
| `mcp-server` | Tightly-scoped tool family. Tool list + auth + deployment story. |
| `hook` | Lifecycle hook injecting context or policy. Trigger + payload + backout path. |
| `slash-command` | Operator-invoked fixed sequence. Grammar + arg schema + output spec. |
| `workflow` | Multi-step orchestration. The DAG + recovery shape + success criterion. |
| `protocol` | Convention multiple agents follow. Framing + versioning + negotiation. |
| `ability` | Capability primitive. I/O contract + failure modes + inference cost. |

Combinatorics for one Claude Lab click: **6 categories × 5 modes × 8 artifacts × ~9 personas ≈ 2,000+ distinct starting frames**, before anti-similarity narrows further.

---

## The Sniper board

Most boards generate from a blank page. Sniper flips the risk: it starts from **demand already proven with real money**, then finds the opening — how the best challengers actually win (Cal.com vs Calendly, Plausible vs Google Analytics, PostHog vs Amplitude).

Every snipe is anchored on a **named, real incumbent** — no name, no idea. The pitch is forced into a fixed shape: *incumbent X proves demand → its structural weakness is Y → we wedge with Z from beachhead B → because now N.*

**Web-grounded, not from memory.** `feeds/market_intel.py` pulls live keyless signal — Hacker News discussion/complaints/"alternative" threads, and GitHub open-source challengers by stars — cached per incumbent (24h TTL), degrading to empty on a blip.

**Seven wedge angles** rotate so the board doesn't pitch fifty cheaper-clones: `price-snipe`, `unbundle`, `down-market`, `vertical`, `ai-native`, `open-source`, `compliance-shift`.

---

## Labs — autonomous avenues (v0.17)

The boards all do one verb — *generate an idea, score it*. Stack that against the full lifecycle (scan → generate → score → decide → build → launch → measure → learn) and the back half is empty. Six avenues close that gap under a **`/labs`** hub.

| Avenue | Phase | What it does | Surface |
|--------|-------|--------------|---------|
| **Scoreboard** | Learn | Captures realized outcome signals and reports predicted-vs-realized per axis. Recalibration stays human-gated. | `/scoreboard`, daily cadence |
| **Foundry** | Build | Generates a ready-to-create starter repo plan (tree, issues, README). Repo creation stays human-gated. | `/foundry` |
| **Pulse** | React | Pulls live HN + GitHub-trending signal and generates an idea anchored to the hottest. Event-driven. | `/pulse`, 3h cadence |
| **Cartographer** | Strategize | White-space + saturation atlas and a "State of the Forge" memo with the recommended next bet. | `/cartographer`, weekly |
| **Kill Board** | Critique | Pre-mortem: ranks ideas most-likely-to-die first and argues *against* each. | `/killboard` |
| **Launchpad / Recruiter** | Per-idea | GTM brief (positioning, first-10-customers, channels); staffed-build estimate (roles, person-weeks, cost band). | Buttons on `/ideas/{id}` |

Every avenue degrades gracefully without an LLM (heuristic fallback) and without network (fetches degrade to empty), so the pages always render.

---

## Testing

```bash
pytest tests/ -q                                   # ~1860 tests, 145 files
pytest tests/ -k "pki" -v                          # subset — v0.23 PKI board
pytest tests/ -k "sniper or snipe or market_intel" # subset — Sniper board
pytest tests/ -k "mechanic" -v                     # subset — self-improvement
pytest tests/ --cov=project_forge --cov-report=term-missing
ruff check src/ tests/
ruff format --check src/ tests/
```

CI runs the same on a self-hosted runner. Schema drift is locked by `tests/test_db_integrity.py` — adding a table or an `ideas` column fails CI until the snapshot is updated, which is what catches a `CREATE TABLE IF NOT EXISTS` change that silently never migrates an existing database.

---

## Deployment notes

The project runs on a single host as one long-lived FastAPI service (`forge-serve`). The in-process scheduler boots with the app — no systemd timers, no cron daemon. File writes deploy instantly via `uvicorn --reload`. The unit files under `scripts/` are kept for reference but are not the active path.

Assumes a normal Python 3.12+ environment with `gh` CLI and (optionally) `claude` CLI on PATH. No Docker image is checked in.

---

## Roadmap

The north star: **from "generates and scores ideas" → "builds, ships, and learns from real outcomes"** — without losing the variety the LLM-first pivot won back. Full detail lives in **[ROADMAP.md](ROADMAP.md)**.

| Horizon | Focus |
|---------|-------|
| **Now** | Real outcome data into the Scoreboard (revenue / inline 👍👎, not just OSS-challenger stars) · Foundry generates a *working* MVP + smoke tests, not just a skeleton · cost-per-cadence attribution |
| **Next** | Launchpad → a deployed landing page for demand validation · calibrate the scorers once outcome data exists · weekly engine-written retro |
| **Later** | Model router + per-cadence budget guard · idea-quality regression suite (canary vs prompt drift) · opt-in per-idea share links |

It's a personal project, so this is intent and direction — not a delivery commitment.

---

## Status

Active. The scheduler fires generation hourly, the PKI probe hourly, issue-sync hourly, Pulse every 3h, missions every 4h, grounded snipes every 6h, scoring + audits + feed refresh + siphon daily, and the challenge + cartographer cadences weekly. Promotion to GitHub is human-gated. Self-improvement and Mechanic are disarmed unless explicitly enabled.

Issues / PRs welcome but not necessarily merged on any timeline — see `CONTRIBUTING.md` and `SECURITY.md`.

---

## License

MIT. See `LICENSE`.
