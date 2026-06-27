"""Grounded competitive intel for the Sniper board.

The Sniper board generates ideas that wedge into a *market-proven*
incumbent's demand. To keep the comps honest, every snipe is grounded in
real, fetched signal — not the model's memory:

- **Hacker News (Algolia)** — discussion volume + sentiment about an
  incumbent and "<incumbent> alternative" threads. Points + comments are
  a real proxy for proven demand and for appetite to displace it.
- **GitHub** — open-source challengers to the incumbent, ranked by stars.
  Star counts quantify how much appetite already exists for an alternative.

Both are keyless, stable JSON APIs reached via the shared feed HTTP helper.
Results are cached per incumbent with a TTL (same discipline as the other
feeds) so churn never hammers the sources. Every fetch degrades to empty
on network failure — a snipe with no live signal still generates, it just
carries weaker grounding.

Module shape mirrors the other feeds: pure ``parse_*`` functions + a
``fetch_*`` that writes a cache + a ``format_*_for_prompt`` renderer.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from project_forge.feeds._http import http_get_bytes as _http_get_bytes
from project_forge.models import IdeaCategory

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache

logger = logging.getLogger(__name__)

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

# Curated, real incumbents to snipe — the demand is already proven, the job
# is to find the wedge. Spans the commercial money categories plus the
# operator's IT/security home turf. These seed the live queries; the live
# fetch is what supplies the actual traction + complaint signal.
INCUMBENT_SEEDS: dict[IdeaCategory, list[str]] = {
    IdeaCategory.AUTOMATION_INCOME: [
        "Zapier",
        "Make",
        "ConvertKit",
        "beehiiv",
        "Gumroad",
        "Systeme.io",
        "ActiveCampaign",
        "ClickFunnels",
        "Kajabi",
    ],
    IdeaCategory.CREATOR_TOOLS: [
        "Descript",
        "Riverside",
        "Canva",
        "CapCut",
        "Opus Clip",
        "Substack",
        "Patreon",
        "Buffer",
        "Later",
        "ConvertKit",
    ],
    IdeaCategory.CONSUMER_APP: [
        "Notion",
        "Todoist",
        "Splitwise",
        "MyFitnessPal",
        "Calm",
        "Headspace",
        "Life360",
        "Cozi",
        "YNAB",
    ],
    IdeaCategory.PRODUCTIVITY: [
        "Notion",
        "Asana",
        "Monday.com",
        "ClickUp",
        "Trello",
        "Linear",
        "Calendly",
        "Motion",
        "Superhuman",
        "Obsidian",
    ],
    IdeaCategory.MICRO_SAAS: [
        "Calendly",
        "Typeform",
        "Bitly",
        "Formspree",
        "Cron",
        "Statuspage",
        "Plausible",
        "Crisp",
        "Tally",
    ],
    IdeaCategory.VERTICAL_SAAS: [
        "ServiceTitan",
        "Toast",
        "Mindbody",
        "Jobber",
        "Housecall Pro",
        "Clio",
        "Procore",
        "Dentrix",
        "Vagaro",
        "ShootProof",
    ],
    IdeaCategory.ECOMMERCE_TOOLS: [
        "Shopify",
        "ShipStation",
        "Klaviyo",
        "Gorgias",
        "Loox",
        "Yotpo",
        "Inventory Planner",
        "Triple Whale",
        "Returnly",
    ],
    IdeaCategory.FINTECH_TOOLS: [
        "QuickBooks",
        "FreshBooks",
        "Bill.com",
        "Ramp",
        "Brex",
        "Expensify",
        "Wave",
        "Stripe Billing",
        "Mercury",
        "Plaid",
    ],
    IdeaCategory.SECURITY_TOOL: [
        "Okta",
        "CrowdStrike",
        "1Password",
        "Snyk",
        "Cloudflare",
        "Auth0",
        "HashiCorp Vault",
        "Tenable",
        "Wiz",
        "Tailscale",
    ],
    IdeaCategory.DEVOPS_TOOLING: [
        "Datadog",
        "PagerDuty",
        "GitHub Actions",
        "CircleCI",
        "Terraform",
        "HashiCorp Vault",
        "LaunchDarkly",
        "Vercel",
        "GitLab",
        "New Relic",
    ],
    IdeaCategory.OBSERVABILITY: [
        "Datadog",
        "New Relic",
        "Splunk",
        "Grafana",
        "Honeycomb",
        "Sentry",
        "Sumo Logic",
        "Dynatrace",
        "Elastic",
    ],
    IdeaCategory.COMPLIANCE: [
        "Vanta",
        "Drata",
        "OneTrust",
        "Secureframe",
        "Tugboat Logic",
        "AuditBoard",
        "LogicGate",
        "Hyperproof",
    ],
    IdeaCategory.CRYPTO_INFRASTRUCTURE: [
        "Venafi",
        "DigiCert",
        "HashiCorp Vault",
        "Keyfactor",
        "AppViewX",
        "Entrust",
        "Sectigo",
        "GlobalSign",
        "AWS Certificate Manager",
    ],
    IdeaCategory.PQC_CRYPTOGRAPHY: [
        "DigiCert",
        "Entrust",
        "Venafi",
        "Keyfactor",
        "Thales",
        "Utimaco",
        "ISARA",
        "PQShield",
        "wolfSSL",
    ],
}


def slug(name: str) -> str:
    """Filesystem-safe slug for a per-incumbent cache file."""
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def pick_incumbent(category: IdeaCategory, *, rng: random.Random | None = None) -> str | None:
    """Pick a random real incumbent to snipe in this category, or None."""
    pool = INCUMBENT_SEEDS.get(category)
    if not pool:
        return None
    return (rng or random).choice(pool)


def parse_hn(payload: dict[str, Any], *, max_items: int = 6) -> list[dict]:
    """Convert an HN Algolia search response into demand-signal items."""
    items: list[dict] = []
    for hit in payload.get("hits") or []:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "id": hit.get("objectID", "?"),
                "title": title,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "points": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
                "ts": hit.get("created_at", ""),
            }
        )
    items.sort(key=lambda i: i["points"], reverse=True)
    return items[:max_items]


def parse_github(payload: dict[str, Any], *, max_items: int = 6) -> list[dict]:
    """Convert a GitHub repo-search response into challenger-signal items."""
    items: list[dict] = []
    for repo in payload.get("items") or []:
        full = (repo.get("full_name") or "").strip()
        if not full:
            continue
        items.append(
            {
                "id": full,
                "title": full,
                "url": repo.get("html_url") or f"https://github.com/{full}",
                "stars": int(repo.get("stargazers_count") or 0),
                "summary": (repo.get("description") or "").strip()[:160],
            }
        )
    items.sort(key=lambda i: i["stars"], reverse=True)
    return items[:max_items]


def _get_json(url: str, http_get: Callable[..., bytes]) -> dict[str, Any]:
    """Fetch + parse JSON, degrading to {} on any network/parse failure."""
    try:
        raw = http_get(url, timeout=12.0)
        return json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("market_intel fetch failed for %s: %s", url, exc)
        return {}


def _fetch_hn(name: str, http_get: Callable[..., bytes], hits: int) -> list[dict]:
    q = quote_plus(f"{name} alternative")
    url = f"{HN_SEARCH_URL}?query={q}&tags=story&hitsPerPage={hits}"
    return parse_hn(_get_json(url, http_get), max_items=hits)


def _fetch_github(name: str, http_get: Callable[..., bytes], hits: int) -> list[dict]:
    q = quote_plus(f"{name} alternative")
    url = f"{GITHUB_SEARCH_URL}?q={q}&sort=stars&order=desc&per_page={hits}"
    return parse_github(_get_json(url, http_get), max_items=hits)


def build_intel_bundle(
    name: str,
    *,
    http_get: Callable[..., bytes] = _http_get_bytes,
    hits: int = 6,
) -> dict[str, Any]:
    """Assemble the grounded intel bundle for one incumbent (no cache)."""
    return {
        "incumbent": name,
        "hn": _fetch_hn(name, http_get, hits),
        "oss_challengers": _fetch_github(name, http_get, hits),
    }


def fetch_incumbent_intel(
    name: str,
    *,
    cache: FeedCache | None = None,
    http_get: Callable[..., bytes] = _http_get_bytes,
    hits: int = 6,
) -> dict[str, Any]:
    """Return grounded intel for an incumbent, using the cache when fresh.

    The bundle is stored as a single-element list so it rides the existing
    FeedCache TTL machinery. Only caches when at least one source returned
    signal, so a transient outage doesn't poison the cache with an empty
    bundle.
    """
    if cache is not None:
        cached = cache.read()
        if cached:
            return cached[0]
    bundle = build_intel_bundle(name, http_get=http_get, hits=hits)
    if cache is not None and (bundle["hn"] or bundle["oss_challengers"]):
        cache.write([bundle])
    return bundle


def format_intel_for_prompt(bundle: dict[str, Any]) -> str:
    """Render an intel bundle as grounded evidence lines for the prompt.

    Empty sections are omitted. Returns a short header even when both
    sources are empty so the prompt is honest that grounding was thin.
    """
    name = bundle.get("incumbent", "the incumbent")
    lines = [f"## Grounded market signal for {name} (real, fetched)"]

    hn = bundle.get("hn") or []
    if hn:
        lines.append("### Hacker News discussion (proven demand + complaints)")
        for it in hn:
            lines.append(f'- "{it["title"]}" — {it["points"]} pts, {it["comments"]} comments — {it["url"]}')

    oss = bundle.get("oss_challengers") or []
    if oss:
        lines.append("### Open-source challengers already attracting users (stars = appetite)")
        for it in oss:
            desc = f" — {it['summary']}" if it.get("summary") else ""
            lines.append(f"- {it['title']} — {it['stars']}★{desc} — {it['url']}")

    if not hn and not oss:
        lines.append("(no live signal returned — ground the comp in what you reliably know, mark figures [approx])")

    return "\n".join(lines)


__all__ = [
    "INCUMBENT_SEEDS",
    "build_intel_bundle",
    "fetch_incumbent_intel",
    "format_intel_for_prompt",
    "parse_github",
    "parse_hn",
    "pick_incumbent",
    "slug",
]
