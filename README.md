# Project Forge

![Version](https://img.shields.io/badge/version-0.11.0-blue) ![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/rayketcham-lab/project-forge/actions/workflows/ci.yml/badge.svg) ![Tests](https://img.shields.io/badge/tests-1000+-passing?color=brightgreen) ![Claude](https://img.shields.io/badge/Claude_Sonnet-powered-blueviolet?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCI+PHRleHQgeT0iMTgiIGZvbnQtc2l6ZT0iMTYiPvCfpJY8L3RleHQ+PC9zdmc+)

**An autonomous, human-directed think-tank engine.** Project Forge generates, scores, synthesizes, and scaffolds IT project ideas — turning strategic gaps into GitHub repos with CI/CD, tests, and issues. The system runs autonomously; a human operator approves before anything ships.

Point it at your portfolio. Walk away. Come back to a ranked pipeline of feasibility-scored project concepts, cross-domain mega-projects, and one-click scaffolding into real repositories.

> [!NOTE]
> **No API key required.** Three LLM backends, picked automatically:
> 1. **Anthropic API** when `ANTHROPIC_API_KEY` is set (lowest latency)
> 2. **Claude Code CLI** when `claude` is on `$PATH` — cost rolls into your Claude subscription, no separate API tier
> 3. **Static heuristics** when neither — still produces ideas via deterministic seed crossing
>
> Override the auto-detect with `FORGE_LLM_BACKEND={api|claude_code|static}` and pick the model with `FORGE_LLM_MODEL={sonnet|opus|haiku}` (default: sonnet).

---

## Table of Contents

- [The Problem](#the-problem)
- [The Pipeline](#the-pipeline)
- [Features](#features)
- [Quick Start](#quick-start)
- [Dashboard](#dashboard)
- [API Reference](#api-reference)
- [Generation Engine Deep Dive](#generation-engine-deep-dive)
- [Categories](#categories)
- [Architecture](#architecture)
- [Deployment](#deployment)
- [Configuration](#configuration)
- [Example Output](#example-output)
- [Tech Stack](#tech-stack)
- [Contributing](#contributing)
- [License](#license)

---

## The Problem

Every quarter, engineering teams ask the same question: **"What should we build next?"**

The answer usually comes from a combination of executive intuition, competitor copying, and whoever talks loudest in the brainstorm. The result? Incremental features, missed market gaps, and zero cross-domain innovation.

Project Forge replaces that process with a structured, autonomous pipeline:

```
Brainstorm (manual, biased, slow)     vs.     Project Forge (autonomous, scored, scaffolded)
------------------------------------------     -----------------------------------------------
- 3 people in a room                          - 12 categories x 4 directions x N seeds
- "I think we should..."                      - Feasibility scored 0.0-1.0
- Sticky notes on a wall                      - Cross-domain super-ideas synthesized
- Nothing gets built                           - One click to GitHub repo with CI/CD
```

---

## The Pipeline

```
                    +------------------+
                    |  Seed Concepts   |
                    |  + Domains       |
                    |  + Directions    |
                    +--------+---------+
                             |
                    +--------v---------+
                    |    GENERATE      |  4 modes: basic, contrarian,
                    |  (Claude or      |  combinatoric, crossover
                    |   auto-scan)     |  Input-tuple dedup prevents waste
                    +--------+---------+
                             |
                    +--------v---------+
                    |     SCORE        |  Market timing: 0.0-1.0
                    |                  |  Competition:   0.0-1.0
                    |                  |  MVP complexity: 0.0-1.0
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
    +---------v----------+       +----------v---------+
    |    SYNTHESIZE      |       |     COMPARE        |
    | Cross-category     |       | Keyword overlap vs  |
    | mega-projects with |       | existing org repos  |
    | phased MVPs        |       | (duplicate/enhance/ |
    +--------+-----------+       |  build new?)        |
              |                  +----------+----------+
              +-------------+---------------+
                            |
                   +--------v---------+
                   |    DASHBOARD     |  Browse, search, filter
                   |  Approve/Reject  |  Compare to existing repos
                   +--------+---------+
                            |
                   +--------v---------+
                   |    SCAFFOLD      |  GitHub repo + CI/CD
                   |                  |  Tests + README + Issues
                   |                  |  Language-appropriate structure
                   +------------------+
```

### What each step actually does

| Step | Input | Output | Intelligence |
|------|-------|--------|-------------|
| **Generate** | Seed concepts + domains | Raw project ideas | Claude Sonnet or local auto-scan engine crosses concepts with domains using 4 generation directions. Contrarian mode specifically challenges assumptions. |
| **Score** | Raw idea | Feasibility score (0.0-1.0) | Evaluates market timing (is the world ready?), competition landscape (who else is doing this?), and MVP complexity (can we ship in weeks, not months?). |
| **Synthesize** | Scored ideas across categories | Super Ideas (mega-projects) | Clusters related ideas from different categories, finds synergies, and produces ambitious multi-phase projects that no single category would generate. |
| **Compare** | Idea + existing repos | Overlap analysis | Keyword extraction + Jaccard similarity against your org's existing repos. Flags duplicates, suggests enhancements, or greenights net-new projects. |
| **Scaffold** | Approved idea | GitHub repository | Creates repo with language-appropriate project structure, CI/CD pipeline, test scaffolding, initial issues, README, and labels. Ready to clone and code. |

---

## Features

| Feature | What It Does | Why It Matters |
|---------|-------------|----------------|
| **4-Direction Generation** | Basic, contrarian, combinatoric, and crossover idea modes | Contrarian mode alone surfaces ideas your team would never brainstorm |
| **Feasibility Scoring** | Market timing + competition + MVP complexity (0.0-1.0) | Kill bad ideas early, fund good ones with data |
| **Super Ideas Engine** | Cross-category synthesis with LLM-reasoned cluster naming | Sonnet names the unifying capability gap instead of slot-filling templates |
| **5-Phase Idea Wizard** | Sonnet asks intelligent follow-ups across Discover → Differentiate → Audience → Constraints → Synthesize | Drop a fragment, get a structured project — wizard probes deeper instead of one-shotting it |
| **Text & URL Ingest** | Paste a fragment OR a URL; Sonnet expands either into a full Idea | Capture half-thoughts in the dashboard the moment you have them |
| **Industry Drill-Down** | Inferred verticals (government, healthcare, education, finance, retail, hospitality, manufacturing, energy, telco) | Slice 4000+ ideas by where they fit — drop the verticals you don't care about |
| **Repo Comparison** | Keyword overlap against existing GitHub repos | Never accidentally duplicate effort or miss an enhancement opportunity |
| **Auto-Scaffolding** | GitHub repo + CI + tests + issues + README in one click | From idea to clonable repo in under 60 seconds |
| **Content Dedup** | SHA-256 + tagline similarity + cluster signature; rejection-aware prompts | Run it 1000 times, never get the same idea twice. Saturation summary feeds back into the next prompt. |
| **External Feed Seeds** | NVD CVEs + arXiv cs.CR + IETF I-D RSS pulled into the prompt | Fresh signals from outside the corpus break the data-poverty loop |
| **Self-Improvement Engine** | Daily introspect cycle proposes patches to its own code | The codebase improves itself — every promoted SI idea ships as a real PR |
| **Web Dashboard** | Stats, explore, projects, think tank — with drill-down filters | Non-technical stakeholders can participate in the pipeline |
| **Autonomous Mode** | Cron-driven, no human intervention | Set it and forget it -- ideas accumulate while you sleep |
| **No-API-Key Path** | Static heuristics OR Claude Code CLI when `claude` is on PATH | Zero-cost or subscription-cost — never blocked on API key provisioning |
| **13 Categories** | Security, infra, compliance, automation, observability, privacy, and more | Configurable seeds — extend or replace per portfolio |

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/rayketcham-lab/project-forge.git
cd project-forge
pip install -e ".[dev,test]"

# Run the test suite (1000+ tests)
pytest tests/ -v

# Start the dashboard
forge-serve
# -> http://localhost:55443

# Generate ideas (LLM auto-detected: Anthropic API, Claude Code CLI, or static)
forge-generate
```

> [!TIP]
> The LLM backend resolver picks the best path automatically. Force a specific one with `FORGE_LLM_BACKEND=claude_code` if you want Claude Code CLI even when an API key is set, or `FORGE_LLM_BACKEND=static` to disable LLM calls entirely. Pick the model with `FORGE_LLM_MODEL=sonnet|opus|haiku` (default: sonnet — cost-effective for creative idea generation).

### One-liner for the impatient

```bash
git clone https://github.com/rayketcham-lab/project-forge.git && cd project-forge && pip install -e . && forge-serve
```

---

## Dashboard

The web dashboard at `http://localhost:55443` provides:

**Home** (`/`) -- Real-time stats: total ideas, category breakdown, average feasibility scores, top-rated ideas. Plus "Browse by Industry" and "Browse by Category" cards for one-click drill-down.

**Add Idea** tab (on the Home page) -- Three ways to capture an idea:
- **From URL** — paste an article/RFC/blog and Sonnet generates a project from it
- **Build Idea from Text — Quick** — paste a fragment, get a structured Idea in one shot
- **Idea Builder Wizard — 5 Phases** — the deeper path: Sonnet asks intelligent follow-ups across Discover → Differentiate → Audience → Constraints → Synthesize. Every field of the final draft is editable before save.

**Explore** (`/explore`) -- Browse all ideas with:
- Full-text search across titles and descriptions
- Industry vertical chip row (government / healthcare / education / finance / retail / hospitality / manufacturing / energy / telco)
- Tech category chip row (13 categories)
- Filter by status (pending / approved / rejected / scaffolded)
- Both axes preserve each other's selection in links — drill down "Government × Crypto-Infrastructure" by clicking once on each axis.
- Pagination (12 ideas per page)

**Think Tank** (`/thinktank`) -- Self-improvement pipeline:
- Engine activity heartbeat: SI proposals accepted/filtered in 24h, last proposal timestamp, recent activity feed
- Forge Lab: AI-proposed code patches (Decompose X, Add tests for X) — promote to GitHub issue
- Roadmap: open + closed self-improvement issues from GitHub

**Idea Detail** (`/ideas/{id}`) -- Deep view of any idea:
- Full description, category, score breakdown
- Related ideas from the same generation run
- One-click "Compare to Repo" -- select any org repo and see keyword overlap
- Approve / Reject / Scaffold buttons

**Scaffold Flow** -- When you click "Scaffold":
1. Choose GitHub org and visibility (public/private)
2. Project Forge creates the repo with:
   - Language-appropriate directory structure
   - CI/CD pipeline (GitHub Actions)
   - Test scaffolding with example tests
   - README with project description
   - 3-5 starter issues based on the idea's MVP plan
   - Labels and milestones
3. You get a link to the new repo. Clone and start building.

---

## API Reference

### Pages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard with stats, top ideas, category breakdown |
| `GET` | `/explore` | Browse, filter, search, paginate all ideas |
| `GET` | `/ideas/{id}` | Idea detail with compare-to-repo and approval controls |

### Actions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ideas/{id}/approve` | Move idea to approved status |
| `POST` | `/ideas/{id}/reject` | Move idea to rejected status |
| `POST` | `/ideas/{id}/scaffold` | Scaffold idea to GitHub repo |

### REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/repos` | List org repos (for compare dropdown) |
| `POST` | `/api/ideas/{id}/compare` | Compare idea keywords against a repo |
| `GET` | `/api/stats` | JSON stats: counts, averages, category breakdown |
| `GET` | `/api/search?q=` | Full-text search across all ideas |
| `GET` | `/health` | Health check (returns `{"status": "ok"}`) |

### Example: Compare an idea to a repo

```bash
curl -X POST http://localhost:55443/api/ideas/42/compare \
  -H "Content-Type: application/json" \
  -d '{"repo": "rayketcham-lab/PKI-Client"}'
```

```json
{
  "idea_id": 42,
  "repo": "rayketcham-lab/PKI-Client",
  "overlap_score": 0.34,
  "shared_keywords": ["certificate", "x509", "pki", "validation"],
  "recommendation": "enhance",
  "rationale": "Significant overlap in PKI domain. Consider extending PKI-Client rather than building a new tool."
}
```

### Example: Search ideas

```bash
curl "http://localhost:55443/api/search?q=post-quantum+migration"
```

### Example: Get stats

```bash
curl http://localhost:55443/api/stats
```

```json
{
  "total_ideas": 247,
  "by_status": {"pending": 189, "approved": 41, "rejected": 12, "scaffolded": 5},
  "by_category": {"pqc-cryptography": 38, "security-tool": 34, "...": "..."},
  "avg_feasibility": 0.62,
  "top_scored": [{"id": 42, "title": "PQC Migration Validator", "score": 0.94}]
}
```

---

## Generation Engine Deep Dive

### Four Generation Directions

The engine doesn't just brainstorm -- it systematically explores the idea space:

| Direction | Strategy | Example |
|-----------|----------|---------|
| **Basic** | Direct application of seed concept to domain | "Apply certificate transparency to IoT device firmware" |
| **Contrarian** | Challenge conventional wisdom in the domain | "What if CRLs are better than OCSP for edge networks?" |
| **Combinatoric** | Combine two seeds from the same category | "Merge hardware attestation + key escrow into a unified root of trust" |
| **Crossover** | Cross seeds from different categories | "Apply compliance automation patterns to vulnerability disclosure timelines" |

### Input-Tuple Tracking

Every `(seed, domain, direction)` combination is stored. On subsequent runs, the engine skips combinations it's already explored. This means:

- **Run 1**: Generates ideas for all unexplored combinations
- **Run 2**: Only generates for NEW seeds, domains, or directions
- **Run 100**: Still producing novel ideas (as you add seeds and domains)

No wasted API calls. No duplicate ideas. Scale without cost explosion.

### Content Deduplication

Even with tuple tracking, different inputs can produce similar ideas. The dedup engine:

1. Extracts a content fingerprint (SHA-256 of normalized title + description)
2. Checks against all existing fingerprints
3. Rejects duplicates before they hit the database

### Auto-Scan Mode (No API Key)

When `FORGE_ANTHROPIC_API_KEY` is not set, the engine falls back to local auto-scan:

- Crosses seed concepts with domains using template-based generation
- Produces structured ideas with titles, descriptions, and category tags
- Lower creativity than Claude, but zero cost and fully offline
- Great for testing, development, or budget-conscious usage

---

## Categories

Project Forge ships with 13 categories, each with curated seed concepts. The default seed lists lean toward security and infrastructure (the use case the project was originally built for), but they're plain Python lists in `engine/categories.py` — extend, replace, or rebalance them for any portfolio:

| Category | Focus | Example Seeds |
|----------|-------|---------------|
| `pqc-cryptography` | Post-quantum algorithms, migration tooling | ML-KEM, hybrid key exchange, PQ readiness scanners |
| `nist-standards` | FIPS validation, SP 800 series compliance | FIPS 140-3 tooling, NIST CSF mappers, SP 800-53 automation |
| `rfc-security` | IETF RFC implementation for security protocols | TLS 1.3 extensions, ACME protocol, MLS messaging |
| `crypto-infrastructure` | PKI, certificate management, key lifecycle | CT log monitors, OCSP stapling, HSM abstraction layers |
| `security-tool` | Offensive and defensive security toolchain gaps | Fuzzing harnesses, SBOM generators, secret scanners |
| `vulnerability-research` | Novel vulnerability discovery and analysis | Protocol fuzzers, binary diffing, CVE correlation engines |
| `privacy` | Privacy-preserving technologies (PETs) | Differential privacy, homomorphic encryption, MPC tooling |
| `compliance` | Regulatory compliance automation | SOC2 evidence collectors, FedRAMP automation, CMMC mappers |
| `observability` | Security monitoring, logging, anomaly detection | SIEM integration, log correlation, behavioral analytics |
| `devops-tooling` | Developer experience and infrastructure security | Policy-as-code, secrets management, supply chain verification |
| `automation` | Workflow and process automation | Incident response playbooks, change management, approval flows |
| `market-gap` | Products and services missing from the market | Competitive analysis, gap identification, market sizing |

> [!TIP]
> **Adding custom categories:** Edit `src/project_forge/engine/categories.py`. Each category is a dataclass with a name, description, and list of seed concepts. Add yours and the engine picks it up on the next run.

---

## Architecture

```
src/project_forge/
  config.py                  # Pydantic-settings: env vars, defaults, validation
  models.py                  # Pydantic models: Idea, ScaffoldSpec, GenerationRun, SuperIdea
  engine/
    categories.py            # 12 category definitions with seed concepts
    generator.py             # Claude-powered idea generation (Anthropic SDK)
    scorer.py                # Feasibility scoring: market timing, competition, MVP complexity
    compare.py               # Idea-to-repo comparison: keyword extraction + Jaccard similarity
    super_ideas.py           # Cross-category synthesis: clustering + theme templates + vision
    prompts.py               # Prompt templates for all 4 generation directions
  storage/
    db.py                    # SQLite: WAL mode, content fingerprinting, input-tuple tracking
  web/
    app.py                   # FastAPI application factory
    routes.py                # Dashboard pages + REST API endpoints (12 routes)
    templates/               # Jinja2 HTML templates (dashboard, explore, detail)
    static/                  # CSS + JavaScript (dark theme, search, filtering)
  scaffold/
    builder.py               # Project structure generation (language-aware)
    github.py                # GitHub CLI integration: repo create, issues, labels, milestones
    templates/               # Jinja2 scaffolding templates (README, CI, tests per language)
  cron/
    runner.py                # Entry point for autonomous generation (forge-generate CLI)
    scheduler.py             # Full cycle orchestration: generate -> score -> synthesize -> store
    auto_scan.py             # Local generation engine (no API key required)
```

### Data Flow

```
User/Cron
    |
    v
runner.py --> scheduler.py --> generator.py --> Claude API (or auto_scan.py)
                                    |
                                    v
                               scorer.py --> db.py (SQLite + WAL + dedup)
                                    |
                                    v
                            super_ideas.py --> db.py
                                    |
                                    v
                              web/routes.py <-- Browser
                                    |
                                    v
                            scaffold/builder.py --> github.py --> GitHub API
```

### Data Models

```python
class Idea(BaseModel):
    id: int
    title: str
    description: str
    category: Category          # One of 12 categories
    status: Status              # pending | approved | rejected | scaffolded
    feasibility_score: float    # 0.0 - 1.0
    market_timing: float        # 0.0 - 1.0
    competition: float          # 0.0 - 1.0
    mvp_complexity: float       # 0.0 - 1.0
    content_hash: str           # SHA-256 fingerprint for dedup
    generation_direction: str   # basic | contrarian | combinatoric | crossover
    created_at: datetime

class SuperIdea(BaseModel):
    id: int
    title: str
    vision: str                 # Multi-paragraph vision statement
    component_ideas: list[int]  # IDs of constituent ideas
    categories_spanned: list[Category]
    mvp_phases: list[str]       # Phased build plan
```

---

## Deployment

### Development

```bash
pip install -e ".[dev,test]"
forge-serve  # http://localhost:55443
```

### Production with systemd

```ini
# /etc/systemd/system/project-forge.service
[Unit]
Description=Project Forge Dashboard
After=network.target

[Service]
Type=simple
User=forge
WorkingDirectory=/opt/project-forge
Environment=FORGE_DB_PATH=/var/lib/forge/forge.db
Environment=FORGE_ANTHROPIC_API_KEY=sk-ant-xxx
ExecStart=/opt/project-forge/.venv/bin/forge-serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now project-forge
```

### Autonomous generation (cron)

```bash
# /etc/cron.d/project-forge
# Generate new ideas every 6 hours
0 */6 * * * forge /opt/project-forge/.venv/bin/forge-generate >> /var/log/forge-generate.log 2>&1
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 55443
CMD ["forge-serve"]
```

```bash
docker build -t project-forge .
docker run -p 55443:55443 -e FORGE_ANTHROPIC_API_KEY=sk-ant-xxx project-forge
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FORGE_ANTHROPIC_API_KEY` | -- | Claude API key. Optional -- auto-scan works without it |
| `FORGE_DB_PATH` | `data/forge.db` | SQLite database file path |
| `FORGE_PORT` | `55443` | Web dashboard port |
| `FORGE_GITHUB_OWNER` | `rayketcham-lab` | Default GitHub org for scaffolding |

### Example `.env` file

```bash
FORGE_ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FORGE_DB_PATH=/var/lib/forge/forge.db
FORGE_PORT=55443
FORGE_GITHUB_OWNER=rayketcham-lab
```

---

## Example Output

### Generated Idea

```json
{
  "id": 42,
  "title": "PQC Migration Validator",
  "description": "A CLI tool that scans codebases and infrastructure for cryptographic dependencies vulnerable to quantum attack. Maps each finding to a NIST-recommended post-quantum replacement, generates a prioritized migration plan, and validates the migration with automated tests.",
  "category": "pqc-cryptography",
  "generation_direction": "basic",
  "feasibility_score": 0.94,
  "market_timing": 0.98,
  "competition": 0.85,
  "mvp_complexity": 0.92,
  "status": "approved"
}
```

### Super Idea (Cross-Domain Synthesis)

```json
{
  "id": 7,
  "title": "Quantum-Ready Compliance Platform",
  "vision": "A unified platform that combines post-quantum cryptography migration with compliance automation. Organizations preparing for CNSA 2.0 deadlines need both crypto agility AND audit evidence. This platform generates migration plans, executes them, and simultaneously produces FedRAMP/CMMC evidence packages proving quantum readiness.",
  "categories_spanned": ["pqc-cryptography", "compliance", "crypto-infrastructure"],
  "mvp_phases": [
    "Phase 1: PQC dependency scanner + NIST algorithm mapper",
    "Phase 2: Automated migration with rollback (hybrid mode)",
    "Phase 3: Compliance evidence generator (FedRAMP, CMMC, SOC2)",
    "Phase 4: Continuous monitoring + drift detection"
  ],
  "component_ideas": [42, 67, 103]
}
```

### Comparison Result

```json
{
  "idea_id": 42,
  "repo": "rayketcham-lab/PKI-Client",
  "overlap_score": 0.34,
  "shared_keywords": ["certificate", "x509", "pki", "validation"],
  "recommendation": "enhance",
  "rationale": "34% keyword overlap in PKI domain. The existing PKI-Client handles certificate operations but lacks PQC awareness. Recommend adding PQC migration features to PKI-Client rather than building a separate tool."
}
```

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Language** | Python 3.12 | StrEnum, match/case, modern typing -- the right tool for an AI-driven pipeline |
| **Web** | FastAPI + Jinja2 | Async-native, automatic OpenAPI docs, server-rendered templates |
| **Database** | SQLite (WAL mode) | Zero-config, embedded, perfect for single-instance deployment |
| **AI** | Anthropic SDK (Claude Sonnet) | Best-in-class reasoning for idea generation and scoring |
| **Scaffolding** | GitHub CLI (`gh`) | Native repo creation, issue management, label setup |
| **Lint** | Ruff (E, F, W, I, S, B, UP) | Fast, comprehensive, replaces 6 tools |
| **Tests** | pytest + pytest-asyncio | 175+ tests, async-native, clean fixtures |

---

## Contributing

### Adding a new category

```python
# src/project_forge/engine/categories.py
Category(
    name="supply-chain",
    description="Software supply chain security and integrity",
    seeds=[
        "SBOM generation and validation",
        "Build provenance verification (SLSA)",
        "Dependency confusion detection",
        "Package registry security",
    ],
)
```

The engine picks it up automatically on the next generation run.

### Adding a generation direction

Edit `src/project_forge/engine/prompts.py` -- each direction is a prompt template that receives `(seed, domain, category)` and returns structured idea JSON.

### Running the full test suite

```bash
# All tests
pytest tests/ -v

# Just the engine tests
pytest tests/test_engine/ -v

# Just the web tests
pytest tests/test_web/ -v

# With coverage
pytest tests/ --cov=project_forge --cov-report=term-missing
```

### Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

---

## License

MIT -- see [pyproject.toml](pyproject.toml) for details.
