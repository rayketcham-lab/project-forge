"""LLM-first idea generator — the pivot away from template-fill.

The original `auto_scan.generate_local_idea` is mechanical: it picks a
template, fills in slots from CATEGORY_SEEDS, and emits an idea. After
4,800+ ideas the slots are saturated and the engine just paraphrases
itself ("drumming the same drum" — Ray, 2026-06-08).

This module does the opposite: ask Haiku 4.5 for a *whole* idea, with
three deliberate variety knobs feeding the prompt:

  1. MODE (one of 5)
     - novel       fresh problem-solution pair for the persona
     - inversion   take a paid SaaS, build the free / open-source version
     - bundle      combine 3 existing tools into a single focused product
     - microservice extract one 100-line micro-utility from a big tool
     - adversarial break an assumption everyone makes in this category

  2. PERSONA (rotated per category)
     Category-specific (indie hackers for money bots, CISOs for security
     tools, parents for consumer apps, etc.). Each cycle picks a fresh
     persona to keep the framing varied.

  3. ANTI-SIMILARITY
     30 most-recent active idea names + taglines from the same category
     are passed in with an explicit "do NOT produce anything resembling
     these" instruction. Pre-prevents the regrowth pattern that the
     INSERT-time dedup gates catch reactively.

Cost: ~$0.0024/call at Haiku pricing. At the 1h expand cadence that's
$0.058/day. The semantic-dedup tie-breaker fires on ~10% of borderline
cases so total LLM bill is roughly $2/month at default cadence.

Caller:
    result = await generate_idea_llm(db, category)
    if result is None:
        idea = template_fallback(category)
    else:
        await filter_and_save(result.idea, db)

Returns None when no backend is reachable or the LLM produces
unparseable output — caller falls back to the template path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

from project_forge.engine.llm_backend import (
    LLMBackend,
    resolve_cheap_backend,
    resolve_role_backend,
)
from project_forge.engine.saturation import density_prompt_block
from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


GENERATION_MODES = ["novel", "inversion", "bundle", "microservice", "adversarial"]


# ARTIFACT TYPES — orthogonal to MODE. Mode is the *thinking lens*
# (novel/inversion/bundle/…); artifact type is the *shape* of the
# output. Default behaviour: a project-pitch. For Claude Lab categories
# we rotate through 8 specific artifact shapes so Churn doesn't just
# produce 50 variants of the same "project pitch" — it produces ideas
# for skills, sub-agents, MCP servers, hooks, slash commands, workflows,
# protocols, and raw capability extensions.
ARTIFACT_TYPES = [
    "skill",  # .claude/skills/ entry — single-purpose, focused
    "sub-agent",  # .claude/agents/ entry — specialized agent w/ role
    "mcp-server",  # MCP protocol server exposing N tools
    "hook",  # Claude Code lifecycle hook (PreToolUse, etc.)
    "slash-command",  # /command in a project for a repeated workflow
    "workflow",  # composition of 2-4 sub-agents fanned for a task
    "protocol",  # standard/spec proposal for inter-agent comms
    "ability",  # raw capability extension — what the agent can NOW do
]

# Categories that get artifact-type variety. Everything else stays on
# the default project-pitch shape (artifact_type = None).
_ARTIFACT_ROTATION_CATEGORIES = {
    IdeaCategory.CLAUDE_SKILLS_AGENTS,
    IdeaCategory.AI_MARKETPLACE,
    # v0.16 Claude Lab expansion — same artifact-shape rotation so these
    # categories also pitch skills / sub-agents / MCP servers / etc.
    IdeaCategory.AGENT_INFRA,
    IdeaCategory.CLAUDE_EVALS,
    IdeaCategory.AGENT_SECURITY,
    IdeaCategory.CONTEXT_MEMORY,
}


_ARTIFACT_PROMPTS: dict[str, str] = {
    "skill": (
        "Pitch this as a Claude Code SKILL (a `.claude/skills/{name}/` entry). "
        "Skills are single-purpose, focused capabilities the model invokes when "
        "the user's request matches. The pitch should specify: the trigger "
        "phrase / pattern, the deterministic instructions in SKILL.md (~50-200 "
        "lines), what files/tools the skill needs, and what the success signal "
        "looks like. Resist scope creep — one skill, one job."
    ),
    "sub-agent": (
        "Pitch this as a Claude SUB-AGENT (a `.claude/agents/{name}.md` entry "
        "spawnable via the Agent tool). Sub-agents are specialised personas "
        "with constrained tool access and a clear domain. The pitch should "
        "specify: the role + persona, when the parent agent should fan it out, "
        "which tools it gets (read-only? bash? edit?), and what artefact it "
        "returns to the parent. Aim for fanned-out parallelism, not sequential "
        "delegation."
    ),
    "mcp-server": (
        "Pitch this as an MCP (Model Context Protocol) SERVER. MCP servers "
        "expose a set of typed tools the agent can call. The pitch should "
        "specify: the 3-6 tools exposed (name + JSON schema for inputs / "
        "outputs), the transport (stdio / SSE), auth model, and what makes "
        "this server worth spinning up vs. shelling out. Concrete schemas, "
        "not vibes."
    ),
    "hook": (
        "Pitch this as a Claude Code HOOK (PreToolUse, PostToolUse, "
        "UserPromptSubmit, PreCompact, Stop, SessionStart, etc.). Hooks fire "
        "deterministically at lifecycle points. The pitch should specify: "
        "which hook event fires it, what the hook script does in 5-30 lines, "
        "what it blocks or allows, and the failure mode if it errors. Hooks "
        "are guardrails or augmentations — name which."
    ),
    "slash-command": (
        "Pitch this as a Claude Code SLASH COMMAND (a `.claude/commands/{name}.md` "
        "entry). Slash commands are user-invokable workflows for a repeated "
        "task in a specific project. The pitch should specify: the command "
        "name (`/foo`), the arguments it takes, what tools / sub-agents it "
        "drives, and the final output the user sees. Compare to existing "
        "commands so the niche is clear."
    ),
    "workflow": (
        "Pitch this as an AGENT WORKFLOW — a composition of 2-4 sub-agents "
        "or skills fanned out (parallel) or chained (sequential) for a "
        "multi-step task. The pitch should specify: the entry trigger, the "
        "agent graph (which agents run when, what data flows between them), "
        "the supervisor's role, and the verifiable output. Diagram the graph "
        "in text. Workflows must be reproducible, not stochastic."
    ),
    "protocol": (
        "Pitch this as a PROTOCOL / SPEC PROPOSAL for the agent ecosystem. "
        "Protocols are how agents, marketplaces, or tools talk to each other "
        "at scale. The pitch should specify: the problem the protocol solves, "
        "the message shape (JSON / wire format), versioning + extensibility "
        "rules, and reference implementations needed for adoption. Be explicit "
        "about who has to adopt it for it to work."
    ),
    "ability": (
        "Pitch this as a raw ABILITY EXTENSION — what the agent can NOW do "
        "that it couldn't before. Not a project, not a tool — a capability "
        "primitive. The pitch should specify: the gap in current capability, "
        "the minimum primitive (one function? one model? one dataset?) that "
        "closes the gap, what becomes possible downstream once this primitive "
        "exists, and the cheapest experiment that proves the primitive works."
    ),
}


_MODE_PROMPTS: dict[str, str] = {
    "novel": (
        "Pitch a fresh project idea that solves a SPECIFIC problem this "
        "persona feels acutely. The idea should be concrete enough that "
        "you could draw a one-screen demo on a napkin."
    ),
    "inversion": (
        "Pick a paid SaaS or commercial tool this persona is currently "
        "stuck paying for, where the underlying engine is actually simple. "
        "Pitch the open-source / self-hosted / free version that wins on "
        "control, transparency, or price."
    ),
    "bundle": (
        "This persona is paying for 3+ overlapping tools whose features "
        "could plausibly live in one focused product. Pick a real combination "
        "and pitch the unified product. Be specific about which existing "
        "tools you're consolidating."
    ),
    "microservice": (
        "Take a big complex tool this persona uses and extract ONE tiny "
        "100-line utility that does ONE thing better than the parent product. "
        "The idea should be brutally narrow — a Unix-philosophy single-job tool."
    ),
    "adversarial": (
        "Identify one assumption that everyone working in this category "
        "takes for granted but is actually wrong (or rapidly becoming wrong). "
        "Pitch the project that exploits the gap created by that wrong "
        "assumption. State the assumption explicitly in the description."
    ),
}


# Personas per category. Keys present here override the generic fallback;
# fall-through goes to a category-agnostic operator persona so we still
# generate something coherent. Each persona is one line: role + pain.
PERSONAS_BY_CATEGORY: dict[IdeaCategory, list[str]] = {
    IdeaCategory.AUTOMATION_INCOME: [
        "indie hacker chasing $5k/month MRR with one solo product",
        "newsletter operator with 8k subscribers, $200/mo Substack bill, no audience-targeting tools",
        "Etsy seller doing $30k/yr, manual listing optimization eats Sunday nights",
        "creator with 50k followers, monetization is brand deals only, hates the inconsistency",
        "freelance agency owner, 4 clients, struggling to standardize lead-gen across them",
        "side-hustler with a day job, 5 hours per weekend, needs deployable-in-a-weekend tools",
        "POD seller on Printify, churning niche designs, manual trend research is the bottleneck",
        "course creator with 200 students, retention is bad, has no signal on where they drop off",
        "B2B affiliate, ranks for 5 keywords, content production is the throttle",
        "ecommerce dropshipper $80k/yr who needs to graduate to a real brand",
    ],
    IdeaCategory.CONSUMER_APP: [
        "parent of two under 8, juggling soccer schedules and pediatrician appointments",
        "renter sharing a small apartment with two roommates, kitchen logistics are a daily fight",
        "remote worker in a new city, wants to meet people but social apps are exhausting",
        "ADHD adult who can't make pomodoro stick, needs body-doubling but lives alone",
        "first-time homebuyer overwhelmed by inspection reports and contractor estimates",
        "elderly daughter coordinating her mother's medications across three doctors",
        "couple negotiating shared finances without merging accounts",
        "fitness enthusiast tracking macros, hates that MyFitnessPal sold to AdTech",
        "small-town homeowner with a giant lawn and seasonal storm damage",
        "queer person navigating partner visits + healthcare in a hostile state",
    ],
    IdeaCategory.PRODUCTIVITY: [
        "consultant managing 7 client engagements, three timezones, calendar is a graveyard",
        "engineering manager of 12, weekly 1:1s + status reports + planning + escalations",
        "PhD student in year 4, writing dissertation while running experiments + TA-ing",
        "indie author drafting a novel between contract gigs",
        "head of growth at Series A, owns SEO + email + paid + analytics, only one report",
        "founder doing investor updates monthly, board prep quarterly, all from a Google Doc",
        "executive coach with 14 clients, needs to track each one's trajectory without CRM bloat",
        "researcher tracking 200 papers across 4 ongoing projects, citation manager is mush",
        "solo developer running 6 side projects + a day job",
        "remote PM running two product squads, async-first, drowning in Notion docs",
    ],
    IdeaCategory.CLAUDE_SKILLS_AGENTS: [
        "indie dev shipping Claude Code skills for their 5-person team, wants a skill that wins on day one",
        "platform engineer at a 200-person org, building MCP servers for internal infra, needs auth + audit baked in",
        "AI researcher prototyping agentic workflows, wants fanned-out sub-agents that compose without glue",
        "engineering manager who wants per-PR review sub-agents that learn from accepted/rejected diffs",
        "terminal power user automating their life: dotfiles + email + RSS + bills via Claude Code agents",
        "DevSecOps engineer building a sub-agent that grades code for safety + hallucination risk before merge",
        "ML engineer wanting Claude integrated into experiment tracking with auto-tagged regressions",
        "founder building an AI-first dev tool, looking for a primitive everyone will need but nobody has built",
        "consultant who wants reproducible agent workflows for clients — same input, same output, every time",
        "open-source maintainer building skills they wish existed for their own repo",
        "educator turning a CS curriculum into agent-led labs with auto-graded code reviews",
        "DevRel engineer building MCP demos for a platform team, needs each one to be impressive in 90 seconds",
    ],
    IdeaCategory.AI_MARKETPLACE: [
        "founder building 'the App Store for agents' — needs discovery + payment + reputation that doesn't suck",
        "VC scouting AI marketplaces, wants to know which primitive everyone will pay for once it exists",
        "AI engineer at a platform company designing the agent layer that handles attribution + revenue split",
        "skill author wanting income from their distribution without building a whole storefront",
        "team lead at a 500-engineer org wanting skill governance: who installed what, where's the budget cap",
        "buyer comparing two agents for the same task, needs a trust signal that isn't 'GitHub stars'",
        "DevRel for an AI platform launching a marketplace, wants the right primitives shipped at GA",
        "compliance officer worried about agent provenance — who trained this, on what data, with what consent",
        "researcher studying agent quality signals, wants a measurement framework the market will respect",
        "educator teaching prompt engineering, wants an exemplar marketplace students can browse + learn from",
        "indie developer wanting to bundle 5 skills as 'my style' and rent them to others",
        "platform engineer building the rev-share rails so 3-agent collaborations distribute earnings fairly",
    ],
    IdeaCategory.CREATOR_TOOLS: [
        "podcaster releasing weekly, edits everything herself in Descript, hates the export step",
        "YouTube creator at 80k subs, thumbnails are the bottleneck — A/B testing is a fantasy",
        "TikTok creator producing 5/week, repurposing to YouTube Shorts is manual",
        "newsletter writer at 12k subs, posts every Thursday, no time for cross-platform promo",
        "indie game streamer trying to start a paid Discord, monetization is a maze",
        "music producer dropping a track a month, social-clip generation is a weekly side-quest",
        "comic artist serializing on Substack, needs to clip + post to Threads/IG",
        "course creator with 500 students, wants better completion analytics than Teachable provides",
        "writer juggling Substack + Medium + personal site, cross-posting eats 3 hours/week",
        "live-streamer who wants automated highlight reels for clip-farming subreddits",
    ],
    # v0.16 money-bot expansion personas.
    IdeaCategory.MICRO_SAAS: [
        "solo founder shipping a paid micro-tool every quarter, wants one that hits $1k MRR fast",
        "indie hacker who sells 3 tiny APIs, each $200/mo, looking for the next single-endpoint product",
        "bootstrapper allergic to VC, needs a product one person can run and support forever",
        "freelance dev productizing a script clients keep asking for, wants Stripe live this weekend",
        "no-code builder who wants a focused backend they can charge a flat monthly fee for",
        "open-source maintainer monetizing a hosted version of their CLI tool",
        "agency owner spinning off an internal tool into a standalone paid SaaS",
        "side-project builder with a day job, 6 hours a week, needs deploy-in-a-weekend scope",
        "micro-ISV doing $4k MRR across 5 tools, hunting the sixth with low support burden",
        "developer who wants recurring revenue from one sharp feature, not a sprawling platform",
    ],
    IdeaCategory.VERTICAL_SAAS: [
        "dentist running two practices, juggling scheduling + recalls + payments across three apps",
        "boutique gym owner losing money to no-shows, membership management is a spreadsheet",
        "HVAC contractor quoting jobs on paper, invoices go out late, cash flow is a guess",
        "solo immigration lawyer tracking case deadlines in email, terrified of a missed filing",
        "vet clinic manager whose recall reminders are manual and whose no-show rate is brutal",
        "independent restaurant owner with no real handle on food cost or menu margins",
        "tattoo studio owner managing bookings + deposits + portfolios across Instagram DMs",
        "residential cleaning company owner routing crews and chasing payment by text",
        "small-nonprofit director tracking donors, grants, and volunteers in disconnected sheets",
        "med-spa owner whose treatment plans and appointments live in three incompatible tools",
        "music teacher with 40 students, scheduling + progress + invoicing all by hand",
        "small landlord with 12 units, maintenance requests and rent roll scattered everywhere",
    ],
    IdeaCategory.ECOMMERCE_TOOLS: [
        "Shopify seller doing $40k/mo, overselling because inventory isn't synced to Amazon",
        "Amazon FBA seller fighting listing hijackers and fake reviews by hand every week",
        "DTC brand founder whose return rate is eating margin and nobody knows which SKUs",
        "Etsy seller scaling past 1k orders/mo, fulfillment and shipping rates are chaos",
        "subscription-box operator bleeding churn tied to delivery delays they can't see",
        "dropshipper graduating to a real brand, needs landed-cost truth per unit",
        "multichannel seller on 4 marketplaces, fee reconciliation is a monthly nightmare",
        "B2B wholesaler taking orders by email, wants a tiered-pricing self-serve portal",
        "print-on-demand seller whose product photos and listings take all weekend",
        "small 3PL operator optimizing pick paths and proving delivery for clients",
    ],
    IdeaCategory.FINTECH_TOOLS: [
        "freelancer chasing late invoices, no read receipts, cash flow is feast or famine",
        "1099 contractor terrified of quarterly estimated taxes, does it on a napkin",
        "small-business owner with no cash-flow forecast, finds out about shortfalls too late",
        "agency owner reconciling multi-currency contractor payouts by hand each month",
        "couple budgeting shared expenses without merging accounts, fighting over categories",
        "solo-LLC founder unsure how to split owner draw vs payroll, no finance hire yet",
        "consultant burning retainer hours invisibly, clients surprised by the top-up ask",
        "e-commerce seller who just learned they owe sales tax in five states retroactively",
        "creator with real business income missing deductions because receipts are everywhere",
        "bookkeeper for 15 micro-clients drowning in receipt-to-QuickBooks data entry",
    ],
    # v0.16 Claude Lab expansion personas.
    IdeaCategory.AGENT_INFRA: [
        "platform engineer running 200 agents nightly, token spend is unpredictable and scary",
        "infra lead who needs agent runs to survive crashes and resume from a checkpoint",
        "founder of an AI-first product whose cold-start latency is killing the demo",
        "SRE on call for an agent fleet with no backpressure, queues melt during spikes",
        "data engineer fanning out 500 sub-agents, half do redundant work with no shared context",
        "cost owner who needs per-team token attribution before finance kills the project",
        "platform team standardizing how agents get short-lived secrets without leaking them",
        "ML infra engineer wanting deterministic replay of any agent run for debugging",
        "engineering manager who needs blue/green agent deploys with shadow comparison",
        "startup CTO who must hard-cap spend per task or one runaway loop bankrupts the month",
    ],
    IdeaCategory.CLAUDE_EVALS: [
        "AI product lead who ships prompt changes blind, no idea if quality regressed",
        "ML researcher whose evals are flaky and can't tell model variance from real change",
        "DevSecOps engineer wanting an eval gate that blocks merges on quality regressions",
        "team lead building an LLM-as-judge harness but worried the judge is biased",
        "founder comparing two models, needs a cost-vs-quality frontier, not a vibe check",
        "QA engineer who needs golden datasets mined from real production traces",
        "researcher measuring hallucination rate grounded against a source corpus",
        "platform engineer wiring tool-call correctness scoring into CI",
        "AI eng who needs to prove jailbreak + injection resistance before launch",
        "product owner who wants a leaderboard ranked by validated outcomes, not downloads",
    ],
    IdeaCategory.AGENT_SECURITY: [
        "security engineer treating the agent as an attack surface, fears indirect prompt injection",
        "platform owner who needs MCP servers verified + signed before anyone installs one",
        "DevSecOps lead wanting least-privilege tool grants per task, not blanket session access",
        "compliance officer demanding tamper-evident audit logs for every agent action",
        "AppSec engineer red-teaming agents by fuzzing their tools with adversarial inputs",
        "enterprise architect worried about agents exfiltrating data to new domains",
        "PKI engineer designing agent identity + attestation so tools can trust the caller",
        "incident responder who needs a kill-switch + quarantine for misbehaving agents",
        "security lead scanning the skill supply chain — who wrote it, what it reads, what it sends",
        "fintech security engineer blocking PII from crossing a classification boundary",
    ],
    IdeaCategory.CONTEXT_MEMORY: [
        "agent builder whose sessions forget every decision the moment the window fills",
        "consultant running 7 client agents that must never leak one client's facts to another",
        "developer who wants project memory that survives across sessions and machines",
        "ML engineer building recall that surfaces only the memories a task actually needs",
        "team lead who wants shared agent memory with provenance and access control per fact",
        "researcher needing time-aware memory: 'what did this codebase look like 3 months ago'",
        "power user whose agent keeps repeating approaches that already failed last week",
        "platform engineer who needs a memory write-policy: is this even worth remembering",
        "founder whose agent's stored facts contradict current reality and nobody reconciles them",
        "knowledge-management lead wanting citation-backed memory linking every fact to a source",
    ],
    # v0.19 Crypto/Web3 board personas — on-chain operators feeling the
    # security / infra / payments / compliance pain each category targets.
    IdeaCategory.ONCHAIN_SECURITY: [
        "protocol security lead whose contracts get re-audited by hand every release, slow and costly",
        "DAO treasurer terrified a single compromised signer drains a nine-figure treasury",
        "wallet-vendor PM whose users keep approving malicious token allowances they can't read",
        "audit-firm engineer drowning in scanner false-positives with no triage tooling",
        "custody-desk security engineer needing hardware-attested proof of who signed each transfer",
        "PKI engineer bringing threshold signing and key ceremonies to a protocol treasury",
        "incident responder for a DeFi protocol with no runtime kill-switch when invariants break",
        "exchange security lead correlating cross-chain flows to catch bridge exploits early",
        "smart-contract dev who wants every commit re-scanned for new vuln classes automatically",
    ],
    IdeaCategory.WEB3_INFRA: [
        "dapp developer whose app breaks every time a public RPC endpoint rate-limits them",
        "data engineer hand-rolling indexers to turn contract events into a usable API",
        "trading-firm infra lead needing reorg-safe, finalized on-chain data streams",
        "wallet team paying three vendors for RPC, gas, and history with no unified SLA",
        "L2 team standing up archival nodes just to answer historical-state queries",
        "SRE autoscaling a node fleet by hand as request load swings 10x intraday",
        "analytics startup brute-forcing normalized on-chain events into Snowflake",
        "NFT marketplace engineer needing verifiable provenance and enforced creator royalties",
        "game studio needing cheap ephemeral chain forks for every CI run",
    ],
    IdeaCategory.DEFI_TOOLING: [
        "crypto fund manager who got liquidated overnight with no automated deleverage",
        "DAO treasurer with no runway forecast or diversification view of the treasury",
        "crypto CPA reconciling cost basis across 40 wallets by exporting CSVs",
        "LP whose impermanent loss silently eats returns with no rebalancing signal",
        "market maker eating MEV sandwiches with no protective order routing",
        "family office needing true cross-protocol collateral-concentration reporting",
        "governance lead who misses treasury-affecting votes buried in proposal noise",
        "serious individual investor with no unified on-chain P&L across chains",
        "yield allocator with no way to score protocol risk by audit history and TVL concentration",
    ],
    IdeaCategory.STABLECOIN_PAYMENTS: [
        "e-commerce merchant wanting stablecoin checkout that settles straight to their bank",
        "marketplace ops lead paying global contractors, killed by wire fees and delays",
        "AI agent builder who needs agents to pay per API call without a human in the loop",
        "SaaS billing owner wanting recurring stablecoin subscriptions with dunning and retries",
        "remittance operator routing corridors to the cheapest compliant off-ramp",
        "fintech founder reconciling on-chain receipts to their accounting stack by hand",
        "treasury manager manually converting volatile receipts into a stablecoin every day",
        "cross-border payroll provider whose workers want automatic local-currency off-ramp",
        "usage-based API vendor wanting per-call micro-payments settled in stablecoins",
    ],
    IdeaCategory.CRYPTO_COMPLIANCE: [
        "exchange compliance officer screening wallets against sanctions lists by hand",
        "VASP ops lead scrambling to implement travel-rule data exchange before an audit",
        "neobank risk lead needing tunable transaction-monitoring typologies with case management",
        "forensics analyst tracing stolen funds through mixers with inadequate tooling",
        "stablecoin issuer producing proof-of-reserves attestations under examiner pressure",
        "regtech founder mapping token activity to per-jurisdiction licensing rules",
        "custodian compliance engineer re-screening the whole customer base on list updates",
        "AML officer assembling SAR filings from raw on-chain activity manually",
        "compliance lead who needs tamper-evident audit trails ready for examiner requests",
    ],
    # v0.20 Cashflow board personas — operators one software system away
    # from their first (or next) folding-cash dollar.
    IdeaCategory.PRODUCTIZED_SERVICES: [
        "PKI engineer who could sell fixed-price cert-expiry audits tomorrow but has no intake or invoicing system",
        "freelance developer tired of hourly billing, wants a fixed-scope offer with automated delivery",
        "SEO consultant doing bespoke audits by hand, every engagement starts from a blank document",
        "accessibility specialist who wants a productized WCAG review with a repeatable scan-and-report pipeline",
        "fractional CFO assembling the same cash-flow review deck for every client manually",
        "security engineer moonlighting, needs a packaged TLS and header review that delivers itself",
        "agency owner turning a one-off cloud-bill teardown into a recurring productized offer",
        "photographer whose editing backlog eats weekends, wants preset-driven post-processing as a service",
        "bookkeeper who wants a fixed-price cleanup package with automated bank-feed reconciliation",
    ],
    IdeaCategory.DIGITAL_PRODUCTS: [
        "Notion power user whose workspace templates get requests daily but has no storefront or delivery automation",
        "Etsy printables seller hunting the next niche with verifiable search demand",
        "developer with a boilerplate five friends already paid for, needs license keys and a landing page",
        "course creator who wants a record-once pipeline: package, paywall, deliver, update",
        "designer sitting on three hundred icons that could be a marketplace bundle with auto-tagging",
        "KDP publisher hand-building coloring-book interiors one page at a time",
        "music producer whose sample folder could be sellable packs with BPM tagging and previews",
        "career coach rewriting the same resume templates per client instead of selling the system",
        "teacher whose worksheets circulate free in Facebook groups while marketplace sellers charge for worse",
    ],
    IdeaCategory.COMMERCE_OPS: [
        "dropshipper burned twice by thirty-day ship times, wants supplier vetting before the next store",
        "side-hustler who wants demand proof before spending a dollar on inventory or ads",
        "print-on-demand seller whose designs get trademark-struck, needs screening before listing",
        "TikTok Shop seller riding trends manually, always two weeks late to the wave",
        "marketplace seller on three platforms overselling because inventory never syncs",
        "one-product-store operator whose ad-creative fatigue kills every winning product",
        "digital-goods seller fighting refund abuse with no delivery proof",
        "subscription-box operator guessing unit economics per box",
        "student entrepreneur with five hundred dollars to test a niche, can't afford one bad supplier",
    ],
    IdeaCategory.LEAD_GENERATION: [
        "ex-agency marketer who wants to own lead-gen assets instead of renting skills to clients",
        "developer who ranks first for a niche calculator but sends the traffic away for free",
        "local marketer selling plumber leads with a spreadsheet and burner phone numbers",
        "affiliate site owner post-algorithm-update, needs data-grounded comparison pages",
        "insurance agent who wants exclusive niche leads instead of shared aggregator scraps",
        "operator who wants a niche job board with employer fees but no audience yet",
        "wedding photographer who built the vendor list every engaged couple asks for — unmonetized",
        "solar installer paying three hundred dollars per aggregator lead, would fund a better source",
        "CPA who would pay per qualified lead during tax season if quality were verifiable",
    ],
    IdeaCategory.FLIPPING_ARBITRAGE: [
        "weekend flipper scrolling marketplace listings for hours to find one underpriced item",
        "retail-arbitrage seller scanning shelves with a phone, wants fee-true ROI before buying",
        "camera-gear flipper who knows prices cold but can't watch three platforms at once",
        "estate-sale hunter who wants photo-to-value estimates before the doors open",
        "refurb tech who flips broken consoles, guessing part costs per lot",
        "pallet buyer who wants manifest-to-resale decomposition before bidding",
        "domain investor screening expired auctions for backlink value by hand",
        "micro-acquisition buyer screening small SaaS listings for faked traffic",
        "textbook flipper racing buyback price windows every semester",
    ],
    # v0.23 PKI board personas — the people who actually run certificate
    # infrastructure and eat the pager when it breaks. Written as
    # "role — the specific thing that is broken for them today", because
    # the board's bar is a real operational gap, not a plausible product.
    IdeaCategory.PKI_REVOCATION: [
        "CA operator whose CRL crossed 200MB and whose CDN bill now scales with revocations",
        "browser security engineer who knows most clients soft-fail revocation checks and cannot prove otherwise",
        "OCSP responder owner sizing capacity for signatures ten times larger than today's",
        "PKI architect deciding between short-lived certificates and building real revocation infrastructure",
        "mobile platform engineer who cannot ship a hundred-megabyte revocation list to metered devices",
        "incident responder who needs to revoke fifty thousand certificates and has no rehearsed path",
        "CDN engineer absorbing revocation traffic spikes with no way to predict the next one",
        "compliance lead asked to state the maximum window during which a revoked cert is still trusted",
        "device fleet owner whose endpoints have never successfully fetched a CRL in production",
    ],
    IdeaCategory.CERT_LIFECYCLE: [
        "SRE who has been paged for the same expiry outage three times and still lacks an ownership map",
        "platform engineer whose renewal automation covers ninety percent of certs and cannot name the rest",
        "infrastructure lead staring down shorter mandated lifetimes with a partly manual fleet",
        "DBA whose database TLS certificates are outside every automation system the company owns",
        "Kubernetes operator whose cert rotation succeeds while pods keep serving the old certificate",
        "email infrastructure admin who cannot use ACME for the protocols that actually matter to him",
        "release manager whose renewal window keeps colliding with change freezes",
        "security engineer who cannot prove private keys are rotated rather than reused at renewal",
        "hosting provider scheduling thousands of renewals that all cluster on the same deadline",
    ],
    IdeaCategory.PQC_MIGRATION: [
        "PKI architect asked for a migration plan who cannot yet answer where RSA is even used",
        "CISO with a hard compliance deadline and no inventory of affected systems",
        "firmware engineer whose shipped devices verify updates with keys that can never be replaced",
        "vendor risk analyst trying to learn which products support post-quantum algorithms and when",
        "TLS engineer whose hybrid chains no longer fit in the initial flight",
        "HSM administrator discovering half the fleet's firmware cannot hold the new key types",
        "government contractor mapping systems to a published deprecation timeline with no tooling",
        "data owner estimating which archives outlive the point at which today's crypto fails",
        "interop engineer whose hybrid handshake works with one stack and silently downgrades with another",
    ],
    IdeaCategory.CA_OPERATIONS: [
        "internal CA owner running root ceremonies from a Word document and a camcorder",
        "PKI lead migrating off an end-of-life commercial CA product with no export path",
        "compliance engineer assembling audit evidence by hand every single year",
        "CA operator who found a misissuance and has no rehearsed disclosure or revocation sequence",
        "trust-store applicant trying to interpret root program requirements before spending a year on it",
        "security architect debugging why one client rejects a chain that every other client accepts",
        "key custodian worried the quorum no longer exists after two people left the company",
        "sub-CA owner who cannot prove name constraints actually bound what was issued",
        "domain owner who wants to know the moment anyone issues a certificate for his names",
    ],
    IdeaCategory.CERT_IDENTITY: [
        "service mesh operator who cannot say which workloads are authorized to call which others",
        "build engineer whose code-signing key is reachable from CI and knows it",
        "device manufacturer binding hardware attestation to operational certificates by hand",
        "zero-trust lead who has more machine identities than employees and no review process",
        "supply chain security engineer linking build provenance to the signing certificate used",
        "IoT operator whose constrained devices cannot fit a standard certificate profile",
        "platform owner cleaning up certificates issued to workloads that no longer exist",
        "release engineer who needs separate signing keys per channel and has one shared key",
        "incident responder planning mass machine-credential rotation after a suspected key compromise",
    ],
    # Security categories still get their detailed personas via the
    # original PERSONA_SEEDS list (diversity_prompts.py). We pull from
    # there in _pick_persona() when the category is not above.
}


@dataclass
class LLMGenerationResult:
    """One successful LLM generation. None means caller should fall back."""

    idea: Idea
    mode: str
    persona: str
    backend: str
    raw_response: str
    # v0.15a — which artifact shape this draw produced. None for the
    # default project-pitch shape, one of ARTIFACT_TYPES for the
    # Claude Lab categories.
    artifact_type: str | None = None


# --------------------------------------------------------------------------- #
# Mode selection                                                              #
# --------------------------------------------------------------------------- #


async def pick_least_used_mode(db: Database, category: IdeaCategory) -> str:
    """Pick the GENERATION_MODES entry that has produced the fewest active
    ideas in this category. Stable tiebreak on the mode list order so the
    selection is deterministic given equal counts."""
    cur = await db.db.execute(
        "SELECT generation_mode, COUNT(*) c FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "AND generation_mode IS NOT NULL "
        "GROUP BY generation_mode",
        (category.value,),
    )
    rows = await cur.fetchall()
    counts = {r["generation_mode"]: int(r["c"]) for r in rows}
    return min(GENERATION_MODES, key=lambda m: (counts.get(m, 0), GENERATION_MODES.index(m)))


async def pick_least_used_artifact(db: Database, category: IdeaCategory) -> str:
    """Pick the ARTIFACT_TYPES entry with the fewest active ideas in this
    category. Same rotation discipline as pick_least_used_mode — over time
    every artifact shape gets equal airtime.
    """
    cur = await db.db.execute(
        "SELECT artifact_type, COUNT(*) c FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "AND artifact_type IS NOT NULL "
        "GROUP BY artifact_type",
        (category.value,),
    )
    rows = await cur.fetchall()
    counts = {r["artifact_type"]: int(r["c"]) for r in rows}
    return min(ARTIFACT_TYPES, key=lambda a: (counts.get(a, 0), ARTIFACT_TYPES.index(a)))


# --------------------------------------------------------------------------- #
# Persona selection                                                           #
# --------------------------------------------------------------------------- #


def _security_personas() -> list[str]:
    """Pull the PERSONA_SEEDS from diversity_prompts and flatten role+pain
    into single-line persona strings. Late import to keep this module's
    import time low.
    """
    from project_forge.engine.diversity_prompts import PERSONA_SEEDS

    return [f"{p['role']} — {p['pain']}" for p in PERSONA_SEEDS]


def _pick_persona(category: IdeaCategory) -> str:
    pool = PERSONAS_BY_CATEGORY.get(category)
    if pool is None:
        # Security and other categories fall back to the original seed pool.
        pool = _security_personas()
    return random.choice(pool)


# --------------------------------------------------------------------------- #
# Anti-similarity                                                             #
# --------------------------------------------------------------------------- #


async def _recent_idea_lines(
    db: Database,
    category: IdeaCategory,
    limit: int = 30,
) -> list[str]:
    """Return up to `limit` recent 'name — tagline' strings from the same
    category. Used as the do-not-produce list in the prompt."""
    cur = await db.db.execute(
        "SELECT name, tagline FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "ORDER BY generated_at DESC LIMIT ?",
        (category.value, limit),
    )
    rows = await cur.fetchall()
    return [f"- {r['name']} — {r['tagline']}" for r in rows]


# --------------------------------------------------------------------------- #
# Prompt building + parsing                                                   #
# --------------------------------------------------------------------------- #


_JSON_SCHEMA_INSTRUCTION = """
Respond with JSON only — no markdown wrapping, no commentary:
{
  "name": "Short pitchable name (3-6 words, title case)",
  "tagline": "One-line summary, max 100 chars, lowercase, concrete",
  "description": "2-3 sentence pitch. State the problem, the solution, why it matters now.",
  "market_analysis": "Who pays for this and why. Be specific about the buyer.",
  "mvp_scope": "Phase 1, Phase 2, Phase 3 — what you build in each.",
  "tech_stack": ["language", "framework", "key-lib"],
  "feasibility_score": 0.70,
  "mode_rationale": "One sentence: why this mode fits this persona."
}
""".strip()


def _build_prompt(
    category: IdeaCategory,
    mode: str,
    persona: str,
    avoid_list: list[str],
    artifact_type: str | None = None,
    seed: str | None = None,
    density_block: str | None = None,
) -> str:
    avoid_block = "\n".join(avoid_list) if avoid_list else "(none yet)"
    seed_block = f"## Fresh real-world signal to react to (anchor the idea to this)\n{seed}\n\n" if seed else ""
    # v0.21 (#97): corpus-density section — crowded zones repel, white
    # space attracts. Rendered by saturation.density_prompt_block.
    density_section = f"{density_block}\n\n" if density_block else ""

    # Two prompt frames: the project-pitch (default — every category before
    # v0.15a used this) and the artifact-shape pitch (Claude Lab categories).
    if artifact_type is None:
        artifact_block = ""
        headline = f"You are pitching a project idea in the {category.value} category."
    else:
        artifact_block = f"## Artifact type: {artifact_type}\n{_ARTIFACT_PROMPTS[artifact_type]}\n\n"
        headline = (
            f"You are pitching a {artifact_type.upper()} for the "
            f"{category.value} category — not a generic project, this "
            f"specific artifact shape."
        )

    return (
        f"{headline}\n\n"
        f"## Persona\n{persona}\n\n"
        f"## Generation mode: {mode}\n{_MODE_PROMPTS[mode]}\n\n"
        f"{artifact_block}"
        f"{seed_block}"
        f"{density_section}"
        f"## Do NOT produce anything resembling these recent ideas\n"
        f"(no renames, no verb-tense variants, no 'X for {{vertical}}' clones):\n"
        f"{avoid_block}\n\n"
        f"## Output\n{_JSON_SCHEMA_INSTRUCTION}\n"
    )


def _strip_codefence(raw: str) -> str:
    raw = raw.strip()
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _parse_idea_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_codefence(raw))
    except json.JSONDecodeError:
        return None
    required = ("name", "tagline", "description", "market_analysis", "mvp_scope")
    if not all(isinstance(data.get(k), str) and data[k] for k in required):
        return None
    return data


def _build_idea_from_payload(
    payload: dict[str, Any],
    category: IdeaCategory,
    mode: str,
    artifact_type: str | None = None,
) -> Idea | None:
    try:
        score = float(payload.get("feasibility_score", 0.7))
    except (TypeError, ValueError):
        score = 0.7
    score = max(0.0, min(1.0, score))
    tech = payload.get("tech_stack") or ["python"]
    if not isinstance(tech, list):
        tech = ["python"]
    try:
        return Idea(
            name=payload["name"].strip()[:160],
            tagline=payload["tagline"].strip()[:200],
            description=payload["description"].strip(),
            category=category,
            market_analysis=payload["market_analysis"].strip(),
            feasibility_score=score,
            mvp_scope=payload["mvp_scope"].strip(),
            tech_stack=[str(t)[:40] for t in tech][:8],
            generation_mode=mode,
            artifact_type=artifact_type,
        )
    except Exception:  # pydantic validation errors etc.
        logger.exception("llm_generator: failed to build Idea from payload")
        return None


# --------------------------------------------------------------------------- #
# Top-level entry                                                             #
# --------------------------------------------------------------------------- #


async def generate_idea_llm(
    db: Database,
    category: IdeaCategory,
    *,
    mode: str | None = None,
    artifact_type: str | None = None,
    backend: LLMBackend | None = None,
    seed: str | None = None,
) -> LLMGenerationResult | None:
    """One LLM-first generation. Returns None when no backend reaches or the
    response fails parsing — caller falls back to the template path.

    `artifact_type` is None for the default project-pitch shape. For the
    Claude Lab categories (CLAUDE_SKILLS_AGENTS, AI_MARKETPLACE) we rotate
    through 8 artifact shapes (skill / sub-agent / mcp-server / hook /
    slash-command / workflow / protocol / ability) — pick happens here if
    the caller doesn't specify.
    """
    backend = backend if backend is not None else resolve_cheap_backend()
    if backend is None:
        return None

    mode = mode if mode in GENERATION_MODES else await pick_least_used_mode(db, category)

    # Artifact rotation: only Claude Lab categories cycle through types.
    if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
        artifact_type = None
    if artifact_type is None and category in _ARTIFACT_ROTATION_CATEGORIES:
        artifact_type = await pick_least_used_artifact(db, category)

    persona = _pick_persona(category)
    # v0.21 (#97): recent avoid-list shrinks 30 -> 15 to make room for the
    # density block — which carries the DENSE stems the recency window
    # misses (a 191-idea category used to look identical to a 31-idea one).
    avoid = await _recent_idea_lines(db, category, limit=15)
    density_block = await density_prompt_block(db, category)
    prompt = _build_prompt(
        category,
        mode,
        persona,
        avoid,
        artifact_type=artifact_type,
        seed=seed,
        density_block=density_block,
    )

    raw = backend.call(prompt) or ""
    if not raw.strip():
        logger.info("llm_generator: backend returned empty response (mode=%s)", mode)
        return None

    payload = _parse_idea_payload(raw)
    if payload is None:
        logger.info("llm_generator: payload parse failed (mode=%s)", mode)
        return None

    idea = _build_idea_from_payload(payload, category, mode, artifact_type=artifact_type)
    if idea is None:
        return None

    return LLMGenerationResult(
        idea=idea,
        mode=mode,
        persona=persona,
        backend=backend.name,
        raw_response=raw,
        artifact_type=artifact_type,
    )


# --------------------------------------------------------------------------- #
# Sniper board — grounded competitive-displacement generation                 #
# --------------------------------------------------------------------------- #


_SNIPE_JSON_SCHEMA_INSTRUCTION = """
Respond with JSON only — no markdown wrapping, no commentary:
{
  "target_incumbent": "The exact name of the real incumbent you are sniping",
  "name": "Distinctive FUN brand name (1-3 words) — invented word or vivid metaphor, not the pricing model",
  "tagline": "One-line wedge, max 100 chars, lowercase, concrete",
  "description": "2-3 sentences: the incumbent's structural weakness, your wedge, and the why-now catalyst.",
  "market_analysis": "PROVEN demand: cite the incumbent's traction (figures [approx]); who switches and why.",
  "mvp_scope": "Phase 1 = the beachhead. Phase 2, Phase 3 = expand from it.",
  "tech_stack": ["language", "framework", "key-lib"],
  "feasibility_score": 0.70
}
""".strip()


def _build_snipe_prompt(
    category: IdeaCategory,
    angle: str,
    incumbent: str,
    persona: str,
    intel_block: str,
    avoid_list: list[str],
) -> str:
    from project_forge.engine.snipe import _ANGLE_PROMPTS

    avoid_block = "\n".join(avoid_list) if avoid_list else "(none yet)"
    return (
        f"You are a competitive strategist hunting a SNIPE in the "
        f"{category.value} space: a market-PROVEN incumbent with real paying "
        f"demand, and a sharp, focused opening to take a slice.\n\n"
        f"## Incumbent to snipe\n{incumbent}\n\n"
        f"{intel_block}\n\n"
        f"## Persona you're building for\n{persona}\n\n"
        f"## {_ANGLE_PROMPTS[angle]}\n\n"
        f"## Rules\n"
        f"- The incumbent is REAL and its demand is already proven — your job "
        f"is the wedge, not inventing a market.\n"
        f"- Anchor every traction claim in the grounded signal above. Mark any "
        f"number you're unsure of with [approx].\n"
        f"- The pitch must read: incumbent X proves demand → its weakness is Y "
        f"→ we snipe with Z from beachhead B → because now N.\n"
        f"- Stay focused: a beachhead a small team can ship, not a clone of "
        f"the whole incumbent.\n"
        f"- NAME IT WELL — this is a real product, give it a name with "
        f"personality. BANNED: starting the name with 'Flat', naming it after "
        f"the pricing model (no 'Flat-', 'Cheap-', 'Open-'), naming it after "
        f"the incumbent, or generic 'Adjective + Category' (e.g. 'Simple "
        f"Scheduler'). Invent something a founder would proudly launch.\n\n"
        f"## Do NOT produce anything resembling these recent ideas\n"
        f"{avoid_block}\n\n"
        f"## Output\n{_SNIPE_JSON_SCHEMA_INSTRUCTION}\n"
    )


def _incumbent_cache(incumbent: str):
    """Build a per-incumbent FeedCache under the feeds dir, or None if the
    path can't be resolved. 24h TTL — incumbent traction moves slowly."""
    import os
    from datetime import timedelta
    from pathlib import Path

    from project_forge.config import settings
    from project_forge.feeds.cache import FeedCache
    from project_forge.feeds.market_intel import slug

    try:
        env = os.environ.get("FORGE_FEEDS_DIR")
        base = Path(env) if env else Path(settings.db_path).parent / "feeds"
        base = base / "incumbents"
        base.mkdir(parents=True, exist_ok=True)
        return FeedCache(base / f"{slug(incumbent)}.json", ttl=timedelta(hours=24))
    except Exception:  # noqa: BLE001 — cache is best-effort
        return None


async def generate_snipe_llm(
    db: Database,
    category: IdeaCategory,
    *,
    angle: str | None = None,
    incumbent: str | None = None,
    intel: dict | None = None,
    backend: LLMBackend | None = None,
) -> LLMGenerationResult | None:
    """One grounded snipe generation. Picks a real incumbent in this
    category, fetches live HN + GitHub signal, and asks the LLM for a
    competitive-displacement wedge. Returns None when no backend reaches,
    no incumbent is known for the category, or parsing fails.

    `incumbent` / `intel` are injectable for tests; in production they're
    auto-picked and live-fetched (cached per incumbent).
    """
    from project_forge.engine.snipe import SNIPE_ANGLES, pick_least_used_angle
    from project_forge.feeds.market_intel import (
        fetch_incumbent_intel,
        format_intel_for_prompt,
        pick_incumbent,
    )

    backend = backend if backend is not None else resolve_cheap_backend()
    if backend is None:
        return None

    incumbent = incumbent or pick_incumbent(category)
    if not incumbent:
        logger.info("snipe: no incumbent registered for %s", category.value)
        return None

    angle = angle if angle in SNIPE_ANGLES else await pick_least_used_angle(db, category)

    if intel is None:
        intel = fetch_incumbent_intel(incumbent, cache=_incumbent_cache(incumbent))
    intel_block = format_intel_for_prompt(intel)

    persona = _pick_persona(category)
    avoid = await _recent_idea_lines(db, category)
    prompt = _build_snipe_prompt(category, angle, incumbent, persona, intel_block, avoid)

    raw = backend.call(prompt) or ""
    if not raw.strip():
        logger.info("snipe: backend returned empty (incumbent=%s)", incumbent)
        return None

    payload = _parse_idea_payload(raw)
    if payload is None:
        logger.info("snipe: payload parse failed (incumbent=%s)", incumbent)
        return None

    # Angle rides in artifact_type; mode is fixed to 'snipe'.
    idea = _build_idea_from_payload(payload, category, "snipe", artifact_type=angle)
    if idea is None:
        return None
    named = (payload.get("target_incumbent") or incumbent).strip()[:120]
    idea.target_incumbent = named or incumbent

    return LLMGenerationResult(
        idea=idea,
        mode="snipe",
        persona=persona,
        backend=backend.name,
        raw_response=raw,
        artifact_type=angle,
    )


# --------------------------------------------------------------------------- #
# Money board — grounded capital-deployment generation                        #
# --------------------------------------------------------------------------- #


_BOT_JSON_SCHEMA_INSTRUCTION = """
Respond with JSON only — no markdown wrapping, no commentary:
{
  "name": "Short name for the STRATEGY (3-6 words), not a product brand",
  "tagline": "One line, max 100 chars, lowercase: what the bot does and what pays it",
  "description": "2-3 sentences: the venue, the mechanism the income comes from, and why it decays.",
  "market_analysis": "Who else is doing this, how much capital the edge absorbs before it dies.",
  "mvp_scope": "Phase 1 = prove the edge small. Phase 2, Phase 3 = scale and harden.",
  "tech_stack": ["language", "client-library", "key-lib"],
  "feasibility_score": 0.70,
  "bot_spec": {
    "venue": "Exact venue name",
    "venue_url": "Documentation URL for the mechanic being exploited",
    "family": "prediction-markets | crypto-defi | sportsbook | brokerage | other",
    "api_primitives": ["exact API operations the bot calls"],
    "mechanism": "One sentence: where the money comes from",
    "capital_floor_usd": 500,
    "capital_target_usd": 10000,
    "expected_return": "Honest return shape, net of fees — never a guarantee",
    "edge_decay": "Why and when this stops working",
    "kill_criteria": ["conditions under which the bot switches itself off"],
    "validation_plan": ["how to prove the edge on small capital before scaling"],
    "legality_note": "Why this is legitimate under the venue's published terms",
    "human_touchpoints": "What still needs a human, and how often"
  }
}
""".strip()

# Capital written as "$2.5k" / "1,000" / "2 million" — models will not stick
# to a bare number, and dropping the whole spec over formatting would throw
# away a good strategy.
_CAPITAL_SUFFIXES: tuple[tuple[str, float], ...] = (
    ("million", 1_000_000.0),
    ("mm", 1_000_000.0),
    ("m", 1_000_000.0),
    ("thousand", 1_000.0),
    ("k", 1_000.0),
)


def _coerce_capital(value: Any) -> float:
    """Best-effort dollars from whatever the model wrote. 0.0 when unusable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return 0.0
    text = value.strip().lower().replace("$", "").replace(",", "").replace("usd", "").strip()
    multiplier = 1.0
    for suffix, mult in _CAPITAL_SUFFIXES:
        if text.endswith(suffix):
            multiplier = mult
            text = text[: -len(suffix)].strip()
            break
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match is None:
        return 0.0
    try:
        return max(0.0, float(match.group(0)) * multiplier)
    except ValueError:
        return 0.0


def _build_bot_spec(payload: dict[str, Any]) -> BotSpec | None:
    """Build a BotSpec from the model's JSON, repairing what is safely
    repairable and refusing what is not.

    Repairable: capital written as prose, an inverted capital band, a family
    the model invented. Not repairable: no venue, no API surface, no
    mechanism, no decay story, no kill criteria — each of those IS the
    contract this board exists to enforce, and inventing one on the model's
    behalf would defeat the gate."""
    raw = payload.get("bot_spec")
    if not isinstance(raw, dict):
        return None

    try:
        family = BotVenueFamily(str(raw.get("family", "")).strip().lower())
    except ValueError:
        family = BotVenueFamily.OTHER

    floor = _coerce_capital(raw.get("capital_floor_usd"))
    target = _coerce_capital(raw.get("capital_target_usd"))
    # A model that swapped the two still described a real band.
    if target < floor:
        floor, target = min(floor, target), max(floor, target)

    def _strings(key: str) -> list[str]:
        value = raw.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip()[:400] for v in value if str(v).strip()][:12]

    try:
        return BotSpec(
            venue=str(raw.get("venue", "")).strip()[:120],
            venue_url=(str(raw.get("venue_url")).strip()[:400] or None) if raw.get("venue_url") else None,
            family=family,
            api_primitives=_strings("api_primitives"),
            mechanism=str(raw.get("mechanism", "")).strip()[:800],
            capital_floor_usd=floor,
            capital_target_usd=target,
            expected_return=str(raw.get("expected_return", "")).strip()[:600],
            edge_decay=str(raw.get("edge_decay", "")).strip()[:600],
            kill_criteria=_strings("kill_criteria"),
            validation_plan=_strings("validation_plan"),
            legality_note=str(raw.get("legality_note", "")).strip()[:800],
            human_touchpoints=str(raw.get("human_touchpoints", "")).strip()[:400],
        )
    except Exception as exc:  # noqa: BLE001 — validation failure means no spec
        logger.info("bot generation: spec rejected (%s)", str(exc)[:160])
        return None


def _build_bot_prompt(
    category: IdeaCategory,
    persona: str,
    seed: str,
    avoid_list: list[str],
) -> str:
    avoid_block = "\n".join(avoid_list) if avoid_list else "(none yet)"
    return (
        f"{seed}\n\n"
        f"## Persona\n{persona}\n\n"
        f"## Category\nFile this strategy under: {category.value}\n\n"
        f"## Do NOT produce anything resembling these recent strategies\n"
        f"{avoid_block}\n\n"
        f"## Output\n{_BOT_JSON_SCHEMA_INSTRUCTION}\n"
    )


async def generate_bot_llm(
    db: Database,
    category: IdeaCategory,
    *,
    program: dict[str, Any] | None = None,
    primitive: Any = None,
    avoid_lessons: list[str] | None = None,
    backend: LLMBackend | None = None,
) -> LLMGenerationResult | None:
    """One grounded money-bot generation.

    Composes a probed venue program with a known-working mechanism from the
    strategy library and asks for a strategy plus its BotSpec. Returns None
    when no backend reaches, the JSON fails to parse, or the spec is
    unusable — an idea with no spec can never be admitted to the board, so
    half-building one just produces landfill for the gate to reject later.
    """
    from project_forge.feeds.venue_probe import program_to_seed

    # Drafting is the 'generate' role: Sonnet by default on the CLI path.
    # The red team stays on the strongest model — see engine/bot_depth.
    backend = backend if backend is not None else resolve_role_backend("generate")
    if backend is None:
        return None

    if program is None:
        return None

    seed = program_to_seed(program, primitive=primitive, avoid_lessons=avoid_lessons)
    persona = _pick_persona(category)
    avoid = await _recent_idea_lines(db, category)
    prompt = _build_bot_prompt(category, persona, seed, avoid)

    # Off the event loop — see bot_edge for why.
    raw = await asyncio.to_thread(backend.call, prompt) or ""
    if not raw.strip():
        logger.info("bot generation: backend returned empty (venue=%s)", program.get("venue"))
        return None

    payload = _parse_idea_payload(raw)
    if payload is None:
        logger.info("bot generation: payload parse failed (venue=%s)", program.get("venue"))
        return None

    spec = _build_bot_spec(payload)
    if spec is None:
        logger.info("bot generation: no usable BotSpec (venue=%s)", program.get("venue"))
        return None

    idea = _build_idea_from_payload(payload, category, "bot")
    if idea is None:
        return None
    idea.bot_spec = spec

    return LLMGenerationResult(
        idea=idea,
        mode="bot",
        persona=persona,
        backend=backend.name,
        raw_response=raw,
    )
