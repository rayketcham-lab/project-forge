"""Venue program probe — the grounding layer under the money board.

The board's rule is that every strategy names a venue and a mechanism a
skeptic could go check. This module is where that citation comes from: it
sweeps the places where venue mechanics are actually announced and returns
candidate PROGRAMS carrying a URL — a maker-fee schedule change, a new
reward budget, a funding mechanic, an incentive campaign, an API primitive
that just became available.

Sources, all keyless and best-effort:

  - GitHub RELEASES for the client SDKs traders actually use. Release notes
    are where "added funding rate endpoint" and "updated maker rebate
    tiers" get written down, usually before a venue's own blog notices.
  - GitHub ISSUE SEARCH across the same repos, filtered to program
    vocabulary. An open issue about undocumented reward qualification is a
    better signal than a marketing page, because it means somebody tried.

Every repo in PROBE_REPOS was verified to exist before being listed, and
each maps to a venue in VENUE_REGISTRY so no candidate is uncitable. That
said, third-party repos get renamed and archived: a source that 404s logs
and yields nothing rather than raising, and the probe as a whole degrades
to [] rather than failing the cadence.

Coverage is honestly uneven. Prediction markets, crypto and brokerage have
maintained open-source clients; sportsbook exchanges largely do not, so
that family is represented in the registry (for venue/docs grounding) with
thin live-probe coverage. `family_probe_coverage()` reports exactly that
rather than letting the gap pass unnoticed.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from project_forge.feeds._http import http_get_bytes as _http_get_bytes
from project_forge.models import BotVenueFamily, IdeaCategory

logger = logging.getLogger(__name__)


class Venue(BaseModel):
    """A place capital can be deployed, and where to read its rules."""

    name: str
    family: BotVenueFamily
    docs_url: str
    # What the operator must confirm on the live docs before funding a bot
    # here — eligibility is not the same question everywhere.
    eligibility_note: str = ""


# The venue universe. Docs URLs are entry points, not deep links: program
# pages move, and a dead deep link reads as authority while being worse
# than nothing. The probe supplies the live citation; this supplies the
# venue's identity and where to start reading.
VENUE_REGISTRY: tuple[Venue, ...] = (
    Venue(
        name="Polymarket",
        family=BotVenueFamily.PREDICTION_MARKETS,
        docs_url="https://docs.polymarket.com/",
        eligibility_note="Jurisdictional eligibility and any market-specific restrictions must be confirmed first.",
    ),
    Venue(
        name="Kalshi",
        family=BotVenueFamily.PREDICTION_MARKETS,
        docs_url="https://trading-api.readme.io/",
        eligibility_note="A regulated US exchange — check account eligibility and API terms before automating.",
    ),
    Venue(
        name="Hyperliquid",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://hyperliquid.gitbook.io/hyperliquid-docs",
        eligibility_note="Perpetuals are restricted in many jurisdictions; confirm access and leverage limits.",
    ),
    Venue(
        name="dYdX",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://docs.dydx.xyz/",
        eligibility_note="Confirm jurisdictional access and the current chain deployment before integrating.",
    ),
    Venue(
        name="Aave",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://aave.com/docs",
        eligibility_note="Smart-contract risk is the dominant risk; read the current audits and risk parameters.",
    ),
    Venue(
        name="Morpho",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://docs.morpho.org/",
        eligibility_note="Market parameters are per-market; read the specific market's risk configuration.",
    ),
    Venue(
        name="Uniswap",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://docs.uniswap.org/",
        eligibility_note="Divergence loss is the core risk for any liquidity position; model it before depositing.",
    ),
    Venue(
        name="CCXT-covered exchanges",
        family=BotVenueFamily.CRYPTO_DEFI,
        docs_url="https://docs.ccxt.com/",
        eligibility_note="A unified client over many venues — each venue's own terms and fee schedule still govern.",
    ),
    Venue(
        name="Alpaca",
        family=BotVenueFamily.BROKERAGE,
        docs_url="https://docs.alpaca.markets/",
        eligibility_note="Brokerage account and any options approval level must be in place before automating.",
    ),
    Venue(
        name="Interactive Brokers",
        family=BotVenueFamily.BROKERAGE,
        docs_url="https://www.interactivebrokers.com/campus/category/ibkr-api-software/",
        eligibility_note="Pattern-day-trading and margin rules apply; confirm account type and permissions.",
    ),
    Venue(
        name="Betfair",
        family=BotVenueFamily.SPORTSBOOK,
        docs_url="https://developer.betfair.com/",
        eligibility_note="An exchange with a published API — jurisdiction and licensing govern eligibility.",
    ),
    Venue(
        name="ProphetX",
        family=BotVenueFamily.SPORTSBOOK,
        docs_url="https://www.prophetx.co/",
        eligibility_note="Confirm whether programmatic placement is permitted by the current terms of service.",
    ),
    Venue(
        name="Novig",
        family=BotVenueFamily.SPORTSBOOK,
        docs_url="https://novig.us/",
        eligibility_note="Confirm whether programmatic placement is permitted by the current terms of service.",
    ),
)

_VENUE_BY_NAME: dict[str, Venue] = {v.name: v for v in VENUE_REGISTRY}

# (repo, venue name). Every repo here returned HTTP 200 when this list was
# written; a later 404 degrades to "source unavailable" and is logged.
PROBE_REPOS: tuple[tuple[str, str], ...] = (
    ("Polymarket/py-clob-client", "Polymarket"),
    ("hyperliquid-dex/hyperliquid-python-sdk", "Hyperliquid"),
    ("dydxprotocol/v4-clients", "dYdX"),
    ("alpacahq/alpaca-py", "Alpaca"),
    ("ccxt/ccxt", "CCXT-covered exchanges"),
    ("aave/aave-v3-core", "Aave"),
    ("morpho-org/morpho-blue", "Morpho"),
    ("Uniswap/v3-core", "Uniswap"),
)

GITHUB_RELEASES = "https://api.github.com/repos/{repo}/releases?per_page=5"
GITHUB_ISSUE_SEARCH = (
    "https://api.github.com/search/issues?q=is:issue+is:open+{terms}+repo:{repo}&sort=updated&order=desc&per_page=5"
)
GITHUB_TERMS = "rewards+OR+rebate+OR+funding+OR+fee+OR+incentive"


# What makes a candidate interesting to a bot builder: a mechanic that pays,
# or an API surface that just changed.
_PROGRAM_WEIGHTS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(reward|rebate|incentive|emission|liquidity program|maker program)\w*\b", re.I), 3),
    (re.compile(r"\b(funding rate|funding payment|basis|carry|borrow rate|lending rate)\b", re.I), 3),
    (re.compile(r"\b(fee (?:tier|schedule|change)|maker fee|taker fee)\b", re.I), 2),
    (re.compile(r"\b(endpoint|websocket|api (?:change|addition)|new method|post[- ]only)\b", re.I), 2),
    (re.compile(r"\b(order book|orderbook|clob|market maker|quoting)\b", re.I), 2),
    (re.compile(r"\b(spread|slippage|price (?:gap|difference)|arbitrage)\b", re.I), 1),
)

# Which of the five bot categories a candidate belongs to. Ordered: the
# first match wins, so the most specific mechanics come first.
_CATEGORY_ROUTES: tuple[tuple[re.Pattern[str], IdeaCategory], ...] = (
    (
        re.compile(r"\b(reward|rebate|incentive|emission|liquidity (?:reward|mining)|points)\w*\b", re.I),
        IdeaCategory.INCENTIVE_CAPTURE,
    ),
    (re.compile(r"\b(funding|basis|carry|borrow rate|lending rate|interest rate)\b", re.I), IdeaCategory.BASIS_CARRY),
    (
        re.compile(r"\b(arbitrage|price (?:gap|difference|discrepancy)|cross[- ]venue|two venues)\b", re.I),
        IdeaCategory.CROSS_VENUE_ARBITRAGE,
    ),
    (
        re.compile(r"\b(maker|quoting|order book|orderbook|clob|post[- ]only|spread)\b", re.I),
        IdeaCategory.MARKET_MAKING,
    ),
    (
        re.compile(r"\b(balance|sweep|collateral|treasury|rebalanc\w+|withdrawal)\b", re.I),
        IdeaCategory.CAPITAL_AUTOMATION,
    ),
)

DEFAULT_CATEGORY = IdeaCategory.CAPITAL_AUTOMATION.value


def _family_of(venue_name: str) -> str:
    venue = _VENUE_BY_NAME.get(venue_name)
    return (venue.family if venue else BotVenueFamily.OTHER).value


def _parse_releases(payload: Any, *, repo: str, venue: str) -> list[dict]:
    """Release notes → candidates. Tolerates any shape that isn't a list."""
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for rel in payload:
        if not isinstance(rel, dict):
            continue
        url = rel.get("html_url") or ""
        if not url:
            continue
        out.append(
            {
                "title": (rel.get("name") or rel.get("tag_name") or "release")[:200],
                "summary": (rel.get("body") or "")[:800],
                "url": url,
                "source": "github-release",
                "repo": repo,
                "venue": venue,
                "family": _family_of(venue),
                "updated": rel.get("published_at") or "",
            }
        )
    return out


def _parse_issues(payload: Any, *, repo: str, venue: str) -> list[dict]:
    """Open issues → candidates. Tolerates any shape that isn't a dict."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for issue in items:
        if not isinstance(issue, dict):
            continue
        url = issue.get("html_url") or ""
        if not url:
            continue
        out.append(
            {
                "title": (issue.get("title") or "")[:200],
                "summary": (issue.get("body") or "")[:800],
                "url": url,
                "source": "github-issue",
                "repo": repo,
                "venue": venue,
                "family": _family_of(venue),
                "updated": issue.get("updated_at") or "",
            }
        )
    return out


def score_program(candidate: dict) -> int:
    """Weighted relevance to the money board. 0 means this is not about a
    mechanism that pays or an API surface that changed, and it is dropped."""
    blob = f"{candidate.get('title', '')} {candidate.get('summary', '')}"
    return sum(weight for pattern, weight in _PROGRAM_WEIGHTS if pattern.search(blob))


def route_category(candidate: dict) -> str:
    """Which of the five bot categories this program belongs to."""
    blob = f"{candidate.get('title', '')} {candidate.get('summary', '')}"
    for pattern, category in _CATEGORY_ROUTES:
        if pattern.search(blob):
            return category.value
    return DEFAULT_CATEGORY


def family_probe_coverage() -> dict[str, int]:
    """How many live probe sources back each venue family.

    Published on purpose: sportsbook exchanges have almost no maintained
    open-source clients, so that family scores near zero here. A silent gap
    would read as "nothing is happening in sportsbook" when the truth is
    "nothing is being watched"."""
    counts: dict[str, int] = {f.value: 0 for f in BotVenueFamily}
    for _repo, venue_name in PROBE_REPOS:
        counts[_family_of(venue_name)] += 1
    return counts


def fetch_venue_programs(
    *,
    http_get: Callable[..., bytes] = _http_get_bytes,
    max_items: int = 20,
) -> list[dict]:
    """Sweep every source and return scored, relevance-filtered candidates,
    highest score first.

    Degrades to [] if everything fails; a partial failure still returns
    whatever the working sources gave us."""
    candidates: list[dict] = []

    for repo, venue in PROBE_REPOS:
        try:
            raw = http_get(GITHUB_RELEASES.format(repo=repo), timeout=15.0)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            candidates.extend(_parse_releases(payload, repo=repo, venue=venue))
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            logger.info("venue probe: releases for %s unavailable (%s)", repo, exc)

        try:
            url = GITHUB_ISSUE_SEARCH.format(terms=GITHUB_TERMS, repo=repo)
            raw = http_get(url, timeout=15.0)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            candidates.extend(_parse_issues(payload, repo=repo, venue=venue))
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            logger.info("venue probe: issues for %s unavailable (%s)", repo, exc)

    scored: list[dict] = []
    for c in candidates:
        s = score_program(c)
        if s <= 0:
            continue  # not about a mechanism that pays
        scored.append({**c, "program_score": s, "category": route_category(c)})

    scored.sort(key=lambda c: c["program_score"], reverse=True)
    return scored[:max_items]


def pick_top_program(candidates: list[dict], *, seen_urls: set[str] | None = None) -> dict | None:
    """The SINGLE program to work this cycle, skipping anything already
    probed. None means a normal quiet hour, not an error."""
    seen = seen_urls or set()
    ordered = sorted(candidates, key=lambda c: c.get("program_score", 0), reverse=True)
    for c in ordered:
        if c.get("url") and c["url"] in seen:
            continue
        return c
    return None


def program_to_seed(
    program: dict,
    *,
    primitive: Any = None,
    avoid_lessons: list[str] | None = None,
) -> str:
    """Turn a probed program plus a known-working mechanism into a seed.

    The composition is the point: the probe says "this venue's mechanics
    just moved", the library says "this mechanism is known to pay", and the
    generator's job is the specific bot at the intersection. Left to itself
    a model writes "AI-powered trading bot", so the seed spells out every
    field the admission gate will demand and names the shapes that are
    refused outright."""
    venue = program.get("venue", "an unnamed venue")
    url = program.get("url", "")
    title = program.get("title", "")
    summary = (program.get("summary") or "")[:600]
    source = program.get("source", "unknown")
    family = program.get("family", BotVenueFamily.OTHER.value)

    # Where the operator can actually trade. Empty by default, in which case
    # generation stays venue-agnostic — but when it is set, saying so up
    # front is far cheaper than letting the legality lens discover it after
    # a full generation and a four-lens panel.
    from project_forge.config import settings

    jurisdiction = (settings.operator_jurisdiction or "").strip()
    jurisdiction_block = ""
    if jurisdiction:
        jurisdiction_block = (
            f"\n## Operator constraint\n"
            f"The operator is based in {jurisdiction}. Only propose a venue and a "
            f"structure they are ELIGIBLE to use from there. If the venue in the "
            f"signal above bars that jurisdiction, say so in one line and propose "
            f"the closest venue that is permitted instead — do not propose a "
            f"strategy the operator cannot legally run.\n"
        )

    # Rejections are the cheapest training signal this board has. The panel
    # kept killing the same two errors — a one-way fee quoted as a round
    # trip, and a capacity claim the reward pool cannot pay — and nothing
    # carried that back into the next generation, so it made them again.
    lessons_block = ""
    if avoid_lessons:
        listed = "\n".join(f"- {lesson}" for lesson in avoid_lessons[:8])
        lessons_block = (
            "\n## Strategies already rejected on this board, and why\n"
            f"{listed}\n"
            "Do not repeat these mistakes, and do not re-propose these "
            "strategies. If your idea shares a failure mode with one of them, "
            "either fix that specific thing explicitly and show the corrected "
            "arithmetic, or propose something else.\n"
        )

    known_block = ""
    if primitive is not None:
        known_block = (
            "\n## A mechanism already known to pay\n"
            f"**{primitive.name}** — {primitive.mechanism}\n"
            f"Return shape: {primitive.yield_shape}\n"
            f"How it decays: {primitive.decay}\n"
            f"Known risks: {'; '.join(primitive.known_risks)}\n"
            "Use this as a starting mechanism, adapt it to the venue above, or "
            "explain in one line why it does not apply here and use a better one.\n"
        )

    return (
        "You are a systematic trader specifying ONE capital-deployment bot. "
        "This is NOT a product, NOT a SaaS, NOT a dashboard, and NOT a service "
        "sold to other traders. It is a bot that deploys the operator's own "
        "capital on a venue and earns from a named mechanism.\n\n"
        f"## Grounding signal (source: {source}, venue family: {family})\n"
        f"Venue: {venue}\n"
        f'Signal: "{title}"\n'
        f"URL: {url}\n"
        f"Context: {summary}\n"
        f"{jurisdiction_block}"
        f"{lessons_block}"
        f"{known_block}\n"
        "## What to produce\n"
        "A specific strategy runnable with little or no human intervention. "
        "Every one of these is mandatory — an answer missing any of them is useless:\n"
        f"1. VENUE + CITATION: name the venue and cite {url} (or the venue's own "
        "documentation page) so the mechanics can be checked before any capital moves.\n"
        "2. API PRIMITIVES: the exact API operations the bot calls — order placement, "
        "book feed, rewards endpoint, claim call. If the venue does not expose what "
        "the strategy needs, say so and pick a different strategy.\n"
        "3. MECHANISM: where the money comes from, in one sentence. A published "
        "rebate, a reward budget, a funding payment, a price difference. If the "
        "answer is 'predicting price better than others', stop — that is not this board.\n"
        "4. CAPITAL: the floor to run it at all and the size where it is worth "
        "running, in dollars.\n"
        "5. RETURN + DECAY: the honest return shape AND why it stops working. Every "
        "real edge decays. An answer with no decay story will be rejected.\n"
        "6. KILL CRITERIA: the conditions under which the bot switches itself off. "
        "Real capital needs a defined stop.\n"
        "7. VALIDATION: how to prove the edge on small capital before scaling — "
        "what to measure, for how long, and what result would kill it.\n"
        "8. LEGALITY: why this is legitimate. It must not depend on market "
        "manipulation, spoofing, wash trading, front-running, non-public "
        "information, exploiting a bug, sybil accounts, or evading a venue's terms. "
        "If the strategy needs any of those, it is out of scope — propose something else.\n\n"
        "## Do the arithmetic before you commit to the strategy\n"
        "This is where these proposals die, every time. Work it out explicitly:\n"
        "- State the maker AND taker fee for EACH leg, as published by the venue.\n"
        "- Compute the ROUND TRIP: entry and exit, on both legs. A position you "
        "open must be closed, so a two-venue strategy pays four fills, not two. "
        "Quoting a one-way cost as a round trip is the single most common error "
        "in this space and it inverts the sign of the result.\n"
        "- Add slippage at the stated capital, and any transfer, gas or funding "
        "cost paid while positioned.\n"
        "- State the expected return NET of all of that, and show the subtraction.\n"
        "- If the mechanism does not clear its own costs at the capital you "
        "named, DO NOT defend it. Say so in one line and propose a different "
        "mechanism or a different venue where it does clear. Walking away from a "
        "strategy that does not work is a correct answer here and a much better "
        "one than an optimistic estimate.\n\n"
        "Be specific and quantitative. Do not claim a risk-free or guaranteed "
        "return; there is no such thing."
    )
