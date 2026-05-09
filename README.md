# Project Forge

![Version](https://img.shields.io/badge/version-0.11.0-blue) ![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/rayketcham-lab/project-forge/actions/workflows/ci.yml/badge.svg) ![Tests](https://img.shields.io/badge/tests-1000+-passing?color=brightgreen)

An autonomous IT project idea generator. It runs on a cron, calls an LLM (or falls back to deterministic heuristics), scores feasibility, deduplicates aggressively, and stores everything in SQLite. A web dashboard lets a human review, approve, and optionally scaffold approved ideas into GitHub repos. Nothing ships without human review.

This is a personal project that's been running for several months. It's open-sourced because some of the patterns (LLM backend abstraction, dedup strategies, multi-axis filtering, self-improvement loop) might be useful to others. It is not a product — there's no support, no roadmap commitment, and no SLA.

> [!NOTE]
> **No API key required.** The LLM backend resolver picks the best path automatically:
> 1. **Anthropic API** when `ANTHROPIC_API_KEY` is set
> 2. **Claude Code CLI** when `claude` is on `$PATH` (uses your Claude subscription)
> 3. **Static heuristics** when neither
>
> Override via `FORGE_LLM_BACKEND={api|claude_code|static}` and `FORGE_LLM_MODEL={sonnet|opus|haiku}` (default: sonnet).

---

## What it actually does

| Step | Implementation |
|------|---------------|
| **Generate** | An LLM produces an idea given a prompt that mixes seed concepts, optional saturation summary (anti-seeds derived from prior rejections), and optional external feed items (NVD CVEs / arXiv / IETF drafts). Without an LLM, deterministic auto-scan crosses seed concepts with domain lists. |
| **Score** | A composite score (0.0–1.0) of three components, weighted: **novelty** (0.4), **specificity** (0.35), **scope realism** (0.25). See `engine/scorer.py`. |
| **Dedup** | Three layers: SHA-256 content hash, tagline token-overlap similarity (Jaccard, threshold 0.7), and for super ideas a cluster-signature anchor. Filtered ideas are written to a separate audit table — they're a signal, not silently dropped. |
| **Synthesize** | Cluster active ideas by category-pair theme. With an LLM, ask it to name the unifying capability gap. Without one, slot-fill `{Keyword1} & {Keyword2} {Suffix}`. |
| **Compare** | Token-overlap (Jaccard) between an idea and a GitHub repo's README + topics + description. Returns a verdict (new / enhance / duplicate). |
| **Scaffold** | Calls `gh repo create`, pushes a language-appropriate template tree (Python / Rust / Go / Node), opens 3–5 starter issues from the idea's MVP scope, applies labels. |

---

## Quick Start

```bash
git clone https://github.com/rayketcham-lab/project-forge.git
cd project-forge
pip install -e ".[dev,test]"

# Run tests
pytest tests/ -v

# Start dashboard
forge-serve     # http://localhost:55443

# Generate one idea (uses whatever backend resolves)
forge-generate
```

The dashboard is plain HTML + a small amount of vanilla JS. No build step.

---

## Dashboard

| Page | What's there |
|------|---------------|
| `/` (Home) | Stats grid, top ideas, super ideas tab, "Add Idea" tab (URL ingest, one-shot text ingest, 5-phase wizard), category and industry browse cards |
| `/explore` | All ideas with two-axis filtering: industry vertical (inferred from text) + tech category. Status filter, full-text search, pagination. |
| `/ideas/{id}` | Detail view with description, score breakdown, related ideas, compare-to-repo, approve/reject/scaffold actions, challenge form |
| `/projects` | List of scaffolded projects |
| `/thinktank` | Self-improvement pipeline: engine activity heartbeat, AI-proposed code patches (Decompose X / Add tests for X), GitHub roadmap |

The "Add Idea" tab has three independent paths:

- **From URL** — paste a link, the tool fetches the page, sends content + metadata to the LLM, gets an idea back
- **Text — Quick** — paste a fragment, one LLM call, get an idea
- **5-Phase Wizard** — Discover → Differentiate → Audience → Constraints → Synthesize. Each phase asks 2–3 follow-up questions based on prior answers. Final phase produces a draft you can edit before saving.

---

## API

A subset of the routes exposed by `web/routes.py`. There are more — see the `@router.get/post` decorators in that file for the full list, or hit `/docs` for the auto-generated OpenAPI page.

| Method | Path | Notes |
|--------|------|-------|
| `GET`  | `/health` | `{"status": "ok"}` |
| `GET`  | `/api/stats` | Aggregate counts |
| `GET`  | `/api/categories` | Category counts + average score |
| `GET`  | `/api/ideas` | Paginated list |
| `POST` | `/api/ideas/{id}/compare` | Compare to a repo |
| `POST` | `/api/ideas/from-url` | Ingest from URL |
| `POST` | `/api/ideas/from-text` | Ingest from text fragment |
| `POST` | `/api/ideas/builder/step` | One step of the wizard |
| `POST` | `/api/ideas/builder/save` | Save the wizard's final draft |
| `POST` | `/ideas/{id}/approve` | Move to approved |
| `POST` | `/ideas/{id}/reject` | Move to rejected |
| `POST` | `/ideas/{id}/scaffold` | Scaffold to GitHub |

Non-read methods require a Bearer token when `FORGE_API_TOKEN` is set. The dashboard uses an ephemeral per-process token rendered into the page meta tag — see `web/auth.py`.

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_DB_PATH` | `data/forge.db` | SQLite path |
| `FORGE_PORT` | `55443` | Web port |
| `ANTHROPIC_API_KEY` / `FORGE_ANTHROPIC_API_KEY` | (unset) | API key, optional |
| `FORGE_LLM_BACKEND` | auto | `api` \| `claude_code` \| `static` \| `none` |
| `FORGE_LLM_MODEL` | `sonnet` | `sonnet` \| `opus` \| `haiku` |
| `FORGE_SUPER_REASONING` | unset | Set to `1` to use the LLM for super-idea cluster naming (Phase 6 path) |
| `FORGE_API_TOKEN` | (unset) | If set, all non-read API methods require Bearer auth |
| `FORGE_GITHUB_OWNER` | `rayketcham-lab` | Default GitHub org for scaffolded repos |

---

## Architecture

```
src/project_forge/
  config.py                  Pydantic-settings
  models.py                  Idea, FilteredIdea, GenerationRun, IdeaCategory, etc.
  engine/
    generator.py             Anthropic API path (IdeaGenerator + LLMBackendIdeaGenerator)
    llm_backend.py           Backend resolver: AnthropicAPI | ClaudeCode | static
    prompts.py               Generation, URL-ingest, text-ingest prompts
    diversity_prompts.py     Combinatoric / contrarian / persona templates
    categories.py            CATEGORY_SEEDS dict (13 categories)
    scorer.py                novelty + specificity + scope_realism → composite
    dedup.py                 Content-hash + tagline-similarity gate
    super_ideas.py           Clustering + slot-fill or LLM-reasoned naming
    super_reasoning.py       Cluster signature + LLM cluster naming (Phase 6)
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
    audit.py                 Audit log helpers
    bulk.py                  Bulk operations
    repo_registry.py         Tracked repos
    router.py                Portfolio routing (contribute vs. new repo)
  feeds/
    nvd.py / arxiv.py / ietf.py    Parser + fetcher per source
    cache.py / health.py / _http.py
  storage/
    db.py                    SQLite (WAL), schema, dedup queries
  web/
    app.py                   FastAPI factory, dashboard token, CSP middleware
    auth.py                  Bearer token middleware
    routes.py                All page + API routes
    templates/               Jinja2 (dashboard, explore, idea_detail, thinktank, projects)
    static/                  app.js, style.css
  cron/
    runner.py                Single-shot cron entry
    scheduler.py             Full cycle: generate → score → dedup → save → route
    auto_scan.py             No-LLM generation
    introspect_runner.py     SI cycle
    self_improve_runner.py   Apply SI patches, open PRs
    horizontal.py            Super-idea generation runner
    expand_runner.py         Cron entry for super-idea expansion
    review_runner.py         Idea-quality review cycle
  scaffold/
    builder.py               Project structure
    github.py                gh CLI wrapper
    templates/               Per-language scaffold templates
```

---

## Self-improvement loop

There's a pipeline that proposes patches to the codebase itself:

- **Static introspector** (no LLM): walks `src/`, finds files >300 lines and modules without tests. Emits `Decompose X` and `Add tests for X` proposals with concrete suggestions (longest functions, public symbols).
- **LLM introspector** (with API key or Claude Code CLI): builds a prompt with file tree, recent commits, lint status, telemetry signals (saturation, filter rate, coverage gaps), asks for one targeted patch with a named target metric.
- **Shadow validation** (logic shipped, runner integration deferred): parses target metric from the proposal, snapshots telemetry before/after, only allows merge if the metric moved correctly.

Promoted proposals appear in the Think Tank dashboard. Whether they auto-merge depends on the `self_improve_runner.py` config — by default it opens a PR and waits for review.

---

## Data model

```python
class Idea(BaseModel):
    id: str                          # 12-char hex, generated
    name: str
    tagline: str
    description: str
    category: IdeaCategory           # 13-value StrEnum
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
```

`SuperIdea` extends with `vision`, `component_idea_ids`, `mvp_phases`. Filtered ideas (`FilteredIdea`) live in their own table with `filter_reason` and `similar_to_id` so saturation telemetry can read them.

---

## Categories

13 of them. The default seeds lean security / infrastructure (it's what the project was built for). They're a `dict[IdeaCategory, dict]` in `engine/categories.py`:

```
security-tool · vulnerability-research · pqc-cryptography · nist-standards
rfc-security · crypto-infrastructure · privacy · compliance · observability
devops-tooling · automation · market-gap · self-improvement
```

Each entry has `description`, `seed_concepts` (list of strings), and `domains_to_cross` (list of unrelated domains for cross-pollination prompts). Replace the dict to retarget the engine at any portfolio.

A parallel **vertical** axis is inferred at query time from idea text: government, healthcare, education, finance, retail, hospitality, manufacturing, energy, telco. Inferred via keyword matching; cached per idea ID. Used by the explore page filter and the dashboard "Browse by Industry" panel.

---

## Deployment notes

The project runs on a single host as a set of systemd units, one per cron-driven cycle (introspect, expand, self-improve, review) plus a long-lived web service. Unit files live in `scripts/` for reference; deploying them to `/etc/systemd/system/` is left to the operator.

The web service can run with uvicorn `--reload` for development. The cron units re-execute their `.sh` wrappers each firing — environment variables in those wrappers control LLM backend choice without touching the systemd unit files.

There's no Docker image checked in. The project assumes a normal Python environment with `gh` CLI and (optionally) `claude` CLI on PATH.

---

## Testing

```bash
pytest tests/ -v                                  # ~1000 tests, ~50s
pytest tests/ -k "telemetry or super" -v          # subset
pytest tests/ --cov=project_forge --cov-report=term-missing
ruff check src/ tests/
```

CI runs the same on a self-hosted runner.

---

## Status

Active. Generation runs every 30 minutes, super-idea synthesis hourly, self-improvement daily. The dashboard at the operator's host shows live activity. Issues / PRs welcome but not necessarily merged on any timeline — see `CONTRIBUTING.md` and `SECURITY.md`.

---

## License

MIT. See `LICENSE` (or the `[project] license` field in `pyproject.toml`).
