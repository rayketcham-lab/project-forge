# Project Forge

![Version](https://img.shields.io/badge/version-0.15-blue) ![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/rayketcham-lab/project-forge/actions/workflows/ci.yml/badge.svg) ![Tests](https://img.shields.io/badge/tests-1290+-passing?color=brightgreen)

An autonomous project idea generator. It runs an in-process scheduler inside the FastAPI app, calls an LLM (or falls back to deterministic heuristics), scores ideas on **three orthogonal axes** — feasibility (can we build it), **fundability** (can we sell it), and **ambition** (does it push Claude / agent capability) — deduplicates aggressively, and stores everything in SQLite. A web dashboard lets a human review, approve, and — with a single click — promote ideas into GitHub issues with full MVP specs. **Promotion is human-gated**: the engine ranks and surfaces, you approve.

Two themed surfaces frame the corpus:

- 💰 **/money-bots** — top ideas in money-friendly categories (automation-income, creator-tools, consumer-app, productivity), sorted by `fundability_score`. Money is the goal.
- 🧠 **/claude-lab** (v0.15) — top ideas in the Claude / agent ecosystem categories (claude-skills-agents, ai-marketplace), sorted by `ambition_score`. Frontier capability is the goal — new skills, sub-agents, MCP servers, marketplaces, attribution systems. Push Claude into the 35th century.

Each page has its own **Churn Now** button that fires the LLM-first generator on demand against the right category family and the right scoring axis.

> Operating philosophy: **autonomous, human-driven**. The engine generates, scores, dedups, sweeps, and audits itself on a schedule. Anything that touches external state (GitHub issues, repos) is one click away — never autonomous. The v0.14 weekly auto-promote cadence was removed in v0.14b after a uvicorn-reload bug fired it three times. Today, the Money Bots page exposes a Promote ➤ button per card; nothing else can flip an idea to `approved` without an operator.

This is a personal project that's been running for several months. It's open-sourced because some of the patterns (LLM backend abstraction, multi-stage dedup, persona-driven generation, fundability scoring, in-process multi-cadence scheduling) might be useful to others. It is not a product — there's no support, no roadmap commitment, and no SLA.

> [!NOTE]
> **No API key required.** The LLM backend resolver picks the best path automatically:
> 1. **Anthropic API** when `ANTHROPIC_API_KEY` is set
> 2. **Claude Code CLI** when `claude` is on `$PATH` (uses your Claude subscription)
> 3. **Static heuristics** when neither
>
> Override via `FORGE_LLM_BACKEND={api|claude_code|static}` and `FORGE_LLM_MODEL={sonnet|opus|haiku}` (default: sonnet). Cheap, batchy work (the LLM-first generator, fundability tie-breaks, semantic-dedup verification) automatically routes to Haiku 4.5 — set `FORGE_HAIKU_API_KEY` if you want to use a dedicated cheap key for those calls without exposing your Sonnet/Opus key.

---

## What it actually does

| Step | Implementation |
|------|---------------|
| **Generate** | Two paths. The **LLM-first generator** (`engine/llm_generator.py`, v0.13+) asks Haiku 4.5 for a whole idea using one of 5 generation modes (novel / inversion / bundle / microservice / adversarial), a category-specific persona, and anti-similarity injection (the 30 most-recent active names — "do NOT produce anything like these"). The **template generator** (`auto_scan.generate_local_idea`) is the deterministic fallback when no backend is reachable. Optional saturation summary, portfolio context, and external feed items (NVD CVEs / arXiv / IETF drafts) are mixed into every prompt. |
| **Score (feasibility)** | A composite score (0.0–1.0) of three components, weighted: **novelty** (0.4), **specificity** (0.35), **scope realism** (0.25). See `engine/scorer.py`. |
| **Score (fundability)** | v0.13. Two-stage: a heuristic looks at tech_stack payment hints (stripe, paddle, lemonsqueezy), paid-product keywords in description / mvp_scope, buyer signal in market_analysis, and a per-category bonus. Borderline scores in `[0.35, 0.70]` get a Haiku tie-break call (~$0.001) for a finer signal. See `engine/fundability.py`. |
| **Score (ambition)** | v0.15. Same shape as fundability but rewards frontier-ness instead of monetizability. Heuristic looks at category bonus (CLAUDE_SKILLS_AGENTS, AI_MARKETPLACE), frontier-keyword density (mcp, sub-agent, attribution, registry, provenance, reproducibility, …), Anthropic / MCP stack signal, and description depth. Borderline `[0.40, 0.75]` gets a Haiku tie-break. See `engine/ambition.py`. |
| **Dedup (INSERT-time gates, v0.11)** | Four layers, all fired before the idea is committed: SHA-256 content hash, tagline token-overlap (Jaccard ≥ 0.7), **name-token Jaccard** on vertical-stripped names, **super-component overlap** for super-ideas, and a **vertical-cap** that rejects an Nth clone in the same vertical family. Filtered ideas are written to a separate audit table with `filter_reason` and `similar_to_id` — they're a signal, not silently dropped. A Haiku semantic-dedup tie-breaker (v0.12) fires for borderline near-rejections. |
| **Synthesize** | Cluster active ideas by category-pair theme. With an LLM (`FORGE_SUPER_REASONING=1`), ask it to name the unifying capability gap. Without one, slot-fill `{Keyword1} & {Keyword2} {Suffix}`. |
| **Compare** | Token-overlap (Jaccard) between an idea and a GitHub repo's README + topics + description. Returns a verdict (new / enhance / duplicate). |
| **Approval check (v0.11)** | When a human flips an idea to `approved`, a non-blocking think-tank coherence checker runs: empty tech stack? mvp scope drifting from description? super-idea components with no shared theme? fake-perfect feasibility score? Results land in `approval_checks` and surface as a banner on the idea detail page. |
| **Verdict audit (v0.11)** | A "who watches the watcher" cadence samples recent LLM verdicts (challenge / review) and re-runs them with a flipped tone. Divergences land in `verdict_audits` for inspection. |
| **Manual promote (v0.14b)** | One click on /money-bots → POST `/api/promote/{id}` → `gh issue create` with the full MVP spec + market analysis + tech stack → idea flips to `approved` and stamps `auto_promoted_at` so re-clicks return the existing issue. The autonomous weekly cadence was removed after a uvicorn-reload bug fired it three times — promotion is human-gated now. |
| **Issue sync (v0.14c)** | Hourly cadence. Pulls live GitHub state for every approved + promoted idea via `gh issue view --json state,stateReason`. CLOSED + COMPLETED → `contributed`. CLOSED + NOT_PLANNED → `archived`. OPEN → leave alone. Keeps the dashboard honest after an operator closes an issue. |
| **Scaffold** | Calls `gh repo create`, pushes a language-appropriate template tree (Python / Rust / Go / Node), opens 3–5 starter issues from the idea's MVP scope, applies labels. |

---

## Quick Start

```bash
git clone https://github.com/rayketcham-lab/project-forge.git
cd project-forge
pip install -e ".[dev,test]"

# Run tests (~1280 tests, ~50s)
pytest tests/ -v

# Start dashboard — the in-process scheduler boots with it
forge-serve     # http://localhost:55443

# Generate one idea (uses whatever backend resolves)
forge-generate

# Trim an over-saturated database one-shot (e.g. archive the long tail
# below a feasibility cutoff, then run uniqueness gates retroactively)
python scripts/trim-now.py
```

The dashboard is plain HTML + a small amount of vanilla JS. No build step. Every cadence kicks in once `forge-serve` is running — there's no separate cron daemon to manage.

---

## Dashboard

| Page | What's there |
|------|---------------|
| `/` (Home) | Stats grid, top ideas, super ideas tab, "Add Idea" tab (URL ingest, one-shot text ingest, 5-phase wizard), category and industry browse cards, **Money Bots card** linking through to `/money-bots`. |
| `/explore` | All ideas with two-axis filtering: industry vertical (inferred from text) + tech category. Status filter, challenged-only filter, full-text search, pagination. |
| `/money-bots` | v0.14. Top monetizable ideas sorted by `fundability_score DESC` across the four money-friendly categories (`automation-income`, `creator-tools`, `consumer-app`, `productivity`). Per-category filter chips, total in-scope count, and a **Churn Now** button that fires the LLM-first generator on demand (~$0.003/click). |
| `/claude-lab` | v0.15. Top frontier ideas sorted by `ambition_score DESC` across the two Claude-ecosystem categories (`claude-skills-agents`, `ai-marketplace`). Same shape as /money-bots but bound to a different category family and a different scoring axis. **Churn a Frontier Idea** button POSTs `/api/churn` with `lab=claude` so the right family + axis apply. |
| `/ideas/{id}` | Detail view with description, score breakdown, related ideas, compare-to-repo, approve/reject/scaffold actions, challenge form, approval-check banner when something looks incoherent. |
| `/projects` | List of scaffolded projects. |
| `/thinktank` | Self-improvement pipeline: engine activity heartbeat, AI-proposed code patches (Decompose X / Add tests for X), GitHub roadmap. |
| `/thinktank/audit` | Verdict-audit results — divergences between original LLM verdicts and the meta-audit's flipped-tone re-runs. |

The "Add Idea" tab has three independent paths:

- **From URL** — paste a link, the tool fetches the page (with SSRF guard), sends content + metadata to the LLM, gets an idea back.
- **Text — Quick** — paste a fragment, one LLM call, get an idea.
- **5-Phase Wizard** — Discover → Differentiate → Audience → Constraints → Synthesize. Each phase asks 2–3 follow-up questions based on prior answers. Final phase produces a draft you can edit before saving.

### Card UX (v0.14c)

Every idea card across the dashboard, money-bots, super-ideas, and explore pages carries the same triage UX:

- **Hover tooltip** — 220 ms after mouse-over, a floating panel shows name + tagline + feasibility / fundability / mode pills + the first 280 chars of the description. Reads from `data-*` attributes for an instant first paint, then lazy-fetches `/api/ideas/{id}` to fill the description. Move off the card and it disappears.
- **In-window modal** — click anywhere on a card (not on a link/button) and the full idea opens in place: description, market, MVP scope, tech-stack chips, the GitHub issue if promoted, a Reject button, and a "Full page ↗" escape hatch. Backdrop / × / ESC all close. No more page navigations for just-peek-at-an-idea.
- **Per-card Reject button** — a small red × top-right of every card, invisible until you hover. Confirms via dialog, posts to `/ideas/{id}/reject`, removes the card from the DOM.
- **Churn Now button** (on /money-bots) — fires `/api/churn` for the current category filter. New idea appears in the grid after a 1.5 s reload. ~$0.003/click at Haiku.
- **Promote ➤ button** (on /money-bots, only for un-promoted ideas) — confirms, posts to `/api/promote/{id}`, files the GitHub issue, flips status to `approved`. The only path that touches GitHub state.

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
| `GET`  | `/api/money-bots/top` | Top monetizable ideas (v0.14) |
| `GET`  | `/api/claude-lab/top` | Top frontier ideas sorted by ambition_score (v0.15) |
| `POST` | `/api/churn` | On-demand idea generation. Body: `{ "category": "...", "lab": "money" \| "claude" }`. The `lab` field switches both the allowed category set and the scoring axis (fundability vs ambition). (v0.14 + v0.15) |
| `POST` | `/api/promote/{id}` | Manual promote → GH issue (v0.14b — replaces the removed auto-promote cadence) |
| `GET`  | `/api/backend-info` | Diagnostic: which LLM backend is in use + censored view of the API-key env vars seen by the running process |
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
| `FORGE_HAIKU_API_KEY` | (unset) | Dedicated key for Haiku 4.5 cheap-path calls (LLM-first generator, fundability tie-break, semantic dedup). Falls back to the primary key if unset. |
| `FORGE_LLM_BACKEND` | auto | `api` \| `claude_code` \| `static` \| `none` |
| `FORGE_LLM_MODEL` | `sonnet` | `sonnet` \| `opus` \| `haiku` |
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
| `FORGE_ISSUE_SYNC_INTERVAL_HOURS` | 1 | v0.14c. Sync `auto_promoted_at` ideas with their live GH issue state |
| `FORGE_CHALLENGE_INTERVAL_HOURS` | 168 | Autonomous adversarial pass on top unchallenged ideas |
| `FORGE_SCHED_INITIAL_DELAY_SEC` | 60 | Boot grace period before the first tick |
| `FORGE_FEEDS_DIR` | `<db_dir>/feeds` | Where the NVD / arXiv / IETF caches live |

### Money-flipper (v0.14b — manual)

These knobs gate the **manual** Promote ➤ button on /money-bots and the underlying `/api/promote/{id}` endpoint. The cadence that used to read them autonomously was removed in v0.14b.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_PROMOTE_CATEGORIES` | `automation-income,creator-tools,consumer-app,productivity` | Comma-separated category whitelist — only ideas in these categories surface on /money-bots |
| `FORGE_PROMOTE_MIN_SCORE` | `0.55` | Minimum `fundability_score` for the candidate-picker to consider the idea promotable |
| `FORGE_PROMOTE_REPO` | `<owner>/<repo>` | Where the promotion issue gets filed. Set this to a dedicated "money-bot board" repo if you want them off your main forge backlog. |

---

## Architecture

```
src/project_forge/
  config.py                  Pydantic-settings
  models.py                  Idea, FilteredIdea, GenerationRun, IdeaCategory, etc.
                             v0.13+ columns: generation_mode, fundability_score
                             v0.14 column:   auto_promoted_at
  engine/
    generator.py             API path (IdeaGenerator + LLMBackendIdeaGenerator)
    llm_generator.py         v0.13. LLM-first generator with 5 modes + persona rotation + anti-similarity
    llm_backend.py           Backend resolver: AnthropicAPI | ClaudeCode | static
    prompts.py               Generation, URL-ingest, text-ingest prompts
    diversity_prompts.py     Combinatoric / contrarian / persona templates
    categories.py            CATEGORY_SEEDS dict (17 categories: 13 IT/security + 4 money)
    scorer.py                novelty + specificity + scope_realism → composite
    fundability.py           v0.13. Heuristic + Haiku tie-break in the [0.35, 0.70] band
    ambition.py              v0.15. Frontier-bias score for the Claude / agent corpus.
                             Heuristic (category + frontier keywords + Anthropic stack +
                             description depth) + Haiku tie-break in [0.40, 0.75].
    dedup.py                 INSERT-time gates: content-hash + tagline + name-Jaccard + super-component + vertical-cap
    super_ideas.py           Clustering + slot-fill or LLM-reasoned naming
    super_reasoning.py       Cluster signature + LLM cluster naming
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
    siphon.py                Bulk import / cleanup helpers
    audit.py                 Audit log helpers
    bulk.py                  Bulk operations
    repo_registry.py         Tracked repos
    router.py                Portfolio routing (contribute vs. new repo)
  feeds/
    nvd.py / arxiv.py / ietf.py    Parser + fetcher per source
    cache.py / health.py / _http.py
  storage/
    db.py                    SQLite (WAL), schema, dedup queries
                             v0.11 tables: approval_checks, verdict_audits
  web/
    app.py                   FastAPI factory, lifespan, dashboard token, CSP middleware
    lifespan_scheduler.py    In-process multi-cadence scheduler (replaces systemd timers)
    auth.py                  Bearer token middleware
    routes.py                All page + API routes (incl. /money-bots, /api/churn)
    templates/               Jinja2 (dashboard, explore, idea_detail, money_bots, thinktank, thinktank_audit, projects)
    static/                  app.js, style.css
  cron/
    runner.py                Single-shot entry (still used by forge-generate)
    scheduler.py             Full-cycle orchestration (generate → score → dedup → save → route)
    auto_scan.py             No-LLM generation
    horizontal.py            Super-idea generation
    expand_runner.py         Cron entry for super-idea expansion
    introspect_runner.py     SI cycle
    self_improve_runner.py   Apply SI patches, open PRs
    review_runner.py         Idea-quality review cycle
    challenge_runner.py      Autonomous adversarial pass
    verdict_audit_runner.py  v0.11. Verdict meta-audit
    auto_promote_runner.py   v0.14. Money-flipper logic. The cadence was removed in v0.14b;
                             still used by POST /api/promote/{id} for the manual button.
    issue_sync_runner.py     v0.14c. Pulls live GH issue state for promoted ideas → DB.
  scaffold/
    builder.py               Project structure
    github.py                gh CLI wrapper
    templates/               Per-language scaffold templates
```

---

## The in-process multi-cadence scheduler

There's no systemd. The original deployment shipped one timer per cron-driven cycle in `/etc/systemd/system/`, but the runtime sandbox doesn't have access to systemd at all (no DBus, no sudo, no `/etc/systemd/` writes). v0.11 moved every cadence into the FastAPI lifespan as a single supervisor task that owns N async loops, one per `Cadence`. A child failing once is logged and retried; a child crashing repeatedly does not stop its siblings; cancelling the supervisor cancels every child.

The defaults are tuned so a single host can run the full engine on roughly **$2–3/month** of LLM spend at Haiku 4.5 prices:

```
expand            1h    cross-category + super idea generation (LLM-first)
issue_sync        1h    pull live GH state for promoted ideas → DB
review            12h   auto-archive sweeps over aged ideas
self_improve      6h    GitHub ci-queue → PR loop
introspect        24h   self-improvement idea proposals
verdict_audit     24h   "who watches the watcher" — samples recent LLM verdicts
feed_refresh      24h   NVD / arXiv / IETF cache refresh
fundability       24h   score recent ideas for monetization viability
challenge         168h  autonomous adversarial pass on top unchallenged ideas
```

There is no `auto_promote` cadence anymore. The weekly money-flipper was removed in v0.14b after a uvicorn-reload bug fired it three times in one session. The runner code at `cron/auto_promote_runner.py` stays — it's invoked by the manual `/api/promote/{id}` endpoint that the Promote ➤ button on /money-bots POSTs to.

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
    category: IdeaCategory           # 17-value StrEnum
    market_analysis: str
    feasibility_score: float         # 0.0-1.0 composite from scorer
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
    fundability_score: float | None  # v0.13. 0.0-1.0 monetization-viability
    ambition_score: float | None     # v0.15. 0.0-1.0 frontier-bias score —
                                     # how far the idea pushes Claude / agent
                                     # capability. Sorts /claude-lab DESC.
    auto_promoted_at: datetime | None # v0.14. Stamped when the money-flipper
                                     # picked this idea — idempotency guard
```

`SuperIdea` extends with `vision`, `component_idea_ids`, `mvp_phases`. Filtered ideas (`FilteredIdea`) live in their own table with `filter_reason` and `similar_to_id` so saturation telemetry can read them. Approval-check results land in `approval_checks`; verdict-audit results land in `verdict_audits`.

---

## Categories

17 of them as of v0.12. The original 13 lean security / infrastructure (it's what the project was built for); v0.12 added 4 money-friendly categories so the engine has fresh idea space once the IT seeds saturate. They're a `dict[IdeaCategory, dict]` in `engine/categories.py`:

```
# Original 13 (IT / security)
security-tool · vulnerability-research · pqc-cryptography · nist-standards
rfc-security · crypto-infrastructure · privacy · compliance · observability
devops-tooling · automation · market-gap · self-improvement

# v0.12 scope expansion — money-friendly
automation-income · consumer-app · productivity · creator-tools

# v0.15 scope expansion — Claude / agent frontier
claude-skills-agents · ai-marketplace
```

19 categories total. The `/money-bots` page reads from the four money-friendly ones; `/claude-lab` reads from the two Claude-ecosystem ones. The other 13 still flow through `/explore` and the dashboard top-ideas grid.

Each entry has `description`, `seed_concepts` (list of strings), and `domains_to_cross` (list of unrelated domains for cross-pollination prompts). Replace the dict to retarget the engine at any portfolio.

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

Each call also picks a **category-specific persona** (indie hackers for money-bots, CISOs for security tools, parents for consumer apps, …) and injects the 30 most-recent active names from the same category as anti-similarity hints: "do NOT produce anything resembling these." This pre-empts the regrowth pattern that the INSERT-time dedup gates would otherwise catch reactively.

---

## Deployment notes

The project runs on a single host as one long-lived FastAPI service (`forge-serve`). The in-process multi-cadence scheduler boots with the app — no systemd timers, no cron daemon. File writes deploy instantly via `uvicorn --reload`. The unit files under `scripts/` (`project-forge-*.service`/`.timer`) are kept for reference but are not the active path; the in-process scheduler supersedes them.

The project assumes a normal Python environment with `gh` CLI and (optionally) `claude` CLI on PATH. There's no Docker image checked in.

---

## Testing

```bash
pytest tests/ -v                                  # ~1100+ tests, ~50s
pytest tests/ -k "telemetry or super" -v          # subset
pytest tests/ --cov=project_forge --cov-report=term-missing
ruff check src/ tests/
```

CI runs the same on a self-hosted runner.

---

## Status

Active. The in-process scheduler fires generation hourly, issue-sync hourly, fundability scoring + verdict audits + feed refresh daily, and the autonomous challenge cadence weekly. Promotion to GitHub is human-gated via the Promote ➤ button on /money-bots. Issues / PRs welcome but not necessarily merged on any timeline — see `CONTRIBUTING.md` and `SECURITY.md`.

See `ROADMAP.md` for the v0.15+ plan — top three: Stripe revenue webhook (so the engine learns which categories actually convert), an edge-finder pre-pass (Haiku-derived "fresh angles" injected as anti-tropes before each generation), and auto-scaffold of the promoted idea into a real starter repo (close the loop from "approved" → "running MVP" without an operator click).

---

## License

MIT. See `LICENSE` (or the `[project] license` field in `pyproject.toml`).
