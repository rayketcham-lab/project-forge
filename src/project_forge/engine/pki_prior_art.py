"""Prior-art gate for the PKI board — "does this already exist?".

The /pki admission gate already refuses items with no anchor and no
urgency. It never asked the question that actually kills certificate
tooling: somebody shipped this in 2017 and it has four thousand stars.
There are dozens of expiry monitors, ACME clients and chain checkers, and
an item that duplicates one of them is landfill no matter how urgent the
underlying problem is.

This module asks GitHub. Keyless, best-effort, capped at a handful of
requests per check, cached per search term so the hourly cadence does not
re-query the same thing every fire.

Two deliberate biases:

  - It matches on PURPOSE, not domain. A repo that merely mentions
    certificates proves nothing — half of GitHub mentions certificates.
    Overlap on the job being done is what counts, and it is weighted by
    stars: an abandoned 3-star script does not kill an idea, a 4k-star
    maintained tool does.
  - It FAILS OPEN. Any network error, rate limit, or unparseable body
    returns exists=False with a reason saying the check could not run. A
    transient blip must never masquerade as "already exists" and silently
    bin a good finding — the gate above this one is already strict enough
    that false rejections are the expensive failure mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from project_forge.feeds._http import http_get_bytes as _http_get_bytes
from project_forge.models import Idea

logger = logging.getLogger(__name__)


GITHUB_REPO_SEARCH = "https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=5"

# Request budget. Terms are ranked, so the cheapest useful searches run
# first; anything beyond this is diminishing returns against a rate limit
# we share with the gap probe.
MAX_SEARCH_TERMS = 4
MAX_SEARCH_REQUESTS = 3
MAX_QUERY_TOKENS = 4
MAX_MATCHES = 5

# Words that would match half of GitHub. Stripped from search terms so we
# query the job, not the product noun.
# fmt: off
GENERIC_FILLER: frozenset[str] = frozenset(
    {
        "tool", "tooling", "toolkit", "platform", "system", "manager", "management",
        "service", "framework", "solution", "engine", "app", "application", "suite",
        "kit", "hub", "portal", "project", "product", "dashboard", "utility", "helper",
        "library", "server", "client", "api", "cli", "open", "source", "simple", "easy",
        "modern", "fast", "lightweight", "based", "using", "via", "across", "your",
        "that", "this", "with", "from", "into", "onto", "over", "under", "and", "the",
        "for", "are", "all", "any", "new", "one", "its", "it's", "them", "they", "you",
        "when", "what", "which", "while", "before", "after", "without", "within",
    }
)
# fmt: on

# High-signal certificate vocabulary. These survive filler-stripping even
# when short, and they are what makes a query specific enough to be worth
# spending a request on.
# fmt: off
DOMAIN_TOKENS: frozenset[str] = frozenset(
    {
        "crl", "crlite", "ocsp", "stapling", "acme", "x509", "csr", "hsm", "pkcs11",
        "spiffe", "spire", "mtls", "tls", "ssl", "pki", "ca", "certificate",
        "revocation", "expiry", "renewal", "issuance", "rotation", "chain",
        "trust", "anchor", "root", "intermediate", "transparency", "sct", "ct",
        "attestation", "quantum", "pqc", "kyber", "dilithium", "dsa", "kem",
        "hybrid", "keystore", "truststore", "cbom", "signing", "ceremony",
    }
)
# fmt: on

# Domain markers that, on their own, prove only that two things live in the
# same neighbourhood. Overlap here is nearly worthless for prior art.
# fmt: off
_TOPICAL_BASE: frozenset[str] = frozenset(
    {
        "certificate", "x509", "pki", "tls", "ssl", "crypto", "cryptography",
        "security", "secure", "ca", "key", "digital", "identity", "encryption",
    }
)
# fmt: on

# EVERY domain word is topical. This board is entirely about certificates, so
# `ocsp`, `crl`, `hsm`, `ceremony` and friends are the vocabulary two items
# share by virtue of both being PKI, not evidence they do the same job. Left
# out of this set they scored as purpose, and two shared nouns plus a popular
# repo cleared the kill threshold on their own: "PQ Chain Sizer" died on
# {chain, handshake} against a TLS fuzzer, "Ceremony Rehearsal Kit" on
# {ceremony, hsm} against SoftHSM. Neither repo does the job. Domain-vocabulary
# collision is inevitable here and must never be the dominant kill signal.
TOPICAL_TOKENS: frozenset[str] = _TOPICAL_BASE | DOMAIN_TOKENS

# Match weights. Purpose overlap is what we are actually buying; topical
# overlap is a rounding error that only breaks ties.
PURPOSE_TOKEN_WEIGHT = 0.28
TOPICAL_TOKEN_WEIGHT = 0.06
MAX_PURPOSE_TOKENS_COUNTED = 4
MAX_TOPICAL_TOKENS_COUNTED = 2

# Star weighting. Popularity is the cheapest available proxy for "is this
# maintained and would an engineer find it before finding us".
STAR_ABANDONED = 50  # below this: a script, not a tool
STAR_ESTABLISHED = 400  # above this: people actually depend on it
STAR_ABANDONED_FACTOR = 0.45
STAR_MODEST_FACTOR = 0.75
STAR_ESTABLISHED_FACTOR = 1.0

# Verdict thresholds.
MATCH_THRESHOLD = 0.55  # at or above: this idea already exists
MATCH_REPORT_THRESHOLD = 0.30  # worth showing in the probe log either way

# Token normalisation so "certs", "monitoring" and "expiration" collapse
# onto the same concept as their counterparts in a repo description.
_ALIASES: dict[str, str] = {
    "certs": "certificate",
    "cert": "certificate",
    "certificates": "certificate",
    "certificat": "certificate",
    "monitoring": "monitor",
    "monitors": "monitor",
    "expiration": "expiry",
    "expiring": "expiry",
    "expires": "expiry",
    "expire": "expiry",
    "alerting": "alert",
    "alerts": "alert",
    "checker": "check",
    "checking": "check",
    "checks": "check",
    "renewals": "renewal",
    "renewing": "renewal",
    "renew": "renewal",
    "rotating": "rotation",
    "revoke": "revocation",
    "revoked": "revocation",
    "revoking": "revocation",
    "postquantum": "quantum",
    "fleets": "fleet",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Per-term result cache. The cadence fires hourly against a slow-moving
# corpus; re-asking GitHub the same question every hour is pure waste.
# Bounded and FIFO-evicted: uvicorn processes live for weeks, and an
# unbounded dict here would also pin a stale empty result for a term
# forever.
MAX_CACHE_ENTRIES = 256
_SEARCH_CACHE: dict[str, list[dict]] = {}


@dataclass
class PriorArtVerdict:
    """The gate's answer. `exists=True` means a real, maintained tool already
    does this job — the idea should not reach the board."""

    exists: bool
    confidence: float
    matches: list[dict] = field(default_factory=list)
    reason: str = ""


def clear_prior_art_cache() -> None:
    """Drop the per-term cache. Used by tests and by anything that wants a
    genuinely fresh look."""
    _SEARCH_CACHE.clear()


def _normalize(text: str) -> str:
    return (
        (text or "")
        .lower()
        .replace("x.509", "x509")
        .replace("pkcs#11", "pkcs11")
        .replace("post-quantum", "postquantum")
    )


def _tokens(text: str) -> list[str]:
    """Significant, normalised tokens — filler and stopwords removed."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(_normalize(text)):
        token = _ALIASES.get(raw, raw)
        if token in GENERIC_FILLER:
            continue
        if token in DOMAIN_TOKENS:
            out.append(token)
            continue
        if len(token) < 3 or token.isdigit():
            continue
        out.append(token)
    return out


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def extract_search_terms(idea: Idea) -> list[str]:
    """2-4 high-signal GitHub queries derived from the idea's name and
    tagline. Empty when the idea is pure marketing nouns — nothing worth
    spending a request on, and nothing a search could meaningfully match."""
    name = _dedupe(_tokens(idea.name))
    tag = _tokens(idea.tagline)
    domain = _dedupe([t for t in name + tag if t in DOMAIN_TOKENS])
    # Query building uses the NARROW topical set: `crl` is a poor scoring
    # signal but an excellent search term, and dropping it here would retrieve
    # the wrong neighbourhood entirely.
    purpose = _dedupe([t for t in tag if t not in _TOPICAL_BASE])

    # A query built from "certificate tls pki" retrieves the whole
    # neighbourhood; one built from "spiffe hsm crl" retrieves the actual
    # competitors. Specific tokens go first, broad ones only pad it out.
    domain.sort(key=lambda t: t in _TOPICAL_BASE)

    queries: list[str] = []
    if name:
        queries.append(" ".join(name[:MAX_QUERY_TOKENS]))
    if purpose:
        queries.append(" ".join(_dedupe(domain[:2] + purpose)[:MAX_QUERY_TOKENS]))
    if len(domain) >= 2:
        queries.append(" ".join(domain[:MAX_QUERY_TOKENS]))
    return _dedupe(queries)[:MAX_SEARCH_TERMS]


def _star_factor(stars: int) -> float:
    if stars < STAR_ABANDONED:
        return STAR_ABANDONED_FACTOR
    if stars < STAR_ESTABLISHED:
        return STAR_MODEST_FACTOR
    return STAR_ESTABLISHED_FACTOR


def score_match(idea: Idea, repo: dict) -> float:
    """How strongly this repo is prior art for this idea, in [0.0, 1.0].

    Purpose overlap carries the score; shared domain vocabulary barely
    counts, because "also about certificates" describes thousands of
    repos — and on this board, "also about OCSP" describes most of them.
    The result is scaled by popularity: an unmaintained match is evidence
    somebody tried, not evidence the problem is solved."""
    idea_tokens = set(_tokens(idea.name)) | set(_tokens(idea.tagline))
    repo_tokens = set(_tokens(str(repo.get("name") or ""))) | set(_tokens(str(repo.get("description") or "")))
    overlap = idea_tokens & repo_tokens
    if not overlap:
        return 0.0

    topical = overlap & TOPICAL_TOKENS
    purpose = overlap - TOPICAL_TOKENS
    if not purpose:
        return 0.0  # same neighbourhood, different job

    raw = min(len(purpose), MAX_PURPOSE_TOKENS_COUNTED) * PURPOSE_TOKEN_WEIGHT
    raw += min(len(topical), MAX_TOPICAL_TOKENS_COUNTED) * TOPICAL_TOKEN_WEIGHT
    raw = min(1.0, raw)

    stars = repo.get("stars")
    return max(0.0, min(1.0, raw * _star_factor(int(stars) if isinstance(stars, int | float) else 0)))


def _parse_repos(payload: Any) -> list[dict]:
    """GitHub repo-search JSON -> our repo shape.

    Raises ValueError when the body is not a repo-search result — a rate
    limit reply is a well-formed JSON object with a `message` and no
    `items`, and treating that as "zero results" would be a lie the caller
    must not act on."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"not a repo-search result: {str(payload)[:120]}")
    out: list[dict] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        name = (item.get("full_name") or item.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": (item.get("name") or name).strip(),
                "url": item.get("html_url") or "",
                "stars": int(item.get("stargazers_count") or 0),
                "description": (item.get("description") or "").strip()[:400],
            }
        )
    return out


async def _search(term: str, http_get: Callable[..., bytes]) -> list[dict]:
    """One cached GitHub repo search. Raises on any failure so the caller
    can count it as a miss rather than as an empty result."""
    if term in _SEARCH_CACHE:
        return _SEARCH_CACHE[term]
    url = GITHUB_REPO_SEARCH.format(q=quote_plus(term))
    raw = await asyncio.to_thread(http_get, url, timeout=15.0)
    repos = _parse_repos(json.loads(raw.decode("utf-8", errors="replace")))
    while len(_SEARCH_CACHE) >= MAX_CACHE_ENTRIES:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    _SEARCH_CACHE[term] = repos
    return repos


async def check_prior_art(idea: Idea, *, http_get: Callable[..., bytes] = _http_get_bytes) -> PriorArtVerdict:
    """Does a maintained tool already do this job?

    Never raises, and never reports `exists=True` on incomplete evidence:
    if every search failed we say so in the reason and let the idea
    through."""
    terms = extract_search_terms(idea)
    if not terms:
        return PriorArtVerdict(
            exists=False,
            confidence=0.0,
            matches=[],
            reason="prior-art check could not run: no searchable terms in name/tagline",
        )

    repos: dict[str, dict] = {}
    attempted = 0
    succeeded = 0
    for term in terms[:MAX_SEARCH_REQUESTS]:
        attempted += 1
        try:
            found = await _search(term, http_get)
        except Exception as exc:  # noqa: BLE001 — best-effort, fails open
            logger.info("prior-art: search %r unavailable (%s)", term, exc)
            continue
        succeeded += 1
        for r in found:
            repos.setdefault(r["url"] or r["name"], r)

    if succeeded == 0:
        return PriorArtVerdict(
            exists=False,
            confidence=0.0,
            matches=[],
            reason=f"prior-art check could not run: all {attempted} GitHub searches failed",
        )

    scored = sorted(
        ((score_match(idea, r), r) for r in repos.values()),
        key=lambda pair: pair[0],
        reverse=True,
    )
    matches = [r for score, r in scored if score >= MATCH_REPORT_THRESHOLD][:MAX_MATCHES]
    best = scored[0][0] if scored else 0.0

    if best >= MATCH_THRESHOLD:
        top = scored[0][1]
        reason = f"prior art: {top['name']} ({top['stars']} stars) already does this — {top['url']}"
        return PriorArtVerdict(exists=True, confidence=best, matches=matches, reason=reason)

    near = f"; closest was {scored[0][1]['name']} at {best:.2f}" if matches else ""
    reason = f"no prior art found across {succeeded}/{attempted} searches, {len(repos)} repos examined{near}"
    return PriorArtVerdict(exists=False, confidence=best, matches=matches, reason=reason)
