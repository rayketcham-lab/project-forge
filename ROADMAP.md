# Project Forge — Roadmap v0.15+

**Status:** v0.14 shipped the money-flipper loop. Engine generates 17 categories × 5 modes × 10 personas via Haiku 4.5, scores fundability, auto-promotes the top money idea to a GitHub issue weekly. ~$2-3/mo. Scaffold pipeline exists but is not yet wired to auto-promote.

**North star for v0.15+:** *Close the loop from idea → shipped MVP → measured revenue, without losing the variety that just got won back.*

The proposals below are grouped by theme and ordered roughly by leverage. Each entry has: pitch, implementation sketch, effort (S/M/L), and why it matters.

---

## Theme 1 — Close the loop (idea → built → earning)

### 1. Auto-scaffold the weekly auto-promoted idea
**Pitch.** Right now the Monday auto-promote stops at a GitHub issue. The single biggest leverage move is making the engine *build* the top idea — scaffold a real repo, push a starter MVP, open a PR with smoke tests — so the owner reviews working code instead of plans.

**Sketch.**
- New cadence `auto_build` (Monday 02:00, after `auto_promote` at 01:00).
- New module `src/project_forge/build/auto_builder.py` that takes the promoted idea ID, calls existing `scaffold/` pipeline, then invokes Haiku via CLI to generate (a) `README.md`, (b) `pyproject.toml` or `package.json`, (c) one minimal working endpoint or CLI, (d) `tests/test_smoke.py`.
- Output goes to a `forge-mvp-{slug}` GitHub repo under the owner's account; PR opened with checklist.
- Store `build_run_id` on the idea row; surface `/builds` page in dashboard.

**Effort:** L (scaffold pipeline exists but wiring + safe generation prompts + repo creation flow is real work).
**Why it matters:** Eliminates the biggest "click stuff" gap. The owner stops grading PowerPoint and starts grading code. Speed-to-MVP collapses from weeks to one cadence cycle.

---

### 2. Revenue tracking via Stripe webhook
**Pitch.** The engine currently *guesses* fundability. It should *learn* fundability from outcomes. A Stripe webhook endpoint that lets shipped MVPs report revenue back closes the only feedback loop that matters for the money goal.

**Sketch.**
- New endpoint `POST /api/revenue/stripe-webhook` (verify signature with `FORGE_STRIPE_WEBHOOK_SECRET`).
- New table `revenue_events(idea_id, build_id, stripe_event_id, amount_cents, currency, created_at)`.
- New table `idea_outcomes(idea_id, status enum{shipped,abandoned,earning,dead}, mrr_cents, last_event_at)`.
- Built MVPs include a forge-tag header `X-Forge-Idea-Id: {id}` on their checkout metadata; webhook routes by that.
- Dashboard tile: "Lifetime revenue from Forge ideas: $X. Top earner: {idea}".

**Effort:** M.
**Why it matters:** Without revenue data, every other "make money" feature is theater. This is the ground truth that lets persona-weighting, fundability A/B, and category mix all become data-driven instead of vibes-driven.

---

### 3. Outcome-feedback loop → generation weights
**Pitch.** Once revenue + status data exists, feed it back into the generator so categories, personas, and modes that produced earning ideas get oversampled, and dead categories get cooled off.

**Sketch.**
- New module `engine/feedback_weights.py` reading `idea_outcomes` + `revenue_events`.
- Computes `category_weight`, `persona_weight`, `mode_weight` as decayed EMA of outcome scores (`shipped=1, earning=5x mrr_log, abandoned=-1, dead=-2`).
- `engine/scheduler` reads weights to bias sampling (but floors every weight at 0.05 to preserve variety — protects against the "drum the same drum" failure mode the owner just escaped).
- Cadence `feedback_refresh` recomputes weights nightly.

**Effort:** M.
**Why it matters:** This is what turns Forge from a content mill into a learning system. Variety preserved by the floor; money chased by the lift.

---

## Theme 2 — Better ideas before they ship

### 4. Edge-finder pre-pass
**Pitch.** Most generated ideas die because they're obvious. A pre-pass that asks Haiku "what's a fresh angle nobody's tried in {category} this quarter?" before the main generator runs would dramatically raise novelty floor.

**Sketch.**
- New module `engine/edge_finder.py`. Called by scheduler before each `expand` run.
- For each category being expanded: Haiku gets last 30 days of generated ideas in that category + current trend feed entries, returns 3-5 "fresh angle" seeds.
- Seeds are stamped into the generator system prompt as "avoid these tropes / try these angles".
- Cached for 24h per category (cost control).

**Effort:** S.
**Why it matters:** Quality lift at near-zero marginal cost. Direct attack on the "drumming the same drum" failure the owner just pivoted out of.

---

### 5. Persona-learning weights
**Pitch.** The 10 personas per category are currently uniform-weighted. Some personas almost certainly produce ideas the owner approves more often. Track approval/fundability/revenue per persona and weight accordingly.

**Sketch.**
- Persist `persona_name` on every generated idea (already partly there — extend if missing).
- Nightly job rolls up per-persona stats: approval rate, mean fundability, mean revenue per shipped idea.
- Sampler uses softmax(persona_score / T) with T tuned so the worst persona still gets ~3% draw.
- Surface `/personas` dashboard page showing leaderboard.

**Effort:** S.
**Why it matters:** Cheap quality lift, gives the owner observable "which voices matter" insight, complements outcome-feedback (#3) without depending on it.

---

### 6. A/B fundability scoring
**Pitch.** Two competing Haiku prompts score the same idea; the one whose past scores better predict actual revenue/approval wins more weight. Self-calibrating scorer.

**Sketch.**
- `engine/fundability/` grows two scorer variants `scorer_a.py`, `scorer_b.py` with different prompts (one quantitative TAM-focused, one qualitative "would-I-pay" focused).
- Each scored idea stores both scores plus a `judge_variant` field for the one used downstream.
- Weekly job correlates each scorer's historical predictions against `idea_outcomes`; updates `scorer_weight_a/b` used for routing the next week.
- Promote scorer C/D variants through PR — A/B framework reused.

**Effort:** M.
**Why it matters:** The auto-promote depends on fundability being calibrated. A/B is the only honest way to know it is.

---

### 7. Build-estimate per idea
**Pitch.** Given an idea, ask Haiku "how many hours to a paying MVP and what's the riskiest unknown?" Show as a sortable column. Lets the owner (and the auto-promote logic) trade off fundability vs. time-to-money.

**Sketch.**
- New scorer `engine/build_estimate.py`: returns `{hours_to_mvp, riskiest_unknown, stack_recommendation}`.
- Runs once per idea after fundability scoring; cached.
- Auto-promote ranking changes from `fundability_score` to `fundability_score / log(hours_to_mvp + 4)` — favors fast wins.
- Dashboard column + filter.

**Effort:** S.
**Why it matters:** Most "great" ideas die from time-cost mismatch. This is the missing axis on the money-bots board.

---

## Theme 3 — Wider, fresher input

### 8. Trend ingestion expansion
**Pitch.** Current feed-refresh is narrow. Add HackerNews top, ProductHunt, Reddit `/r/Entrepreneur` + `/r/SideProject`, Indie Hackers milestones. More signal → less stale ideation.

**Sketch.**
- `engine/feeds/` gets new adapters: `hn.py`, `producthunt.py`, `reddit.py`, `indiehackers.py`.
- Each returns normalized `TrendItem(source, title, url, score, posted_at, summary)`.
- `feed_refresh` cadence already exists; just register new sources.
- Rate limits + ETag caching per source.
- Dashboard `/trends` page already has hooks; just surface new sources.

**Effort:** M (four scrapers, not deep but each has its own quirks).
**Why it matters:** Variety + freshness — directly attacks the "drumming the same drum" risk. Indie Hackers in particular surfaces *what real people are paying for right now*.

---

### 9. LLM-driven super-idea synthesis
**Pitch.** Current dedup clusters similar ideas. Synthesis goes further: Haiku reads N adjacent ideas and writes one *bridge* idea that combines their strongest elements. This is how breakthroughs actually emerge.

**Sketch.**
- New cadence `synthesize` (every 6h).
- Pulls top-N approved-or-high-fundability ideas in adjacent embedding clusters.
- Haiku prompt: "Here are 5 ideas. Write one new idea that uses the best mechanism from each. It must not be a vague mashup — name the wedge."
- Stored with `mode=synthesis` and `parent_ideas=[ids]` so lineage is auditable.
- Synthesized ideas re-enter the normal score → fundability → auto-promote stream.

**Effort:** M.
**Why it matters:** This is the "1+1=3" generator. Cheap variety multiplier and a plausible source of the highest-fundability ideas, since it explicitly recombines what already scored well.

---

### 10. Adversarial reviewer that kills duds
**Pitch.** A devil's-advocate Haiku pass that tries to *kill* every idea before it reaches auto-promote. "Why won't this make money? Who already does it? What's the cheapest substitute?" Surviving ideas are sturdier.

**Sketch.**
- New cadence `kill_review` between `review` and `verdict_audit`.
- For each idea above a fundability threshold: Haiku returns `{kill_score 0-100, top_three_objections, weakest_assumption}`.
- Ideas with `kill_score >= 70` get auto-archived (still browsable, but excluded from auto-promote).
- Dashboard column "Objections" — owner sees the steel-manned counter-argument inline.

**Effort:** S.
**Why it matters:** Inverts the existing optimism bias. Combined with edge-finder (#4), this is offense + defense on idea quality.

---

## Theme 4 — Autonomy & ergonomics for the owner

### 11. Slack/Discord notifications on Monday auto-promotion
**Pitch.** The Monday auto-promote currently requires the owner to *look*. A push notification turns it into a habit.

**Sketch.**
- `notify/` module with adapters `slack.py`, `discord.py`.
- Webhook URL via `FORGE_SLACK_WEBHOOK` / `FORGE_DISCORD_WEBHOOK` (optional — silent if unset).
- Hooks: auto-promote (Mon 01:00), auto-build success/failure (Mon 02:30), revenue events (#2) above threshold, weekly digest (Sun 18:00 — top 3 ideas, persona leaderboard, $ this week).
- Idempotency by event hash.

**Effort:** S.
**Why it matters:** Pure autonomy lift. Engine becomes a teammate that pings you, not a tab you forget.

---

### 12. Multi-user dashboards / share-link to a specific idea
**Pitch.** Owner wants to send a single idea to a collaborator or investor without exposing the whole brain. Per-idea share links with read-only public view.

**Sketch.**
- `share_tokens(token, idea_id, expires_at, created_by)` table.
- `POST /api/ideas/{id}/share` returns `https://forge/share/{token}`.
- Public route `/share/{token}` renders a stripped detail page (idea, fundability, objections, build estimate — no internal scoring innards).
- CSP headers locked down; no auth cookies on share routes.
- Tokens expire 14 days default, revocable from idea detail page.

**Effort:** S.
**Why it matters:** Lets the owner monetize the *engine itself* by sharing — the path to investor / paying-customer interest.

---

### 13. Public marketplace board (opt-in)
**Pitch.** Top-N auto-promoted ideas surfaced on a public board if the owner flips a per-idea `marketplace_visible` flag. Becomes a discovery surface and lead magnet.

**Sketch.**
- New route `/market` listing public ideas with build estimate + fundability rationale.
- Each card has a "claim & build" CTA mailing the owner.
- Static rendering with 5-minute cache (Cloudflare-friendly).
- Per-idea toggle on detail page; default off.
- No PII; rate-limited.

**Effort:** M.
**Why it matters:** Top-of-funnel for a future SaaS. Even if no one builds the ideas, the board is SEO + waitlist bait. Owner-controlled visibility means zero risk of leaking unfinished thinking.

---

## Theme 5 — Engine durability & model economics

### 14. Sonnet fallback architecture (cost-resilience)
**Pitch.** Haiku is cheap today. If Anthropic raises Haiku prices or deprecates the model, the engine should swap to Sonnet (or downshift to a cheaper tier) without a re-architecture.

**Sketch.**
- Introduce `engine/llm/router.py` with `Router.choose(task_type) -> ModelHandle`.
- Config-driven: `forge.yaml` declares per-task model + price ceiling. Example: `{task: generate, model: haiku-4.5, ceiling_per_call_cents: 1.0}`.
- Router enforces ceiling; on breach, escalates to fallback chain.
- Daily budget guard: per-cadence and global `FORGE_DAILY_CENTS_BUDGET`; cadences self-throttle.
- All existing CLI calls migrate behind `Router`.

**Effort:** M.
**Why it matters:** Cost is the only reason this project is viable at $2-3/mo. One pricing change shouldn't break the math.

---

### 15. Idea-quality regression suite
**Pitch.** As prompts and weights drift, idea quality can silently degrade. Lock a golden set: re-score it nightly, alert if mean fundability moves >2σ from baseline.

**Sketch.**
- `tests/quality/golden_ideas.json` — 50 hand-picked ideas spanning categories.
- Nightly job re-scores all of them with current scorer; stores in `quality_runs` table.
- Alert (uses #11 notify) on drift.
- Dashboard tile: "Scorer drift: ±X% vs baseline".

**Effort:** S.
**Why it matters:** Quality is the only moat. This is the canary that catches prompt regressions before they ship to auto-promote.

---

### 16. Cost attribution per cadence
**Pitch.** "Engine costs $2-3/mo" is true today but opaque per-feature. Per-cadence cost reporting lets the owner cull cadences that aren't carrying their weight.

**Sketch.**
- `cost_events(cadence, model, prompt_tokens, completion_tokens, cents, ts)`.
- All `Router.choose` calls log here.
- Dashboard `/costs` page: per-cadence stacked area, top-10 most expensive prompts, dollars per shipped idea, dollars per dollar of attributed revenue (once #2 lands).
- Weekly digest section.

**Effort:** S.
**Why it matters:** Without this, scaling decisions are guesses. With it, the owner can confidently turn cadences on/off.

---

## Theme 6 — Quality-of-life smaller wins

### 17. "Why this idea?" provenance panel
**Pitch.** Each idea's detail page shows a lineage panel: trend items that seeded it, persona that wrote it, edge-finder angles used, parent ideas if synthesized. Trust comes from receipts.

**Sketch.**
- Already most data exists. Just plumb to the detail page.
- New section `<provenance>` rendered from `idea.lineage` JSON column.
- Add `lineage` column where missing; backfill on next generation.

**Effort:** S.
**Why it matters:** Trust in auto-promote depends on the owner believing the engine isn't hallucinating. Receipts > vibes.

---

### 18. Owner-feedback inline (thumbs + reason)
**Pitch.** One-click thumbs up/down with a free-text "why" feeds directly into persona/category weights. The owner becomes a passive trainer just by browsing.

**Sketch.**
- `feedback(idea_id, vote enum{up,down}, reason text, ts)` table.
- API: `POST /api/ideas/{id}/feedback`.
- Detail page gets the buttons.
- Weights pipeline (#3, #5) treats `up=+1`, `down=-2`, weights reasons by TF-IDF for future prompt tuning.

**Effort:** S.
**Why it matters:** Highest-bandwidth signal source available; turns every browsing session into training data.

---

### 19. Weekly retro digest (engine writes its own report card)
**Pitch.** Sunday-evening Haiku-written summary of the week: "I generated N ideas across M categories. Top fundability: X. Auto-promoted Y. Drift on scorer: Z. Recommendations for next week's prompt: ..."

**Sketch.**
- New cadence `weekly_retro` (Sun 18:00).
- Pulls metrics, costs, drift, top + bottom ideas; Haiku writes a markdown digest.
- Posted via `notify/` (#11) and stored at `/retros/{date}`.
- Recommendations section is action-flagged; owner clicks to apply.

**Effort:** S.
**Why it matters:** Engine reflecting on itself is the closest thing to autonomy hygiene. Owner gets a single weekly artifact to skim instead of dashboards to mine.

---

### 20. Idea-to-landing-page generator
**Pitch.** Given an auto-promoted idea, generate a single-page landing site (headline, three bullets, email-capture form, Stripe link) and deploy it under a forge subdomain. Validate demand *before* writing code.

**Sketch.**
- New module `build/landing.py`: Haiku writes copy from idea + build estimate.
- Static page template (HTML + minimal CSS, no JS to satisfy CSP). Form posts to `/api/landing/{slug}/signup`.
- Deploy to a Caddy/Nginx static bucket on the existing host; CNAME via `forge.{owner-domain}/{slug}`.
- Signups feed `idea_outcomes.signup_count` — demand signal even before MVP.

**Effort:** M.
**Why it matters:** Demand validation is the missing step between "auto-promoted" and "auto-built". Cheap signal that informs whether to spend effort on #1 for that idea.

---

## Suggested sequencing

A pragmatic v0.15 → v0.18 ladder:

- **v0.15 — Learn**: #2 (revenue webhook) + #18 (inline feedback) + #16 (cost attribution) + #15 (quality regression). All small/medium; sets up the data layer everything else needs.
- **v0.16 — Sharpen**: #4 (edge-finder) + #10 (kill-review) + #5 (persona weights) + #7 (build estimate). Quality + variety lift before any auto-building.
- **v0.17 — Ship**: #20 (landing page) → #1 (auto-scaffold). Demand-validate before building, then build.
- **v0.18 — Compound**: #3 (outcome-feedback weights) + #6 (A/B scorer) + #9 (synthesis) + #14 (router). Now the engine is genuinely self-improving on the money axis.

Notifications (#11), provenance (#17), trend expansion (#8), share links (#12), marketplace (#13), retro (#19) slot in opportunistically.
