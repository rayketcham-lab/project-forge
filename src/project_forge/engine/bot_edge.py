"""Bot-edge scoring — does this strategy actually survive contact with a venue.

The v0.24 money board's axis. Where `fundability_score` asks "can we sell
it" and `cashflow_score` asks "how soon is the first invoice",
`bot_edge_score` asks:

    named venue  x  real API surface  x  a mechanism that pays  x
    an honest decay story  x  runs unattended  x  legal on its face

That is not a product question, which is the entire point. The old money
board ranked by fundability, so it surfaced SaaS pitches — a dashboard for
traders scores well on "can we sell it" and has no edge at all. This axis
refuses that shape by construction.

Like `engine.pki`, it does double duty: the board's SORT ORDER and its
ADMISSION GATE. A draft that cannot name where the money comes from, or
that has no BotSpec, or that would only work by manipulating a market, is
never stored.

Two-stage scoring, same as every other axis here:

  1. Heuristic (always runs, ~free): venue / API / mechanism / capital /
     decay / kill-switch signals, penalized for product shape, hand-wave
     alpha, and free-lunch claims.
  2. LLM verification (borderline band only). With no backend configured
     the heuristic always stands — the axis works fully keyless.

The hard veto sits in front of both. Manipulation, non-public information,
bug exploitation, sybil farming, and KYC evasion score 0.0 and are refused
outright, no matter how well-specified they are. A NEGATED mention is
explicitly allowed through, because the honest way to describe a market
maker includes the words "no spoofing".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import MONEY_CATEGORIES, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# Venues the engine knows by name. Not exhaustive and not meant to be — it
# is corroboration that the text names a real place to send an order, not a
# whitelist of where capital may go.
_VENUE_NAMES: tuple[str, ...] = (
    # prediction markets
    "Polymarket",
    "Kalshi",
    "Manifold",
    "PredictIt",
    # crypto venues and protocols
    "Hyperliquid",
    "dYdX",
    "Binance",
    "Coinbase",
    "Kraken",
    "OKX",
    "Bybit",
    "Deribit",
    "Drift",
    "GMX",
    "Aave",
    "Compound",
    "Morpho",
    "Uniswap",
    "Curve",
    "Lido",
    "Jito",
    # sportsbook exchanges
    "ProphetX",
    "Novig",
    "Betfair",
    "Pinnacle",
    "Sporttrade",
    # brokerage
    "Interactive Brokers",
    "IBKR",
    "Alpaca",
    "Tradier",
    "Tastytrade",
    "Schwab",
)

_VENUE_RE = re.compile(r"\b(" + "|".join(re.escape(v) for v in _VENUE_NAMES) + r")\b", re.IGNORECASE)

# Evidence there is a real API surface, not an aspiration to trade.
_API_SURFACE = re.compile(
    r"\b(rest api|websocket|ws feed|endpoint|order (?:placement|entry|api)|"
    r"post[- ]only|clob|fix (?:api|protocol)|sdk|rpc|smart contract call|"
    r"place(?:s|d)? (?:a )?(?:limit|market|maker) order|cancel[- /]replace|"
    r"api key|rate limit|order book feed)\b",
    re.IGNORECASE,
)

# Where the money comes from. A strategy that cannot match any of these is
# not describing a mechanism, it is describing a hope.
_YIELD_SOURCE = re.compile(
    r"\b(rebate|reward(?:s| budget| pool| minute)?|incentive|emissions|"
    r"funding (?:rate|payment)|basis|carry|spread capture|maker fee|"
    r"liquidity (?:reward|mining|budget)|borrow rate|lending rate|"
    r"interest rate spread|fee tier|staking reward|premium|"
    r"price (?:gap|difference|discrepancy)|arbitrage|convergence)\b",
    re.IGNORECASE,
)

# Capital is named as a number, not implied.
_CAPITAL = re.compile(
    r"(\$\s?\d[\d,._]*\s?(?:k|m|thousand|million)?|\b\d[\d,]*\s?(?:usd|usdc|dollars)\b)", re.IGNORECASE
)

# The edge is admitted to be temporary.
_DECAY = re.compile(
    r"\b(decay\w*|dilut\w+|compress\w+|crowd\w+|competitors? arrive|"
    r"pro[- ]rata|shrinks?|erodes?|taper\w*|falls? as|declin\w+|"
    r"schedule ends|program ends|no longer pays)\b",
    re.IGNORECASE,
)

# It can be switched off without a human watching it.
_RISK_CONTROL = re.compile(
    r"\b(kill switch|kill[- ]criteria|stop[- ]loss|drawdown limit|position limit|"
    r"inventory (?:limit|cap)|liquidation (?:distance|monitor)|circuit breaker|"
    r"halts?|unwinds?|flattens?|max exposure|risk cap)\b",
    re.IGNORECASE,
)

# The shape the old board produced. Present tense of "this is a product".
_PRODUCT_SHAPE = re.compile(
    r"\b(saas|subscription|subscribers?|per[- ]seat|monthly plan|"
    r"dashboard for|platform (?:for|that helps)|marketplace for|"
    r"customers pay|pricing tiers?|free trial|onboarding flow|"
    r"churn rate|mrr\b|arr\b|sign[- ]?ups?)\b",
    re.IGNORECASE,
)

# Alpha by assertion.
_HAND_WAVE = re.compile(
    r"\b(ai[- ]powered|machine learning (?:model )?predicts?|proprietary algorithm|"
    r"secret sauce|revolutionar\w+|cutting[- ]edge|next[- ]generation|"
    r"predicts? (?:the )?market direction|beats the market|alpha[- ]generating model)\b",
    re.IGNORECASE,
)

_FREE_LUNCH = re.compile(
    r"\b(guaranteed (?:return|profit|income)|risk[- ]free|riskless|no risk|"
    r"cannot lose|can't lose|always profitable|free money)\b",
    re.IGNORECASE,
)

# The hard veto. These are not "risky" — they are how you get charged, sued,
# or banned, and none of them belong on a board the operator might build from.
_ILLEGAL = re.compile(
    r"\b(spoof\w*|wash trad\w+|layering\b|painting the tape|"
    r"front[- ]run\w*|insider (?:information|trading|tip)|non[- ]public information|"
    r"market manipulation|manipulat\w+ the (?:price|market|book)|"
    r"pump[- ]and[- ]dump|ramp\w* the price|"
    r"exploit\w*\s+(?:a|an|the)?\s*(?:\w+\s+)?(?:bug|vulnerability|flaw|exploit)\b|"
    r"sybil\w*|multi[- ]account\w* to (?:farm|claim)|"
    r"bypass\w* kyc|evad\w* kyc|circumvent\w* (?:kyc|aml|geoblock\w*)|"
    r"nominee accounts?|unlicensed money transmission)\b",
    re.IGNORECASE,
)

# Cues that a veto phrase is being DISCLAIMED rather than proposed, so
# "no spoofing" and "would be wash trading, which is out of scope" read as
# the honesty they are.
#
# The window is deliberately tight (30 chars). A wider one swallowed real
# intent: "places orders it never intends to fill to move the price —
# spoofing the book" carries a negation 40-odd characters upstream that
# negates something else entirely. A disclaimer sits next to the word it
# disclaims; an unrelated "never" does not.
_NEGATION = re.compile(
    r"\b(no|not|never|without|avoids?|avoiding|prohibits?|forbids?|excludes?|"
    r"refuses?|rather than|instead of|would be|would constitute|explicitly not|"
    r"rules? out)\b[^.;]{0,30}$",
    re.IGNORECASE,
)

# Per-category nudge. Small on purpose: the axis is about the strategy's
# substance, not which drawer it was filed in.
_CATEGORY_BONUS: dict[IdeaCategory, float] = {
    # Published budgets, public rules — the most verifiable yield source.
    IdeaCategory.INCENTIVE_CAPTURE: 0.06,
    # Real, durable, and the venue is on your side.
    IdeaCategory.MARKET_MAKING: 0.05,
    # A published payment for a nameable risk.
    IdeaCategory.BASIS_CARRY: 0.05,
    # Boring and reliable; low ceiling, which the score should reflect.
    IdeaCategory.CAPITAL_AUTOMATION: 0.03,
    # Real but the most contested and the most often mis-specified.
    IdeaCategory.CROSS_VENUE_ARBITRAGE: 0.02,
}

# Score band that triggers the LLM second opinion.
LLM_VERIFY_LOWER = 0.35
LLM_VERIFY_UPPER = 0.75

# Admission gate for the bot cadence. Tuned so a strategy naming its venue,
# its API calls, its yield source and its decay gets in, while a plausible
# trading-flavored paragraph does not.
BOT_ADMIT_THRESHOLD = 0.55


def _blob(idea: Idea) -> str:
    """Everything the scorer reads — prose plus the spec, since a spec field
    is exactly where a venue or a kill criterion is most likely to live."""
    parts = [
        idea.name or "",
        idea.tagline or "",
        idea.description or "",
        idea.mvp_scope or "",
        idea.market_analysis or "",
    ]
    spec = getattr(idea, "bot_spec", None)
    if spec is not None:
        parts += [
            spec.venue,
            spec.venue_url or "",
            spec.mechanism,
            spec.expected_return,
            spec.edge_decay,
            spec.legality_note,
            spec.human_touchpoints,
            " ".join(spec.api_primitives),
            " ".join(spec.kill_criteria),
            " ".join(spec.validation_plan),
        ]
    return " ".join(parts)


def illegal_reason(idea: Idea) -> str | None:
    """The veto. Returns why this must never be stored, or None if clean.

    Every match is checked for a negation cue in the 40 characters before
    it: a spec that says "no spoofing, no orders intended not to trade" is
    describing discipline, and refusing it would push generation toward
    saying nothing about manipulation at all — the opposite of the goal.
    """
    blob = _blob(idea)
    for match in _ILLEGAL.finditer(blob):
        run_up = blob[max(0, match.start() - 40) : match.start()]
        if _NEGATION.search(run_up):
            continue
        return f"not legitimate: mechanism relies on {match.group(0).lower()}"
    return None


def extract_venue(idea: Idea) -> str | None:
    """The venue this strategy deploys capital on.

    The spec wins when present; otherwise the prose is scanned for a venue
    the engine knows by name. None means nobody could tell where the orders
    would go, which the gate treats as disqualifying."""
    spec = getattr(idea, "bot_spec", None)
    if spec is not None and (spec.venue or "").strip():
        return spec.venue.strip()
    match = _VENUE_RE.search(_blob(idea))
    if match is None:
        return None
    return match.group(1)


def score_bot_edge_heuristic(idea: Idea) -> float:
    """Cheap, deterministic edge score in [0.0, 1.0]."""
    if illegal_reason(idea) is not None:
        return 0.0

    blob = _blob(idea)
    spec = getattr(idea, "bot_spec", None)
    score = 0.08  # baseline — being about markets is not itself an edge

    # Somewhere to actually send the order.
    if extract_venue(idea) is not None:
        score += 0.14

    # An API surface, from the spec or named in the prose.
    if (spec is not None and spec.api_primitives) or _API_SURFACE.search(blob):
        score += 0.14

    # Where the money comes from.
    if _YIELD_SOURCE.search(blob):
        score += 0.16

    # How much capital, as a number.
    if (spec is not None and spec.capital_floor_usd > 0) or _CAPITAL.search(blob):
        score += 0.09

    # Admits the edge is temporary. The single strongest honesty signal.
    if _DECAY.search(blob):
        score += 0.12

    # Can be switched off unattended.
    if (spec is not None and spec.kill_criteria) or _RISK_CONTROL.search(blob):
        score += 0.10

    # A plan to prove it small before scaling.
    if spec is not None and spec.validation_plan:
        score += 0.05

    # Matches a mechanism already known to pay.
    if _matches_known_primitive(blob):
        score += 0.06

    score += _CATEGORY_BONUS.get(idea.category, 0.0)

    from project_forge.engine.scoreboard import learned_nudge

    score += learned_nudge("bot_edge", idea.category)

    # It is a product, not a bot. The failure mode this board exists to end.
    if _PRODUCT_SHAPE.search(blob):
        score -= 0.28

    # Alpha asserted rather than mechanised.
    if _HAND_WAVE.search(blob):
        score -= 0.18

    # Claims that cannot be true of any real edge.
    if _FREE_LUNCH.search(blob):
        score -= 0.20

    return max(0.0, min(1.0, score))


def _matches_known_primitive(blob: str) -> bool:
    """True when the text describes a mechanism the strategy library already
    documents. Corroboration, not a requirement — a genuinely new edge should
    still be able to score."""
    from project_forge.engine.strategy_library import STRATEGY_LIBRARY

    lowered = blob.lower()
    for prim in STRATEGY_LIBRARY:
        words = [w for w in prim.key.split("-") if len(w) > 3]
        if words and all(w in lowered for w in words):
            return True
    return False


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer score when the heuristic is borderline.
    Falls back to the heuristic on any backend / parse failure."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    spec = getattr(idea, "bot_spec", None)
    spec_block = ""
    if spec is not None:
        spec_block = (
            f"\n**Venue:** {spec.venue}\n"
            f"**API primitives:** {', '.join(spec.api_primitives)}\n"
            f"**Mechanism:** {spec.mechanism}\n"
            f"**Capital:** ${spec.capital_floor_usd:,.0f} floor / ${spec.capital_target_usd:,.0f} target\n"
            f"**Edge decay:** {spec.edge_decay}\n"
            f"**Kill criteria:** {'; '.join(spec.kill_criteria)}\n"
        )
    prompt = (
        "You are a systematic trader reviewing a proposed capital-deployment "
        "bot. Rate its EDGE on a 0.0-1.0 scale: does the described mechanism "
        "survive fees, slippage, competition and capacity, and can a bot run "
        "it unattended?\n\n"
        "1.0 = names a real venue and the exact API calls, the income has a "
        "verifiable source (a published rebate, reward budget, funding "
        "payment or price difference), the capital requirement is stated, and "
        "it admits how the edge decays. 0.0 = a trading-flavored product "
        "pitch, an assertion of alpha with no mechanism, or a claim of "
        "risk-free returns.\n\n"
        "Do NOT reward commercial appeal — this is not a product. Penalize "
        "any strategy whose profitability depends on being faster than "
        "professional market makers, and penalize anything that omits fees.\n"
        "Respond with JSON only, single key 'score'.\n\n"
        f"## Strategy: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**MVP:** {idea.mvp_scope}\n"
        f"{spec_block}\n"
        'Reply: {"score": 0.0-1.0}'
    )
    # Off the event loop: the CLI backend shells out to `claude --print`,
    # which blocks for tens of seconds and would freeze every request the
    # web app is serving while it runs.
    raw = (await asyncio.to_thread(backend.call, prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data: dict[str, Any] = json.loads(raw)
        s = float(data["score"])
    except Exception:
        logger.info("bot edge LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_bot_edge(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band.

    A vetoed strategy short-circuits to 0.0 without ever reaching the
    backend — there is nothing to adjudicate about front-running."""
    if illegal_reason(idea) is not None:
        return 0.0
    heuristic = score_bot_edge_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


# --------------------------------------------------------------------------- #
# Admission                                                                   #
# --------------------------------------------------------------------------- #


def admits(idea: Idea, score: float) -> tuple[bool, str]:
    """The money board's bar. Returns (admitted, reason).

    Five ways to fail:
      - wrong board (not a bot category)
      - not legitimate (the hard veto)
      - no BotSpec — the strategy was never actually specified
      - nothing to verify against: no venue URL and no validation plan, so
        neither the operator nor a skeptic can check it before funding it
      - below the edge threshold — plausible, but not an edge

    The cadence stores NOTHING when this returns False."""
    if idea.category not in MONEY_CATEGORIES:
        return False, f"not a bot category: {idea.category.value}"

    veto = illegal_reason(idea)
    if veto is not None:
        return False, veto

    spec = getattr(idea, "bot_spec", None)
    if spec is None:
        return False, "no BotSpec — venue, API surface and mechanism were never specified"

    if not (spec.venue_url or spec.validation_plan):
        return False, "nothing to verify against: no venue URL and no validation plan"

    if score < BOT_ADMIT_THRESHOLD:
        return False, f"edge {score:.2f} below admit threshold {BOT_ADMIT_THRESHOLD:.2f}"

    return True, "admitted"


# --------------------------------------------------------------------------- #
# Bulk back-fill                                                              #
# --------------------------------------------------------------------------- #


async def score_pending_bot_edge(db: Database, limit: int = 50) -> dict[str, Any]:
    """Score active bot-board ideas that don't yet have a bot_edge_score.

    Scoped to MONEY_CATEGORIES *and* `generation_mode = 'bot'` — same
    reasoning as the PKI back-fill. A score is what puts an idea on the
    board, so scoring whatever the ordinary rotation happened to file under
    a bot category would be a back door around the admission gate.

    Idempotent; returns a summary."""
    placeholders = ",".join("?" * len(MONEY_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE bot_edge_score IS NULL "
        f"AND category IN ({placeholders}) "
        f"AND generation_mode = 'bot' "
        f"AND status NOT IN ('archived', 'rejected') "
        f"ORDER BY generated_at DESC LIMIT ?",
        (*[c.value for c in MONEY_CATEGORIES], limit),
    )
    rows = await cur.fetchall()
    scored = 0
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is None:
            continue
        idea.bot_edge_score = await score_bot_edge(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
