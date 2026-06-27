# Project Forge

![Version](https://img.shields.io/badge/version-0.17-blue) ![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/rayketcham-lab/project-forge/actions/workflows/ci.yml/badge.svg) ![Tests](https://img.shields.io/badge/tests-1530+-passing?color=brightgreen) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**[Quickstart](#quick-start) · [Dashboard](#dashboard) · [Labs](#labs--autonomous-avenues-v017) · [Roadmap](#roadmap) · [Architecture](#architecture) · [Config](#configuration)**

An autonomous project idea generator. It runs an in-process scheduler inside the FastAPI app, calls an LLM (or falls back to deterministic heuristics), scores ideas on **four orthogonal axes** — **feasibility** (can we build it), **fundability** (can we sell it), **ambition** (does it push the frontier), and **snipe** (can we wedge into a market-proven incumbent) — deduplicates aggressively, and stores everything in SQLite. A web dashboard lets a human review, approve, and — with a single click — promote ideas into GitHub issues with full MVP specs. **Promotion is human-gated**: the engine ranks and surfaces, you approve.

Three themed surfaces frame the corpus, each with its own question and its own scoring axis:

- **/money-bots** — top ideas in money-friendly categories (automation-income, creator-tools, consumer-app, productivity, micro-saas, vertical-saas, ecommerce-tools, fintech-tools), sorted by `fundability_score DESC`. *Can we sell it?*
- **/claude-lab** (v0.15) — top ideas in the Claude / agent ecosystem categories (claude-skills-agents, ai-marketplace, agent-infra, claude-evals, agent-security, context-memory), sorted by `ambition_score DESC`. *Does it push the frontier?* — new skills, sub-agents, MCP servers, hooks, slash commands, marketplaces, attribution and provenance systems.
- **/sniper** (v0.16) — competitive-displacement plays sorted by `snipe_score DESC`. Each idea wedges into a **named, market-proven incumbent** (the `vs. X` badge), grounded in **live** Hacker News + GitHub signal so the comp is real demand, not a hunch. *Can we take a slice of a proven market?*

Each page has its own **Churn Now** button that fires the generator on demand against the right category family and the right scoring axis. On /claude-lab, Churn rotates through 8 artifact shapes (skill, sub-agent, MCP server, hook, slash command, workflow, protocol, ability); on /sniper it rotates through 7 wedge angles (price-snipe, unbundle, down-market, vertical, ai-native, open-source, compliance-shift) — so each click produces a meaningfully different starting frame.

> Operating philosophy: **autonomous, human-driven**. The engine generates, scores, dedups, sweeps, and audits itself on a schedule. Anything that touches external state (GitHub issues, repos) is one click away — never autonomous. The v0.14 weekly auto-promote cadence was removed in v0.14b after a uvicorn-reload bug fired it three times. Today, the Money Bots and Claude Lab pages each expose a Promote ➤ button per card; nothing else can flip an idea to `approved` without an operator.

This is a personal project that's been running for several months. It's open-sourced because some of the patterns (LLM backend abstraction, multi-stage dedup, persona-driven generation, multi-axis scoring, web-grounded competitive comps, in-process multi-cadence scheduling, artifact-shape rotation) might be useful to others. It's not a product — no support, no SLA, no promises about your timeline. It *is* actively developed against a real [roadmap](#roadmap) ([ROADMAP.md](ROADMAP.md)) — read that as the direction of travel, not a contract.

> [!NOTE]
> **No API key required.** The LLM backend resolver picks the best path automatically:
> 1. **Anthropic API** when `ANTHROPIC_API_KEY` is set
> 2. **Claude Code CLI** when `claude` is on `$PATH` (uses your Claude subscription)
> 3. **Static heuristics** when neither
>
> Override via `FORGE_LLM_BACKEND={api|claude_code|static}` and `FORGE_LLM_MODEL={sonnet|opus|haiku}` (default: sonnet).
>
> **v0.15a — CLI defaults to Opus.** When the resolved backend is the Claude Code CLI, the cheap-path resolver (`resolve_cheap_backend()`) now returns **Opus** rather than Haiku. There's no per-call cost on a Claude subscription, so the strongest model wins for the LLM-first generator, fundability/ambition tie-breaks, and semantic-dedup verification. API-path users still get Haiku 4.5 there for cost discipline. Override via `FORGE_CLI_MODEL={sonnet|opus|haiku}` for the speed/quality tradeoff. Set `FORGE_HAIKU_API_KEY` if you want to use a dedicated cheap key for the API path without exposing your Sonnet/Opus key.
>
> `GET /api/backend-info` (v0.15a) returns which backend is in use plus a censored view of the API-key env vars visible to the running process — useful when /money-bots looks suspiciously like the static generator wrote it.

---

## What it actually does

| Step | Implementation |
|------|---------------|
| **Generate** | Two paths. The **LLM-first generator** (`engine/llm_generator.py`, v0.13+) asks the configured cheap-path model for a whole idea using one of 5 generation modes (novel / inversion / bundle / microservice / adversarial), a category-specific persona, and anti-similarity injection (the 30 most-recent active names — "do NOT produce anything like these"). For Claude Lab categories, the call additionally picks one of **8 artifact shapes** (v0.15a — see below) and injects a per-shape prompt section (~150 words) that pins down exactly what the LLM should produce. The **template generator** (`auto_scan.generate_local_idea`) is the deterministic fallback when no backend is reachable. Optional saturation summary, portfolio context, and external feed items (NVD CVEs / arXiv / IETF drafts) are mixed into every prompt. |
| **Score (feasibility)** | A composite score (0.0–1.0) of three components, weighted: **novelty** (0.4), **specificity** (0.35), **scope realism** (0.25). See `engine/scorer.py`. Answers *can we build it?* |
| **Score (fundability)** | v0.13. Two-stage: a heuristic looks at `tech_stack` payment hints (stripe, paddle, lemonsqueezy), paid-product keywords in description / mvp_scope, buyer signal in market_analysis, and a per-category bonus. Borderline scores in `[0.35, 0.70]` get a Haiku/Opus tie-break call (~$0.001 on API, free on CLI) for a finer signal. See `engine/fundability.py`. Answers *can we sell it?* |
| **Score (ambition)** | v0.15. Same shape as fundability but rewards frontier-ness instead of monetizability. Heuristic looks at category bonus (`CLAUDE_SKILLS_AGENTS`, `AI_MARKETPLACE`, `AGENT_INFRA`, `AGENT_SECURITY`, `CONTEXT_MEMORY`, `CLAUDE_EVALS`), frontier-keyword density (mcp, sub-agent, attribution, registry, provenance, reproducibility, …), Anthropic / MCP stack signal, and description depth. Borderline `[0.40, 0.75]` gets a Haiku/Opus tie-break. See `engine/ambition.py`. Answers *does it push the frontier?* |
| **Score (snipe)** | v0.16. Same two-stage shape, scoring competitive-displacement potential. The gate: a **named `target_incumbent`** or it isn't a snipe. The heuristic then rewards grounded demand signal ($/ARR/funding, GitHub stars, HN points), a structural wedge (overpriced / bloated / enterprise-only / closed / legacy / unbundle-able), a why-now catalyst (AI-native rebuild, price hike, new mandate), and a focused beachhead. Borderline gets a Haiku/Opus tie-break. See `engine/snipe.py`. Answers *can we take a slice of a proven market?* |
| **Ground (market intel)** | v0.16. Before each snipe, `feeds/market_intel.py` pulls **live, keyless** signal on the chosen incumbent — Hacker News (Algolia) discussion + complaints, and GitHub open-source-challenger stars (proven appetite for an alternative). Cached per incumbent (24h TTL), degrades to empty on a network blip. The signal is injected into the generation prompt so every comp traces to a real, dated source — not the model's memory. |
| **Dedup (INSERT-time gates, v0.11)** | Four layers, all fired before the idea is committed: SHA-256 content hash, tagline token-overlap (Jaccard ≥ 0.7), **name-token Jaccard** on vertical-stripped names, **super-component overlap** for super-ideas, and a **vertical-cap** that rejects an Nth clone in the same vertical family. Filtered ideas are written to a separate audit table with `filter_reason` and `similar_to_id` — they're a signal, not silently dropped. A semantic-dedup tie-breaker (v0.12) fires for borderline near-rejections. |
| **Synthesize** | Cluster active ideas by category-pair theme. With an LLM (`FORGE_SUPER_REASONING=1`), ask it to name the unifying capability gap. Without one, slot-fill `{Keyword1} & {Keyword2} {Suffix}`. The daily rotation got a new slot 8 — **Claude Frontier** — that biases super-idea clustering toward `CLAUDE_SKILLS_AGENTS` + `AI_MARKETPLACE` pairs. |
| **Compare** | Token-overlap (Jaccard) between an idea and a GitHub repo's README + topics + description. Returns a verdict (new / enhance / duplicate). |
| **Approval check (v0.11)** | When a human flips an idea to `approved`, a non-blocking think-tank coherence checker runs: empty tech stack? mvp scope drifting from description? super-idea components with no shared theme? fake-perfect feasibility score? Results land in `approval_checks` and surface as a banner on the idea detail page. |
| **Verdict audit (v0.11)** | A "who watches the watcher" cadence samples recent LLM verdicts (challenge / review) and re-runs them with a flipped tone. Divergences land in `verdict_audits` for inspection. |
| **Manual promote (v0.14b)** | One click on /money-bots or /claude-lab → POST `/api/promote/{id}` → `gh issue create` with the full MVP spec + market analysis + tech stack → idea flips to `approved` and stamps `auto_promoted_at` so re-clicks return the existing issue. The autonomous weekly cadence was removed after a uvicorn-reload bug fired it three times — promotion is human-gated now. |
| **Issue sync (v0.14c)** | Hourly cadence. Pulls live GitHub state for every approved + promoted idea via `gh issue view --json state,stateReason`. CLOSED + COMPLETED → `contributed`. CLOSED + NOT_PLANNED → `archived`. OPEN → leave alone. Keeps the dashboard honest after an operator closes an issue. |
| **Scaffold** | Calls `gh repo create`, pushes a language-appropriate template tree (Python / Rust / Go / Node), opens 3–5 starter issues from the idea's MVP scope, applies labels. |

---

## Quick Start

```bash
git clone https://github.com/rayketcham-lab/project-forge.git
cd project-forge
pip install -e ".[dev,test]"

# Run tests (~1340+ tests)
pytest tests/ -v

# Start dashboard — the in-process scheduler boots with it
forge-serve     # http://localhost:55443

# Generate one idea (uses whatever backend resolves)
forge-generate

# Check which LLM backend is wired up right now
curl -s http://localhost:55443/api/backend-info | jq .

# Trim an over-saturated database one-shot (e.g. archive the long tail
# below a feasibility cutoff, then run uniqueness gates retroactively)
python scripts/trim-now.py
```

The dashboard is plain HTML + a small amount of vanilla JS. No build step. Every cadence kicks in once `forge-serve` is running — there's no separate cron daemon to manage.

---

## Dashboard

| Page | What's there |
|------|---------------|
| `/` (Home) | Stats grid, top ideas, super ideas tab, "Add Idea" tab (URL ingest, one-shot text ingest, 5-phase wizard), category and industry browse cards, **Money Bots card** and **Claude Lab card** linking through to their respective surfaces. |
| `/explore` | All ideas with two-axis filtering: industry vertical (inferred from text) + tech category. Status filter, challenged-only filter, full-text search, pagination. |
| `/money-bots` | v0.14. Top monetizable ideas sorted by `fundability_score DESC` across the eight money-friendly categories (`automation-income`, `creator-tools`, `consumer-app`, `productivity`, `micro-saas`, `vertical-saas`, `ecommerce-tools`, `fintech-tools`). Per-category filter chips, total in-scope count, and a **Churn Now** button that fires the LLM-first generator on demand (~$0.003/click on API, free on CLI). |
| `/claude-lab` | v0.15. Top frontier ideas sorted by `ambition_score DESC` across the six Claude-ecosystem categories (`claude-skills-agents`, `ai-marketplace`, `agent-infra`, `claude-evals`, `agent-security`, `context-memory`). Same shape as /money-bots but bound to a different category family and a different scoring axis. **Churn** POSTs `/api/churn` with `lab=claude` so the right family + axis apply, and a per-card **artifact-type badge** (colored per shape — skill / sub-agent / mcp-server / hook / slash-command / workflow / protocol / ability). |
| `/sniper` | v0.16. Competitive-displacement plays sorted by `snipe_score DESC`. Each card carries a **`vs. {incumbent}` badge** and a wedge-angle pill. **Snipe Now** POSTs `/api/churn` with `lab=snipe`: the engine picks a real incumbent, pulls live HN + GitHub demand signal, and generates a grounded wedge. Hunts across both the commercial categories and the fat-incumbent IT/security space. |
| `/ideas/{id}` | Detail view with description, score breakdown (feasibility + fundability + ambition where present), related ideas, compare-to-repo, approve/reject/scaffold actions, challenge form, approval-check banner when something looks incoherent. |
| `/projects` | List of scaffolded projects. |
| `/thinktank` | Self-improvement pipeline: engine activity heartbeat, AI-proposed code patches (Decompose X / Add tests for X), GitHub roadmap. |
| `/thinktank/audit` | Verdict-audit results — divergences between original LLM verdicts and the meta-audit's flipped-tone re-runs. |

The "Add Idea" tab has three independent paths:

- **From URL** — paste a link, the tool fetches the page (with SSRF guard), sends content + metadata to the LLM, gets an idea back.
- **Text — Quick** — paste a fragment, one LLM call, get an idea.
- **5-Phase Wizard** — Discover → Differentiate → Audience → Constraints → Synthesize. Each phase asks 2–3 follow-up questions based on prior answers. Final phase produces a draft you can edit before saving.

### Navigation (v0.15)

The nav bar was rebuilt in v0.15 (redundant tabs dropped, emoji prefixes removed). v0.16 added Sniper. The seven items:

```
Dashboard · Explore · Money Bots · Claude Lab · Sniper · Projects · Think Tank
```

Money Bots, Claude Lab, and Sniper each carry a subtle 6px colored dot and a colored bottom-underline-on-active — small visual hooks that say "this is a themed surface" without the loud gradient backgrounds of the v0.14 nav.

### Card UX (v0.14c)

Every idea card across the dashboard, money-bots, claude-lab, super-ideas, and explore pages carries the same triage UX:

- **Hover tooltip** — 220 ms after mouse-over, a floating panel shows name + tagline + feasibility / fundability / ambition / mode pills + the first 280 chars of the description. Reads from `data-*` attributes for an instant first paint, then lazy-fetches `/api/ideas/{id}` to fill the description. Move off the card and it disappears.
- **In-window modal** — click anywhere on a card (not on a link/button) and the full idea opens in place: description, market, MVP scope, tech-stack chips, the GitHub issue if promoted, a Reject button, and a "Full page ↗" escape hatch. Backdrop / × / ESC all close. No more page navigations for just-peek-at-an-idea.
- **Per-card Reject button** — a small red × top-right of every card, invisible until you hover. Confirms via dialog, posts to `/ideas/{id}/reject`, removes the card from the DOM.
- **Churn Now button** (on /money-bots and /claude-lab) — fires `/api/churn` for the current category filter and lab. New idea appears in the grid after a 1.5 s reload.
- **Artifact-type badge** (on /claude-lab) — small colored pill on each card showing which of the 8 artifact shapes the LLM was asked to produce. Lets a triager see at a glance "this is a slash command" vs "this is an MCP server" without opening the card.
- **Promote ➤ button** (on /money-bots and /claude-lab, only for un-promoted ideas) — confirms, posts to `/api/promote/{id}`, files the GitHub issue, flips status to `approved`. The only path that touches GitHub state.

---

## API

A subset of the routes exposed by `web/routes.py`. There are more — see the `@router.get/post` decorators in that file for the full list, or hit `/docs` for the auto-generated OpenAPI page.

| Method | Path | Notes |
|--------|------|-------|
| `GET`  | `/health` | `{"status": "ok"}` |
| `GET`  | `/api/stats` | Aggregate counts |
| `GET`  | `/api/categories` | Category counts + average score |
| `GET`  | `/api/ideas` | Paginated list |
| `GET`  | `/api/ideas/{id}` | JSON detail + recent challenges + 4 related ideas. Powers the hover tooltip and in-window modal (v0.14c) |
| `GET`  | `/api/money-bots/top` | Top monetizable ideas sorted by `fundability_score` (v0.14) |
| `GET`  | `/api/claude-lab/top` | Top frontier ideas sorted by `ambition_score` (v0.15) |
| `GET`  | `/api/sniper/top` | Top competitive-displacement ideas sorted by `snipe_score`, with `target_incumbent` + wedge `angle` (v0.16) |
| `POST` | `/api/churn` | On-demand idea generation. Body: `{ "lab": "money" \| "claude" \| "snipe", "category": "..." }`. The `lab` field switches both the allowed category set and the scoring axis (fundability / ambition / snipe). Claude Lab calls carry the chosen `artifact_type`; snipe calls carry `target_incumbent` + the wedge angle. (v0.14 + v0.15 + v0.16) |
| `POST` | `/api/promote/{id}` | Manual promote → GH issue (v0.14b — replaces the removed auto-promote cadence) |
| `GET`  | `/api/backend-info` | v0.15a diagnostic: which LLM backend is in use + censored view of the API-key env vars seen by the running process |
| `POST` | `/api/ideas/{id}/compare` | Compare to a repo |
| `POST` | `/api/ideas/from-url` | Ingest from URL (rate-limited) |
| `POST` | `/api/ideas/from-text` | Ingest from text fragment (rate-limited) |
| `POST` | `/api/ideas/builder/step` | One step of the wizard |
| `POST` | `/api/ideas/builder/save` | Save the wizard's final draft |
| `POST` | `/ideas/{id}/approve` | Move to approved (triggers the approval-check) |
| `POST` | `/ideas/{id}/reject` | Move to rejected |
| `POST` | `/ideas/{id}/scaffold` | Scaffold to GitHub (rate-limited) |
| `GET`  | `/api/ideas/{id}/approval-check` | Read back the coherence-check result |

Non-read methods require a Bearer token when `FORGE_API_TOKEN` is set. The dashboard uses an ephemeral per-process token rendered into the page meta tag — see `web/auth.py`.

---

## Configuration

### Core

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_DB_PATH` | `data/forge.db` | SQLite path |
| `FORGE_PORT` | `55443` | Web port |
| `ANTHROPIC_API_KEY` / `FORGE_ANTHROPIC_API_KEY` | (unset) | Primary API key, optional |
| `FORGE_HAIKU_API_KEY` | (unset) | Dedicated key for cheap-path API calls (LLM-first generator, fundability/ambition tie-break, semantic dedup). Falls back to the primary key if unset. |
| `FORGE_LLM_BACKEND` | auto | `api` \| `claude_code` \| `static` \| `none` |
| `FORGE_LLM_MODEL` | `sonnet` | `sonnet` \| `opus` \| `haiku` |
| `FORGE_CLI_MODEL` | `opus` | v0.15a. Cheap-path model when the resolved backend is the Claude Code CLI. Defaults to **Opus** (strongest model wins — no per-call cost on subscription). Override with `sonnet` for speed or `haiku` to match the API-path behavior. |
| `FORGE_SUPER_REASONING` | unset | Set to `1` to use the LLM for super-idea cluster naming |
| `FORGE_API_TOKEN` | (unset) | If set, all non-read API methods require Bearer auth |
| `FORGE_GITHUB_OWNER` | `rayketcham-lab` | Default GitHub org for scaffolded repos |

### Scheduler cadences (v0.11)

All in hours. The in-process scheduler owns these — no systemd timers required. See `web/lifespan_scheduler.py`.

| Variable | Default (h) | Cadence |
|----------|-------------|---------|
| `FORGE_EXPAND_INTERVAL_HOURS` | 1 | Cross-category + super idea generation |
| `FORGE_REVIEW_INTERVAL_HOURS` | 12 | Auto-archive sweeps over aged ideas |
| `FORGE_SELF_IMPROVE_INTERVAL_HOURS` | 6 | GitHub `ci-queue` → PR loop |
| `FORGE_INTROSPECT_INTERVAL_HOURS` | 24 | Self-improvement idea proposals |
| `FORGE_VERDICT_AUDIT_INTERVAL_HOURS` | 24 | Verdict meta-audit ("who watches the watcher") |
| `FORGE_FEED_REFRESH_INTERVAL_HOURS` | 24 | NVD / arXiv / IETF cache refresh |
| `FORGE_FUNDABILITY_INTERVAL_HOURS` | 24 | Score recent ideas for monetization viability |
| `FORGE_AMBITION_INTERVAL_HOURS` | 24 | v0.15. Score Claude Lab category ideas for frontier-bias |
| `FORGE_SNIPE_INTERVAL_HOURS` | 6 | v0.16. Generate grounded competitive-displacement snipes across the Sniper hunting grounds (watermark-gated) |
| `FORGE_ISSUE_SYNC_INTERVAL_HOURS` | 1 | v0.14c. Sync `auto_promoted_at` ideas with their live GH issue state |
| `FORGE_CHALLENGE_INTERVAL_HOURS` | 168 | Autonomous adversarial pass on top unchallenged ideas |
| `FORGE_SCHED_INITIAL_DELAY_SEC` | 60 | Boot grace period before the first tick |
| `FORGE_FEEDS_DIR` | `<db_dir>/feeds` | Where the NVD / arXiv / IETF caches live |

### Promote button (v0.14b — manual)

These knobs gate the **manual** Promote ➤ button on /money-bots and /claude-lab and the underlying `/api/promote/{id}` endpoint. The cadence that used to read them autonomously was removed in v0.14b.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_PROMOTE_CATEGORIES` | the 8 `MONEY_CATEGORIES` | Comma-separated category whitelist for the promotion candidate-picker. v0.16: defaults to the canonical `MONEY_CATEGORIES` grouping. (The manual Promote ➤ button promotes any idea by id regardless of this.) |
| `FORGE_PROMOTE_MIN_SCORE` | `0.55` | Minimum score (fundability for money categories, ambition for Claude Lab categories) for the candidate-picker to consider an idea promotable |
| `FORGE_PROMOTE_REPO` | `<owner>/<repo>` | Where the promotion issue gets filed. Set this to a dedicated "money-bot board" or "claude-lab board" repo if you want them off your main forge backlog. |

---

## Architecture

```
src/project_forge/
  config.py                  Pydantic-settings
  models.py                  Idea, FilteredIdea, GenerationRun, IdeaCategory, etc.
                             v0.13+ columns: generation_mode, fundability_score
                             v0.14 column:   auto_promoted_at
                             v0.15 columns:  ambition_score, artifact_type
                             v0.16 columns:  snipe_score, target_incumbent
                             v0.16 groupings: MONEY_CATEGORIES / CLAUDE_LAB_CATEGORIES /
                             SNIPER_CATEGORIES (canonical board membership)
  engine/
    generator.py             API path (IdeaGenerator + LLMBackendIdeaGenerator)
    llm_generator.py         v0.13. LLM-first generator with 5 modes + persona rotation + anti-similarity.
                             v0.15a: ARTIFACT_TYPES + _ARTIFACT_PROMPTS + pick_least_used_artifact()
    llm_backend.py           Backend resolver: AnthropicAPI | ClaudeCode | static.
                             v0.15a: resolve_cheap_backend() returns Opus on CLI, Haiku on API.
    prompts.py               Generation, URL-ingest, text-ingest prompts
    diversity_prompts.py     Combinatoric / contrarian / persona templates
    categories.py            CATEGORY_SEEDS dict (19 categories). Claude Lab categories carry
                             22 seeds + 12 personas each.
    scorer.py                novelty + specificity + scope_realism → composite (feasibility)
    fundability.py           v0.13. Heuristic + Haiku/Opus tie-break in [0.35, 0.70]
    ambition.py              v0.15. Frontier-bias score for the Claude Lab corpus.
                             Heuristic (category + frontier keywords + Anthropic stack +
                             description depth) + tie-break in [0.40, 0.75].
    snipe.py                 v0.16. Competitive-displacement score for the Sniper board.
                             Named-incumbent gate + demand/wedge/why-now signals; 7 wedge
                             angles; generate_snipe_llm lives in llm_generator.py.
    dedup.py                 INSERT-time gates: content-hash + tagline + name-Jaccard + vertical-cap
    super_ideas.py           Clustering + slot-fill or LLM-reasoned naming
    super_reasoning.py       Cluster signature + LLM cluster naming.
                             v0.15: DAILY_ROTATION slot 8 = "Claude Frontier".
    static_introspect.py     No-LLM SI proposals (Decompose / Add tests for)
    introspect.py            LLM-driven self-improvement prompt builder
    idea_builder.py          5-phase wizard prompts + step orchestration
    text_ingest.py           Free-form text → Idea
    url_ingest.py            URL → Idea (with SSRF guard)
    verticals.py             Industry inference (keyword-based, cached)
    telemetry.py             filter_rate, saturation, novelty_trend, coverage_gaps
    shadow.py                Patch validation (parse target metric, compare snapshots)
    compare.py               Idea-to-repo overlap
    quality_review.py        Reject low-quality / off-topic ideas
    approval_check.py        v0.11. Approval-time think-tank coherence checker
    siphon.py / audit.py / bulk.py / repo_registry.py / router.py
  feeds/
    nvd.py / arxiv.py / ietf.py    Parser + fetcher per source (prompt-seed material)
    market_intel.py          v0.16. Live incumbent intel for the Sniper board —
                             HN (Algolia) + GitHub challenger stars, keyless, cached
    cache.py / health.py / _http.py
  storage/
    db.py                    SQLite (WAL), schema, dedup queries.
                             v0.11 tables: approval_checks, verdict_audits.
                             v0.15 ideas columns: ambition_score REAL, artifact_type TEXT.
                             v0.16 ideas columns: snipe_score REAL, target_incumbent TEXT.
                             v0.15 hardening: asyncio write-lock, busy_timeout=60s.
  web/
    app.py                   FastAPI factory, lifespan, dashboard token, CSP middleware
    lifespan_scheduler.py    In-process multi-cadence scheduler (replaces systemd timers).
                             v0.16: _fire_snipe cadence + seconds_until_next_snipe watermark.
    auth.py                  Bearer token middleware
    routes.py                All page + API routes (incl. /money-bots, /claude-lab, /sniper, /api/churn, /api/backend-info)
    templates/               Jinja2 (dashboard, explore, idea_detail, money_bots, claude_lab, sniper, thinktank, thinktank_audit, projects)
    static/                  app.js, money_bots.js, claude_lab.js, sniper.js, style.css
  cron/
    runner.py                Single-shot entry (used by forge-generate)
    scheduler.py             Full-cycle orchestration (generate → score → dedup → save → route)
    auto_scan.py             No-LLM generation
    horizontal.py            Super-idea generation
    expand_runner.py / introspect_runner.py / self_improve_runner.py
    review_runner.py / challenge_runner.py / verdict_audit_runner.py
    auto_promote_runner.py   v0.14. Promote logic, invoked by POST /api/promote/{id} only
                             (the autonomous weekly cadence was removed in v0.14b).
    issue_sync_runner.py     v0.14c. Pulls live GH issue state for promoted ideas → DB.
  scaffold/
    builder.py               Project structure
    github.py                gh CLI wrapper
    templates/               Per-language scaffold templates
```

---

## The in-process multi-cadence scheduler

There's no systemd. The original deployment shipped one timer per cron-driven cycle in `/etc/systemd/system/`, but the runtime sandbox doesn't have access to systemd at all (no DBus, no sudo, no `/etc/systemd/` writes). v0.11 moved every cadence into the FastAPI lifespan as a single supervisor task that owns N async loops, one per `Cadence`. A child failing once is logged and retried; a child crashing repeatedly does not stop its siblings; cancelling the supervisor cancels every child.

The defaults are tuned so a single host can run the full engine on roughly **$2–3/month** of LLM spend at API-path Haiku 4.5 prices — and essentially $0 on a Claude subscription (the CLI path):

```
expand            1h    cross-category + super idea generation (LLM-first)
issue_sync        1h    pull live GH state for promoted ideas → DB
review            12h   auto-archive sweeps over aged ideas
self_improve      6h    GitHub ci-queue → PR loop
introspect        24h   self-improvement idea proposals
verdict_audit     24h   "who watches the watcher" — samples recent LLM verdicts
feed_refresh      24h   NVD / arXiv / IETF cache refresh
fundability       24h   score recent ideas for monetization viability
ambition          24h   score Claude Lab category ideas for frontier-bias (v0.15)
snipe             6h    grounded competitive-displacement generation (v0.16)
pulse             3h    event-driven generation seeded by live HN/GitHub signal (v0.17)
scoreboard        24h   capture realized outcome signals for the engine's bets (v0.17)
cartographer      168h  corpus white-space/saturation strategy memo (v0.17)
challenge         168h  autonomous adversarial pass on top unchallenged ideas
```

There is no `auto_promote` cadence anymore. The weekly money-flipper was removed in v0.14b after a uvicorn-reload bug fired it three times in one session. The runner code at `cron/auto_promote_runner.py` stays — it's invoked by the manual `/api/promote/{id}` endpoint that the Promote ➤ buttons on /money-bots and /claude-lab POST to.

Each cadence is overridable via env (see the table above). The scheduler honours per-cadence watermarks (e.g. `expand` consults `MAX(generated_at)` so a manual `forge-generate` resets the clock), so frequent restarts don't double-fire.

---

## Self-improvement loop

There's a pipeline that proposes patches to the codebase itself:

- **Static introspector** (no LLM): walks `src/`, finds files >300 lines and modules without tests. Emits `Decompose X` and `Add tests for X` proposals with concrete suggestions (longest functions, public symbols).
- **LLM introspector** (with API key or Claude Code CLI): builds a prompt with file tree, recent commits, lint status, telemetry signals (saturation, filter rate, coverage gaps), asks for one targeted patch with a named target metric.
- **Shadow validation**: parses target metric from the proposal, snapshots telemetry before/after, only allows merge if the metric moved correctly.

Promoted proposals appear in the Think Tank dashboard. Whether they auto-merge depends on the `self_improve_runner` config — by default it opens a PR and waits for review.

---

## Data model

```python
class Idea(BaseModel):
    id: str                          # 12-char hex, generated
    name: str
    tagline: str
    description: str
    category: IdeaCategory           # 27-value StrEnum (v0.16)
    market_analysis: str
    feasibility_score: float         # 0.0-1.0 composite from scorer — "can we build it?"
    mvp_scope: str
    tech_stack: list[str]
    generated_at: datetime
    status: IdeaStatus               # new | approved | scaffolded | rejected
                                     # | archived | contributed | implemented
    github_issue_url: str | None
    project_repo_url: str | None
    content_hash: str | None         # for dedup
    source_url: str | None           # for URL-ingest provenance
    generation_mode: str | None      # v0.13. Which of the 5 LLM-first modes
                                     # produced this idea (or None for template)
    fundability_score: float | None  # v0.13. 0.0-1.0 monetization viability —
                                     # "can we sell it?" Sorts /money-bots DESC.
    ambition_score: float | None     # v0.15. 0.0-1.0 frontier-bias —
                                     # "does it push the frontier?" Sorts /claude-lab DESC.
    artifact_type: str | None        # v0.15a. One of skill / sub-agent / mcp-server /
                                     # hook / slash-command / workflow / protocol / ability
                                     # for Claude Lab ideas. v0.16: snipe ideas reuse this
                                     # column to store the wedge angle. None elsewhere.
    snipe_score: float | None        # v0.16. 0.0-1.0 competitive-displacement —
                                     # "can we wedge a proven incumbent?" Sorts /sniper DESC.
    target_incumbent: str | None     # v0.16. The named real incumbent a snipe targets —
                                     # powers the "vs. X" badge. None for non-snipe ideas.
    auto_promoted_at: datetime | None # v0.14. Stamped when a Promote ➤ click
                                     # filed the issue — idempotency guard
```

`SuperIdea` extends with `vision`, `component_idea_ids`, `mvp_phases`. Filtered ideas (`FilteredIdea`) live in their own table with `filter_reason` and `similar_to_id` so saturation telemetry can read them. Approval-check results land in `approval_checks`; verdict-audit results land in `verdict_audits`.

---

## Categories

27 of them as of v0.16. The original 13 lean security / infrastructure (it's what the project was built for); each later wave opens fresh idea space once the prior seeds saturate. They're a `dict[IdeaCategory, dict]` in `engine/categories.py`:

```
# Original 13 (IT / security)
security-tool · vulnerability-research · pqc-cryptography · nist-standards
rfc-security · crypto-infrastructure · privacy · compliance · observability
devops-tooling · automation · market-gap · self-improvement

# v0.12 scope expansion — money-friendly
automation-income · consumer-app · productivity · creator-tools

# v0.15 scope expansion — Claude / agent frontier
claude-skills-agents · ai-marketplace

# v0.16 expansion — fundable product shapes
micro-saas · vertical-saas · ecommerce-tools · fintech-tools

# v0.16 expansion — the rest of the agent ecosystem
agent-infra · claude-evals · agent-security · context-memory
```

The board membership is centralized in `models.py` so every surface stays in lockstep:

- `MONEY_CATEGORIES` (8) → **/money-bots**
- `CLAUDE_LAB_CATEGORIES` (6) → **/claude-lab**
- `SNIPER_CATEGORIES` (14: the money categories + the fat-incumbent IT/security space — security-tool, devops-tooling, observability, compliance, crypto-infrastructure, pqc-cryptography) → the hunting grounds **/sniper** churns from (the page itself filters on `snipe_score IS NOT NULL`).

The remaining categories still flow through `/explore` and the dashboard top-ideas grid.

Each entry has `description`, `seed_concepts` (~20 strings), and `domains_to_cross` (unrelated domains for cross-pollination prompts); category-specific `personas` live in `engine/llm_generator.py`. Replace the dict to retarget the engine at any portfolio.

A parallel **vertical** axis is inferred at query time from idea text: government, healthcare, education, finance, retail, hospitality, manufacturing, energy, telco. Inferred via keyword matching; cached per idea ID. Used by the explore page filter and the dashboard "Browse by Industry" panel.

---

## The 5 generation modes (v0.13)

`engine/llm_generator.py` rotates through these. The picker prefers under-represented modes so the rotation self-balances.

| Mode | What it pitches |
|------|-----------------|
| `novel` | A fresh problem-solution pair the persona feels acutely. Concrete enough to draw a one-screen demo on a napkin. |
| `inversion` | Pick a paid SaaS the persona is stuck paying for; pitch the open-source / self-hosted / free version. |
| `bundle` | Three+ overlapping tools the persona pays for; pitch the unified product, name the specific tools being consolidated. |
| `microservice` | Take a big complex tool; extract one 100-line utility that does ONE thing better than the parent. Unix-philosophy single-job tool. |
| `adversarial` | Identify an assumption everyone in this category takes for granted but is wrong (or becoming wrong). Pitch the project that exploits the gap. |

Each call also picks a **category-specific persona** (indie hackers for money-bots, CISOs for security tools, parents for consumer apps, agent authors / MCP integrators / prompt-eng leads / marketplace founders for Claude Lab, …) and injects the 30 most-recent active names from the same category as anti-similarity hints: "do NOT produce anything resembling these." This pre-empts the regrowth pattern that the INSERT-time dedup gates would otherwise catch reactively.

---

## The 8 artifact shapes (v0.15a — Claude Lab only)

On Claude Lab Churn clicks, the LLM-first generator additionally picks one of 8 artifact shapes and injects a per-shape prompt section (~150 words) that pins down what the LLM should produce. The picker prefers under-represented shapes via `pick_least_used_artifact(db, category)` — same rotation discipline as `pick_least_used_mode`.

| Shape | What the LLM is asked to design |
|-------|--------------------------------|
| `skill` | Reusable capability bundling instructions + assets the agent loads on demand. Needs a trigger condition and an explicit win condition. |
| `sub-agent` | Specialised agent invoked from a primary agent for a delegated job (review, refactor, secops, …). Needs invocation contract + returns-shape + scope boundary. |
| `mcp-server` | MCP server exposing a tightly-scoped tool family. Needs tool list + auth shape + deployment story. |
| `hook` | Lifecycle hook (PreCompact, SessionStart, …) that injects context or enforces policy. Needs trigger event + payload contract + backout path on failure. |
| `slash-command` | Operator-invoked command that runs a fixed sequence. Needs invocation grammar + arg schema + output spec. |
| `workflow` | Multi-step orchestration over skills / sub-agents / commands toward a named outcome. Needs the DAG + recovery shape + success criterion. |
| `protocol` | Convention multiple agents follow to coordinate. Needs framing + versioning + negotiation path. |
| `ability` | First-class capability primitive (e.g. self-verify-output, query-own-traces). Needs I/O contract + failure modes + inference-time cost. |

The artifact shape is stored on the idea row (`artifact_type` column) and surfaced on the Claude Lab card as a colored badge. The `/api/churn` response carries it alongside the idea fields so the operator can see how the dice landed.

Combinatorics for one Claude Lab Churn click: **6 categories × 5 modes × 8 artifacts × ~10 personas ≈ 2,400 distinct starting frames**, before the anti-similarity injection narrows further. Money Bots Churn at 8 × 5 × ~10 ≈ 400 frames — no artifact rotation there because the shape on those categories is always "product."

---

## The Sniper board (v0.16)

Money Bots and Claude Lab both generate from a blank page. The Sniper board flips the risk: it starts from **demand that's already been proven with real money**, then finds the opening. It's how the best challengers actually win — Cal.com vs Calendly, Plausible vs Google Analytics, Posthog vs Amplitude. Comping against a market-proven incumbent is the single highest-signal way to de-risk an idea.

Every snipe is anchored on a **named, real incumbent** — if the generator can't name one, the idea fails the gate. The pitch is forced into a fixed shape: *incumbent X proves demand → its structural weakness is Y → we wedge with Z from beachhead B → because now N.*

**Web-grounded, not from memory.** Before generating, `feeds/market_intel.py` pulls live, keyless signal on the chosen incumbent and injects it into the prompt:

- **Hacker News (Algolia)** — discussion volume + complaints + "<incumbent> alternative" threads. Points and comments are a real proxy for proven demand and appetite to displace.
- **GitHub** — open-source challengers ranked by stars. Star counts quantify how much appetite for an alternative already exists.

Both are cached per incumbent (24h TTL) and degrade to empty on a network blip — a snipe still generates, it just carries weaker grounding. Every comp traces to a dated source.

**Seven wedge angles** rotate so the board doesn't pitch fifty cheaper-clones — the variety engine, same discipline as the artifact-shape rotation. The chosen angle rides in the `artifact_type` column and shows as a pill on the card:

| Angle | The opening it exploits |
|-------|-------------------------|
| `price-snipe` | Incumbent got greedy — PE-owned, enshittified, surprise fees. Win on honest, lower pricing. |
| `unbundle` | A bloated suite where customers pay for 20 features to use 2. Extract one as a sharp standalone. |
| `down-market` | Enterprise-only pricing + complexity locks out the SMB / solo long tail. Ship the affordable version. |
| `vertical` | A horizontal tool blind to a trade's real workflow. Build the deeply-vertical fit. |
| `ai-native` | The incumbent's core workflow is now 10× cheaper with LLMs. Rebuild it AI-native. |
| `open-source` | Closed, no API, data hostage. Ship the OSS / self-hostable / API-first challenger. |
| `compliance-shift` | A new mandate or deadline the incumbent is slow on. Nail the new requirement first. |

`snipe_score` (`engine/snipe.py`) is the fourth axis: a free heuristic — named-incumbent gate + grounded-demand + wedge + why-now + beachhead signals — with a Haiku/Opus tie-break in the borderline band. The board sorts on it; the `target_incumbent` powers the `vs. X` badge. The `snipe` cadence generates a couple of grounded snipes every 6h across the hunting grounds (watermark-gated so reloads don't double-fire).

---

## Labs — autonomous avenues (v0.17)

The three boards all do the same verb — *generate an idea, score it on an axis*. Stack them against the full lifecycle (scan → generate → score → decide → build → launch → measure → learn) and the back half is empty: the engine *thinks* and never *does, measures, or learns*. v0.17 adds six avenues that close that gap, grouped under a **`/labs`** hub.

| Avenue | Phase | What it thinks | What it does | Surface |
|--------|-------|----------------|--------------|---------|
| **Scoreboard** | Learn | "Were my predictions right?" | Captures realized outcome signals (top OSS-challenger stars for each named incumbent) and reports predicted-vs-realized per axis + recommendations. Recalibration stays human-gated. | `/scoreboard`, `outcome_signals` table, daily `scoreboard` cadence (`engine/scoreboard.py`) |
| **Foundry** | Build | Architecture + file tree for a top idea | Generates a ready-to-create starter repo plan (tree, starter issues, README). Repo creation stays human-gated via the existing scaffold flow. | `/foundry`, `POST /api/foundry/plan/{id}` (`engine/foundry.py`) |
| **Pulse** | React | "What just changed in the world?" | Pulls live Hacker News + GitHub-trending signal, picks the hottest, and generates a fresh idea anchored to it (the generator's new `seed` hook). Event-driven, not timer-driven. | `/pulse`, `POST /api/pulse/churn`, `pulse` cadence (`feeds/pulse.py`) |
| **Cartographer** | Strategize | White-space + saturation across the whole corpus | Builds an atlas and a "State of the Forge" memo with the recommended next bet. | `/cartographer`, weekly `cartographer` cadence (`engine/cartographer.py`) |
| **Kill Board** | Critique | "Why does this fail? Who's already doing it?" | A pre-mortem: ranks ideas by survival odds (most-likely-to-die first) and makes the strongest case *against* each. | `/killboard`, `POST /api/premortem/{id}` (`engine/premortem.py`) |
| **Launchpad / Recruiter** | Per-idea | Go-to-market & staffing | Launchpad drafts a launch-ready GTM brief (positioning, first-10-customers, channels, copy); Recruiter estimates the staffed build (roles, person-weeks, cost band). | Buttons on any `/ideas/{id}` page (`engine/launchpad.py`, `engine/recruiter.py`) |

Every avenue degrades gracefully without an LLM (deterministic heuristic fallback) and without network (external fetches degrade to empty), so the pages always render. The autonomous cadences (`scoreboard`, `cartographer`, `pulse`) join the in-process scheduler.

---

## v0.15 / v0.15a bug fixes worth calling out

A few quiet correctness fixes that landed alongside the bigger features:

- **`app.js` here-doc escaping**: ten `\!` sequences had crept into the JS source and broke every JS-driven handler (Reject button, modal open/close, hover tooltip, issue-reporter form). Fixed in v0.14c. The same pattern then bit `style.css` — four `\!important` declarations were silently invalid — and was fixed in v0.15.
- **Stale "✓ promoted" badge on /money-bots**: the template gated on `auto_promoted_at IS NOT NULL`, which left the badge stuck on ideas that had been rejected after promotion. Now gates on `status='approved' AND auto_promoted_at IS NOT NULL`.
- **"database is locked" on /reject**: an asyncio write-lock was added to the SQLite layer, `busy_timeout` was bumped to 60s, and the fundability rescore batch was reduced from 50 → 5 per cycle. Backend latency is now 4–13ms across all paths.

---

## Deployment notes

The project runs on a single host as one long-lived FastAPI service (`forge-serve`). The in-process multi-cadence scheduler boots with the app — no systemd timers, no cron daemon. File writes deploy instantly via `uvicorn --reload`. The unit files under `scripts/` (`project-forge-*.service`/`.timer`) are kept for reference but are not the active path; the in-process scheduler supersedes them.

The project assumes a normal Python environment with `gh` CLI and (optionally) `claude` CLI on PATH. There's no Docker image checked in.

---

## Testing

```bash
pytest tests/ -v                                  # ~1340+ tests
pytest tests/ -k "sniper or snipe or market_intel" # subset — v0.16 Sniper board
pytest tests/ -k "ambition or claude_lab" -v      # subset — v0.15 surface area
pytest tests/ -k "artifact_type" -v               # subset — v0.15a artifact rotation
pytest tests/ --cov=project_forge --cov-report=term-missing
ruff check src/ tests/
```

CI runs the same on a self-hosted runner.

---

## Roadmap

The north star: **from "generates and scores ideas" → "builds, ships, and learns from real outcomes"** — without losing the variety the LLM-first pivot won back. Full detail (with shipped-vs-planned mapping and per-item sketches) lives in **[ROADMAP.md](ROADMAP.md)**.

| Horizon | Focus |
|---------|-------|
| **Now** | Real outcome data into the Scoreboard (revenue / inline 👍👎, not just OSS-challenger stars) · Foundry generates a *working* MVP + smoke tests, not just a skeleton · cost-per-cadence attribution + idea provenance receipts |
| **Next** | Launchpad → a deployed landing page for demand validation · A/B fundability + persona-learning weights (calibrate the scorers once outcome data exists) · Slack/Discord notifications + a weekly engine-written retro |
| **Later** | Model router + per-cadence budget guard (cost-resilience) · idea-quality regression suite (canary vs prompt drift) · opt-in public marketplace / per-idea share links |

It's a personal project, so this is intent and direction — not a delivery commitment.

---

## Status

Active. The in-process scheduler fires generation hourly, issue-sync hourly, grounded snipes every 6h, fundability + ambition scoring + verdict audits + feed refresh daily, and the autonomous challenge cadence weekly. Promotion to GitHub is human-gated via the Promote ➤ button on /money-bots and /claude-lab. Issues / PRs welcome but not necessarily merged on any timeline — see `CONTRIBUTING.md` and `SECURITY.md`.

See `ROADMAP.md` for what's next. Recurring themes: closing the loop from "approved" → "running MVP" without an operator click, embeddings-based semantic dedup, and a quality-eval harness that scores the engine's own output over time.

---

## License

MIT. See `LICENSE` (or the `[project] license` field in `pyproject.toml`).
