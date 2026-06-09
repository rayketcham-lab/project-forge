# Project Forge — v0.16 Expansion Menu

This is a menu of vetted next-step options for Project Forge. Each item has
**What** (concrete thing), **Why** (rationale), and **How** (rough implementation).

**Picking system:** reply to me with any combination of codes (e.g. `A2 A8 B1 B11 C1 D1 D3`) and I'll build them. No cap — Pro Max isn't metered. The ★ marks each agent's strongest pick.

Sections:
- **A. New Labs** — themed idea-generation surfaces (like Money Bots and Claude Lab)
- **B. Better Churn** — improve the quality/variety of LLM-generated ideas
- **C. Architecture Pivots** — change what the system fundamentally IS
- **D. Bonus Pile** — small UX/quality wins to mix freely

---

## A. New Labs — themed idea-generation surfaces

Each lab adds: a new nav tab, a dedicated page (`/your-lab`), 2-4 new IdeaCategory enum values, 12 personas, 15-20 seed concepts, and a new score axis. Same shape as `/money-bots` and `/claude-lab`. Implementation cost per lab: ~M (1-2 days each).

### Business lens — money is the goal

**A1. Vertical SaaS Lab** — `/vertical-saas-lab`
- **What**: Niche industries with 2003-era incumbents (dental pre-auth, freight brokers, K-12 bus routing).
- **Why**: Boring industries have desperate buyers and zero competition. Vertical SaaS retention is best-in-class.
- **How**: 4 IdeaCategory values per vertical, score axis `niche_moat_score`, mint-green nav dot. Personas: dental office manager, freight broker, school transport director.

**A2. ★ Infra Margin Lab** — `/infra-margin-lab`
- **What**: Wedge plays skimming margins off Stripe, AWS, OpenAI, Twilio. Routing, caching, fallback, optimization layers.
- **Why**: Customer "no" cost is quantifiable on day one. LTV/CAC is best-in-class. Integration depth becomes the moat.
- **How**: Categories like `payment-routing`, `llm-cost-arbitrage`. Score axis `wedge_score`. Sample idea: CardRail (Stripe API proxy that routes to cheaper PSPs). Amber nav dot.

**A3. Acquisition Lab** — `/acquisition-lab`
- **What**: Buy micro-SaaS at 2-4x ARR, plug AI in, exit at 6-10x.
- **Why**: Capital-efficient path that skips zero-to-one. Acquihire arbitrage exists at the small end.
- **How**: Score axis `flip_score` (multiple expansion potential × AI integration leverage). Personas: solo SaaS owner ready to exit, search-fund operator.

**A4. Compliance Cash-Flow Lab** — `/compliance-lab`
- **What**: Regulated workflows (SOC2, GDPR DSAR, state MTL, CMMC) where "must comply" makes purchase non-optional.
- **Why**: Highest contract values, lowest churn. Regulations only get stricter.
- **How**: Score axis `mandate_score` (regulatory force × audit frequency × penalty size). Personas: GRC analyst, CISO, audit-prep manager.

### Public-good lens — impact is the goal

**A5. Civic Records Lab** — `/civic-records-lab`
- **What**: FOIA deadline trackers, redaction auditors, council-minutes OCR.
- **Why**: Public records exist but are hostile to use. Small tools have outsized journalist/civic pickup.
- **How**: Score axis `transparency × legal_durability × journalist_pickup`. Anchor idea: FoiaDeadlineHawk — deterministic statutory-clock reminders, no LLM required.

**A6. Climate Adaptation Lab** — `/climate-adaptation-lab`
- **What**: Tools for surviving heat waves, floods, smoke *this season* — not 2050.
- **Why**: Mitigation gets the funding; adaptation gets neglected. People need help now.
- **How**: Score `adaptation × equity × tractability`. Anchor: WellnessRoute — offline-first vulnerable-address routing for community health workers in Phoenix-style heat emergencies.

**A7. Public Defender Tooling Lab** — `/justice-tools-lab`
- **What**: Closes the DA-vs-PD resource asymmetry. Discovery indexers, motion-template tooling.
- **Why**: Public defenders are 100x under-resourced vs DAs in most jurisdictions. Software leverage matters.
- **How**: Score `asymmetry × caseload_relief × defendant_outcome`. Higher Haiku floor (0.60+) because court-filing risk is real. Anchor: DiscoveryIndex — on-prem body-cam/PDF indexer, no cloud, no generated claims.

**A8. ★ Health Equity Frontline Lab** — `/health-equity-lab`
- **What**: Workflow tools for ER nurses, community health workers, doulas, school nurses — minutes-saved is the metric.
- **Why**: Most concrete personas of any public-good lab. AI's role is honestly scoped. Value is measurable in shift-minutes — not a dashboard nobody opens.
- **How**: Score `frontline_minutes_saved × access × interoperability × clinician_trust`. Anchors: HandoffSixty (60-second shift change), AVSPlain (on-prem multilingual after-visit rewriter), CrisisBedNow (real-time 988 bed registry).

### Contrarian lens — niches mainstream engines won't suggest

**A9. Solder & Soul Lab** — `/solder-soul-lab`
- **What**: Embedded firmware, bench instruments, repair-economy tooling for hardware hobbyists.
- **Why**: Hardware hobbyists pay $200+/yr without flinching. Subscription tolerance is high; competition is nil.
- **How**: Score `solder_passion_score` (depth of hardware involvement × subscription willingness). Personas: amateur radio operator, Heathkit restorer, mechanical-keyboard collector.

**A10. Time Capsule Lab** — `/time-capsule-lab`
- **What**: Protocol revival, emulation tooling, archive rescue for the BBS / Gemini / Hypercard / zine underground.
- **Why**: Lost-tech communities are surprisingly large and surprisingly buying. Nostalgia is a real funding source.
- **How**: Score `revival_viability_score` (community size × commercial viability of revival). CRT-phosphor purple accent. Personas: BBS sysop revival operator, Gemini protocol enthusiast.

**A11. Field & Stream Lab** — `/field-stream-lab`
- **What**: Offline-first, mittens-friendly, legal-stakes tools for hunters, foragers, SOTA hams, SAR techs, beekeepers.
- **Why**: Outdoor users have offline-first reality. Apps that assume connectivity die. Legal-stakes users (hunting tags) pay for accuracy.
- **How**: Score `offline_first_pain_index`. Tech stack defaults: rust + sqlite + tauri (offline-first reality). Personas: SAR tech, fly-fishing guide.

**A12. ★ Hands & Trades Lab** — `/hands-trades-lab`
- **What**: Field-service, code-compliance, truck-inventory software for 1-15-truck owner-operators ServiceTitan ignores.
- **Why**: The bottom 95% of trades shops still run on paper. Every working tool pays for itself by Friday. A master plumber will pay $200/month for the right thing without a sales call.
- **How**: Score `truck_test_score` ("would this work in an F-250 cab at 7:14 AM?"). Work-glove tan accent. Personas: 4-truck plumber, master electrician, HVAC dispatcher.

---

## B. Better Churn — quality/variety of LLM-generated ideas

### Prompt-engineering (changes INSIDE the LLM call)

**B1. ★ Critique-and-Rebuild**
- **What**: Single Opus call with three XML-tagged sections: `<draft>` (initial pitch), `<critique>` (skeptical reviewer attacks it), `<rebuild>` (final version that addresses the critique).
- **Why**: Makes the model argue with itself. Largest single-call quality jump available. We only parse `<rebuild>` but log all three for inspection.
- **How**: ~2 hours. Update `_build_prompt()` in `engine/llm_generator.py` to add the critique scaffolding. Update the JSON parser to look for the final `<rebuild>` block. No new modules.

**B2. ★ Named-Character Personas**
- **What**: Replace generic personas ("indie hacker chasing $5k MRR") with rich characters: name, business, dollar figures, tools paid for, tried-and-failed list. Example: "Marcus Vela @ HOTKEY HERESY, 2k subs, $39 Beehiiv bill, tried SparkLoop and bounced because of latency."
- **Why**: Specificity in the prompt forces specificity in the output. Cures the "every idea feels samey" complaint at the source.
- **How**: ~1 day. Rewrite the ~80 entries in `PERSONAS_BY_CATEGORY` (in `engine/llm_generator.py`). No schema changes. Pure content work.

**B3. Constraint Injection**
- **What**: Pool of ~25 hard constraints ("must work offline", "< 200 LOC", "CLI before UI", "ship in 48 hours") sampled 1-2 per call with mandatory trade-off explanation.
- **Why**: Forces concrete trade-offs into descriptions. Cheapest of all (~30 min). Generates surprising design choices.
- **How**: Add a `_CONSTRAINTS` list + `random.sample(2)` in `_build_prompt`. New section in the prompt header.

**B4. Failure-Mode-First**
- **What**: Instead of "pitch the idea", ask "first describe 3 ways this kind of product fails. Then design around the most likely failure."
- **Why**: Filters out ideas that ignore obvious failure modes. Output reads as battle-tested rather than rosy.
- **How**: New mode prompt OR add to existing modes. Half-day.

**B5. Negative-Space Prompting**
- **What**: Frame around what DOESN'T exist: "What's missing in the {category} space that everyone complains about but nobody has built?"
- **Why**: Forces "gap-finding" cognition instead of "build-on-existing-trend" cognition.
- **How**: New generation mode `negative-space`. Add to `GENERATION_MODES` list and `_MODE_PROMPTS` dict.

**B6. Persona Pair-Up**
- **What**: Two personas in one prompt arguing over the idea. "Marcus (creator) wants X but Olivia (CFO) hates it because Y. Pitch the version that satisfies both."
- **Why**: Forces multi-stakeholder thinking. Output has more defensible business case.
- **How**: Pick two personas (random pair) in `generate_idea_llm()`. Update prompt template.

**B7. Anti-Trope Library**
- **What**: Curated list of tired AI-tropes ("just another GPT wrapper", "Notion for X", "Slack but for Y") explicitly named in the prompt as things to avoid.
- **Why**: The avoid-list catches duplicates of YOUR recent ideas; the anti-trope list catches duplicates of the INDUSTRY's tired patterns.
- **How**: New `_ANTI_TROPES` constant + injection into prompt. Maintain the list quarterly.

**B8. Time-Pressure Framing**
- **What**: "It's 11 PM on a Tuesday. You have until 6 AM to ship this. What's the MVP?"
- **Why**: Pressure framings produce more focused ideas. Cuts scope creep at the source.
- **How**: Add as a generation mode OR as a constraint (see B3). Half-day.

**B9. Adversarial-Failure Seeding**
- **What**: Pull from a curated list of "ambitious failures" — projects that died for a specific reason. Prompt: "Here's why X failed. Design the version that wouldn't."
- **Why**: Learning from real failures > theoretical reasoning. Forces the model to confront concrete cause-of-death.
- **How**: Build a `data/failed_projects.jsonl` (50-100 entries: name, what they tried, why they died). Pick one per call. Inject as inspiration material.

**B10. Source-of-Novelty Injection**
- **What**: Pull from external trend data (HackerNews top, Reddit hot in relevant subs, arXiv papers from last week) and inject as fresh material.
- **Why**: Current Churn has no fresh input. After 1000 generations on the same seed pool, output goes stale.
- **How**: New `cron/trend_fetch_runner.py` cadence (daily). Cache to `data/trends/`. Inject 3-5 lines per Churn prompt.

### System / loop mechanics (changes OUTSIDE the LLM call)

**B11. ★ Adversarial-Similarity Retrieval**
- **What**: Replace the chronological 30-name avoid-list with a similarity-ranked one — first ask Opus for a one-line "sketch" of what it's about to produce, then retrieve the 30 most-similar existing ideas as the avoid-list.
- **Why**: Current avoid-list is dated, not adversarial. Predicted 40-60% reduction in dedup-rejected Churns.
- **How**: ~M. New helper that does a quick "sketch" call (~1s), then a similarity rank using existing dedup primitives. No new dependencies. Cost: +1 short call per Churn.

**B12. ★ Good-Example Injection**
- **What**: Approved high-score ideas become exemplars injected into future prompts: "this is the energy."
- **Why**: Compounds with every human approval. Single-shot, zero latency cost, mode-collapse mitigated by age-decay + 10% no-exemplar control branch.
- **How**: New helper in `engine/llm_generator.py` that pulls 2-3 high-fundability OR high-ambition approved ideas (decayed by age). Inject as JSON in the prompt with explicit "energy match" instruction.

**B13. Mode-Persona Bandit**
- **What**: Thompson sampling over `(category, mode, persona)` triples replaces uniform rotation.
- **Why**: Pure infrastructure win — automatically bias toward what humans actually approve. Needs ~200 churns of cold-start data; deploy alongside B11/B12.
- **How**: New `engine/bandit.py` module. Track per-triple accept-rate in `data/bandit.db`. Picker call replaces `pick_least_used_mode` / persona-random.

**B14. 3-Shot Ensemble + Self-Rate**
- **What**: Generate 3 candidate ideas in parallel (3 Opus calls), have Opus rate them itself, return the highest-rated.
- **Why**: ~3× latency but consistently better output. Best for the explicit "give me your best shot" button, not default.
- **How**: New endpoint `/api/churn/best`. Reuses generate_idea_llm 3× then a 4th Opus call for self-rate.

**B15. Outcome-Feedback Loop**
- **What**: When a human approves or promotes an idea, write a "good example" row; future generations include 2-3 good examples as "produce something with this energy."
- **Why**: Closes the loop from human signal back to generation. Compounding.
- **How**: Subset of B12 — explicit "approved → exemplar" surface vs. B12 which is "high-score → exemplar". Could merge.

**B16. Burst Tournament (opt-in)**
- **What**: A "Burst" variant of the Churn button — fires 10 ideas in parallel, runs a tournament, returns the winner. Opt-in only.
- **Why**: When you want the BEST idea (not just a fresh one). Costs 10x but Pro Max isn't metered.
- **How**: New `/api/churn/burst` endpoint. Parallel `asyncio.gather` of 10 generates. Tournament logic = pairwise Opus call.

**B17. Multi-Category Bridge**
- **What**: Churn picks TWO categories and asks Opus to bridge them. ("Health Equity Lab × Claude Skills — what does a sub-agent for ER nurses look like?")
- **Why**: Forces synthesis. Often the most surprising ideas come from category collisions.
- **How**: Pick two categories, run a single prompt with explicit bridge instruction. Mode + artifact still apply.

**B18. Trend-Feed Cadence**
- **What**: New daily cadence that pulls HackerNews top-N + Reddit r/{relevant subs} + Anthropic announcements weekly into `data/trends/` cache. Then B10 (or B12 variant) injects them.
- **Why**: Auto-refresh the source of novelty. No manual maintenance.
- **How**: New `cron/trend_fetch_runner.py`. Wire into `lifespan_scheduler.py` as a 24h cadence.

---

## C. Architecture Pivots — change what the system fundamentally IS

Pick at most one of these. They reshape rather than extend.

**C1. ★ MCP Server**
- **What**: Expose the engine as a Model Context Protocol server. `forge.churn(lab=…)` becomes a tool any Claude session can call directly.
- **Why**: Highest leverage-per-effort. Pure wrapper layer, zero schema risk. Makes the engine composable inside Claude Code where you already work. Strategic foundation for everything else (CLI, builder agent, discovery layer all become alternate clients of the same surface).
- **How**: ~S (1 week). New `src/project_forge/mcp/server.py` using the MCP Python SDK. Tools: `churn`, `top_money_bots`, `top_claude_lab`, `promote`, `reject`. Stdio transport for local; SSE for remote.

**C2. ★ Long-Running Builder Agent**
- **What**: The engine doesn't just produce static ideas; it actively *builds* the top idea each week. Auto-scaffold → starter code → first PR. The output isn't ideas, it's repos.
- **Why**: Closes the loop end-to-end. The only pivot with real long-tail money story. Produces actual portfolio output, not more rows in `ideas`.
- **How**: ~L (a quarter). New `build_runs` table + sandboxing + build supervisor cadence. Reuses existing scaffold pipeline. Real architectural cost.

**C3. GitHub App**
- **What**: Install Project Forge on a repo. Engine watches issues + commits + discussions, generates ideas for things the repo could ship. Pushes to a special "ideas" branch.
- **Why**: Context-aware ideation grounded in real codebase. Most valuable when the repo is active.
- **How**: ~M (1 month). New GH App registration. Webhook handler + repo-aware prompting + branch-based output.

**C4. Public Stream**
- **What**: Engine broadcasts top ideas to an RSS feed / newsletter / Twitter / blog. The corpus becomes a public artifact.
- **Why**: Engagement loop. Public corpus drives feedback. Could become a content/audience play of its own.
- **How**: ~S (1 week). New `cron/publish_runner.py` cadence. RSS generator + email digest + (optional) social poster.

**C5. Discovery Layer**
- **What**: Pivot from "produce ideas" to "find existing under-the-radar projects on GitHub that match your interests." Recommends, doesn't generate.
- **Why**: Different value prop. Less risky than greenfield. Surface real existing projects.
- **How**: ~M. GitHub Search API + similarity ranking. Schema change: `discovered_repos` table parallel to `ideas`.

**C6. CLI-First**
- **What**: Strip the dashboard from the critical path. `forge churn`, `forge promote N`, `forge query "money bot"` work first; the dashboard is optional.
- **Why**: Some workflows are faster in the terminal. Headless deployment becomes possible.
- **How**: ~S. New `cli/main.py` using click. Wraps the same engine functions the routes use.

---

## D. Bonus Pile — small UX/quality wins, mix freely

**D1. ★ Reverse-Chronological Sort Toggle**
- **What**: One query param (`?sort=newest`) on /money-bots, /claude-lab, /projects.
- **Why**: Daily visibility. Fixes the "where are today's ideas?" frustration.
- **How**: XS (30 min). Add `?sort=newest` parsing in routes; flip ORDER BY clause.

**D2. ★ Now-Playing Cadence Pill**
- **What**: Small status pill in the footer showing which cadence ran most recently and when the next is due.
- **Why**: Turns the invisible scheduler into a glanceable green-dot pill. Catches silent scheduler failures on every page load.
- **How**: S (a couple hours). New `/api/scheduler-status` endpoint. Footer template addition. JS polls every 30s.

**D3. ★ Keyboard Shortcuts**
- **What**: `j` / `k` to navigate cards, `c` to churn, `r` to reject, `m` to promote, `?` to show help.
- **Why**: 3-5x speedup on the most repetitive workflow (triage on /explore and /money-bots).
- **How**: S. Frontend-only addition to `app.js`. No schema risk.

**D4. Markdown Render of Idea Descriptions**
- **What**: Description / market / mvp fields render as markdown instead of plain text.
- **Why**: Currently the LLM produces markdown but it renders as raw text. Free quality win.
- **How**: S. Add a markdown lib (`marked` via CDN or server-side `markdown` in Jinja). Render in modal + detail page.

**D5. Dashboard Sparklines**
- **What**: Tiny 7-day trend chart next to each stat card (ideas/day, accepts/day, etc.).
- **Why**: Trend perception is faster than reading numbers. Catches drops/spikes at a glance.
- **How**: S. SVG-based sparklines (no charting library). New `/api/stats/timeseries` endpoint.

**D6. Idea Export**
- **What**: Markdown or JSON download of a single idea or a category's top-N.
- **Why**: Take ideas elsewhere — Notion, Obsidian, ChatGPT. Persistent backup.
- **How**: XS. New `/api/ideas/{id}/export?format=md` + `/api/money-bots/export?format=json`.

**D7. Compact Mode Toggle**
- **What**: Denser card grid setting for users who want to see more at once.
- **Why**: Currently each card takes ~250px. Power users want twice the density.
- **How**: XS. CSS class toggle + localStorage preference.

**D8. Pinned / Starred Ideas**
- **What**: Star an idea to keep it at the top of /explore regardless of date or score.
- **Why**: Reference ideas you keep coming back to. Mark a corpus subset for follow-up.
- **How**: S. Schema: `pinned_at TIMESTAMP`. New `/api/ideas/{id}/pin` toggle.

**D9. Quick-Filter Chips on Explore**
- **What**: Chips for "ambition > 0.7", "fundability > 0.8", "promoted", "this week" right above the grid.
- **Why**: Common filters become one click instead of URL editing.
- **How**: S. Add chip strip to `explore.html`. Wire to existing query params.

**D10. Telemetry Endpoint**
- **What**: `/api/telemetry` exposes recent cadence runs + latencies + reject reasons in JSON.
- **Why**: Debugging without SSH. Future grafana / dashboard wiring.
- **How**: S. New endpoint reading from existing logs / metrics already tracked in `db.query_times`.

**D11. Color-Blind Palette Option**
- **What**: Alternative palette that doesn't rely on red/green for status.
- **Why**: Accessibility. ~5% of users can't distinguish current red-vs-green status pills.
- **How**: S. CSS variable swap + toggle in user prefs.

**D12. Random-Jump Button**
- **What**: "Show me a random surprising idea I haven't seen" button.
- **Why**: Serendipity. Forces you out of the recent/top-score rut.
- **How**: XS. New `/api/ideas/random?weight=ambition` endpoint with weighted sampling.

---

## How to pick

Reply with any combination of codes. Examples:

- **Minimum build** (1 evening): `D1 D2 D3` — the bonus stars
- **Quality lift** (~1 day): `B1 B2 B11 B12` — the Churn stars
- **One new lab**: `A2` (Infra Margin) OR `A8` (Health Equity) OR `A12` (Hands & Trades)
- **Strategic swing** (1 week): `C1` (MCP Server)
- **Audacious package**: `A2 A8 A12 B1 B2 B11 B12 C1 D1 D2 D3` — three labs + four Churn upgrades + MCP server + three UX wins

No cap. Tell me what you want and I'll build it.

---

*Full deep-design files for each section live in this repo's history under the
parallel-agent fan-out commit. The labs each have full 12-persona / 15-seed /
3-baked-idea designs ready to drop into `engine/categories.py` and
`engine/llm_generator.py`.*
