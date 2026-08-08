"""PKI gap probe — the grounding layer under the hourly /pki cadence.

The PKI board's rule is that every item is pinned to something a skeptic
could go read. This module is where that anchor comes from: it sweeps the
places where PKI problems are actually declared, and returns candidate
gaps carrying a concrete artifact (a draft name, a spec URL, a tracker
issue).

Sources, all keyless and best-effort:

  - IETF Datatracker RSS for the working groups that own this space
    (LAMPS, TLS, ACME, PQUIP) — where certificate and revocation problems
    get written down before there is any tooling for them.
  - GitHub issue search across the implementations that eat the pain
    first (OpenSSL, rustls, cert-manager, step-ca, ...), filtered to
    certificate/revocation/PQ vocabulary.

Every network call degrades to an empty list rather than raising. A probe
that finds nothing is a normal, expected outcome: `_fire_pki` logs it and
stores no idea. That is the whole design — an empty hour is honest.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from project_forge.feeds._http import http_get_bytes as _http_get_bytes

logger = logging.getLogger(__name__)


# IETF working groups that own certificate infrastructure. LAMPS is the
# direct one (PKIX successor); TLS and ACME carry the protocol and issuance
# consequences; PQUIP tracks the post-quantum transition itself.
IETF_WG_FEEDS: tuple[str, ...] = (
    "https://datatracker.ietf.org/feed/wg/lamps/",
    "https://datatracker.ietf.org/feed/wg/tls/",
    "https://datatracker.ietf.org/feed/wg/acme/",
    "https://datatracker.ietf.org/feed/wg/pquip/",
)

# Implementations where certificate pain shows up as filed issues. Scoped
# to open issues so we surface live problems, not settled history.
GITHUB_ISSUE_SEARCH = (
    "https://api.github.com/search/issues?q=is:issue+is:open+{terms}+repo:{repo}&sort=updated&order=desc&per_page=5"
)

PROBE_REPOS: tuple[str, ...] = (
    "cert-manager/cert-manager",
    "smallstep/certificates",
    "openssl/openssl",
    "rustls/rustls",
    "spiffe/spire",
    "sigstore/cosign",
)

GITHUB_TERMS = "certificate+OR+revocation+OR+CRL+OR+OCSP+OR+post-quantum"


# Vocabulary that marks a feed item as genuinely about certificate
# infrastructure rather than incidentally mentioning it. Weighted: the
# things that are both urgent and under-tooled score highest.
_GAP_WEIGHTS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(revocation|crl|ocsp|crlite)\b", re.I), 5),
    (re.compile(r"\b(post[- ]quantum|pqc|ml-dsa|ml-kem|slh-dsa|hybrid)\b", re.I), 5),
    (re.compile(r"\b(certificate|x\.?509|cert chain|chain building)\b", re.I), 3),
    (re.compile(r"\b(acme|issuance|renewal|expir\w+|lifetime)\b", re.I), 3),
    (re.compile(r"\b(hsm|pkcs#?11|key ceremony|trust anchor|root program)\b", re.I), 3),
    (re.compile(r"\b(attestation|mtls|spiffe|code[- ]signing|workload identity)\b", re.I), 2),
    (re.compile(r"\b(deprecat\w+|sunset|migration|transition|deadline)\b", re.I), 2),
    (re.compile(r"\b(size|bloat|too large|overhead|fragment\w+|mtu)\b", re.I), 2),
)

# Routing from probe vocabulary to the board's five categories. First match
# wins, so the more specific patterns are listed first.
_CATEGORY_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(revocation|crl|ocsp|crlite|stapling)\b", re.I), "pki-revocation"),
    (re.compile(r"\b(post[- ]quantum|pqc|ml-dsa|ml-kem|slh-dsa|hybrid|migration)\b", re.I), "pqc-migration"),
    (
        re.compile(r"\b(attestation|mtls|spiffe|workload identity|code[- ]signing|firmware)\b", re.I),
        "cert-identity",
    ),
    (
        re.compile(r"\b(hsm|pkcs#?11|key ceremony|trust anchor|root program|transparency|ct log)\b", re.I),
        "ca-operations",
    ),
    (re.compile(r"\b(acme|issuance|renewal|expir\w+|lifetime|rotation)\b", re.I), "cert-lifecycle"),
)

DEFAULT_CATEGORY = "cert-lifecycle"


def _parse_rss(xml_text: str, *, source: str) -> list[dict]:
    """Feed XML -> gap candidates. Never raises."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 — input is datatracker.ietf.org
    except ET.ParseError as exc:
        logger.warning("PKI probe: failed to parse %s feed: %s", source, exc)
        return []

    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "source": source,
                "title": title,
                "summary": (item.findtext("description") or "").strip()[:1200],
                "url": (item.findtext("link") or "").strip(),
                "ts": (item.findtext("pubDate") or "").strip(),
            }
        )
    # Atom fallback — datatracker serves Atom on some endpoints.
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{ns}entry"):
            title = (entry.findtext(f"{ns}title") or "").strip()
            if not title:
                continue
            link_el = entry.find(f"{ns}link")
            items.append(
                {
                    "source": source,
                    "title": title,
                    "summary": (entry.findtext(f"{ns}summary") or "").strip()[:1200],
                    "url": (link_el.get("href") if link_el is not None else "") or "",
                    "ts": (entry.findtext(f"{ns}updated") or "").strip(),
                }
            )
    return items


def _parse_github_issues(payload: dict[str, Any], *, repo: str) -> list[dict]:
    """GitHub issue-search JSON -> gap candidates."""
    out: list[dict] = []
    for item in (payload or {}).get("items", []) or []:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "source": f"github:{repo}",
                "title": title,
                "summary": (item.get("body") or "").strip()[:1200],
                "url": item.get("html_url") or "",
                "ts": item.get("updated_at") or "",
            }
        )
    return out


def score_gap(candidate: dict) -> int:
    """Weighted relevance of a candidate to the PKI board. 0 means it is not
    really about certificate infrastructure and should be dropped."""
    blob = f"{candidate.get('title', '')} {candidate.get('summary', '')}"
    return sum(weight for pattern, weight in _GAP_WEIGHTS if pattern.search(blob))


def route_category(candidate: dict) -> str:
    """Which of the five PKI categories this gap belongs to."""
    blob = f"{candidate.get('title', '')} {candidate.get('summary', '')}"
    for pattern, category in _CATEGORY_ROUTES:
        if pattern.search(blob):
            return category
    return DEFAULT_CATEGORY


def fetch_pki_gaps(
    *,
    http_get: Callable[..., bytes] = _http_get_bytes,
    max_items: int = 20,
) -> list[dict]:
    """Sweep every source and return scored, relevance-filtered candidates,
    highest score first. Degrades to [] if everything fails; a partial
    failure still returns whatever the working sources gave us."""
    candidates: list[dict] = []

    for url in IETF_WG_FEEDS:
        try:
            raw = http_get(url, timeout=15.0)
            candidates.extend(_parse_rss(raw.decode("utf-8", errors="replace"), source="ietf"))
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            logger.info("PKI probe: IETF source %s unavailable (%s)", url, exc)

    for repo in PROBE_REPOS:
        try:
            url = GITHUB_ISSUE_SEARCH.format(terms=GITHUB_TERMS, repo=repo)
            raw = http_get(url, timeout=15.0)
            candidates.extend(_parse_github_issues(json.loads(raw.decode("utf-8", errors="replace")), repo=repo))
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            logger.info("PKI probe: GitHub source %s unavailable (%s)", repo, exc)

    scored: list[dict] = []
    for c in candidates:
        s = score_gap(c)
        if s <= 0:
            continue  # not actually about certificate infrastructure
        scored.append({**c, "gap_score": s, "category": route_category(c)})

    scored.sort(key=lambda c: c["gap_score"], reverse=True)
    return scored[:max_items]


def pick_top_gap(candidates: list[dict], *, seen_urls: set[str] | None = None) -> dict | None:
    """The SINGLE highest-leverage gap to work this hour, skipping anything
    already probed. Returns None when nothing qualifies — the caller must
    treat that as a normal empty hour, not an error."""
    seen = seen_urls or set()
    for c in candidates:
        if c.get("url") and c["url"] in seen:
            continue
        return c
    return None


def gap_to_seed(gap: dict) -> str:
    """Turn a gap into a generation seed that demands a spec-grade answer.

    Deliberately heavy. The board's whole premise is that one well-worked
    item per hour beats twenty pitches, so the seed insists on the concrete
    artifact, the failure mechanism, the tooling gap, and a validation
    plan — and explicitly forbids the generic-product shape."""
    title = gap.get("title", "")
    url = gap.get("url", "")
    summary = (gap.get("summary") or "")[:600]
    source = gap.get("source", "unknown")

    return (
        "You are a senior PKI engineer proposing ONE piece of work that would "
        "materially help the certificate-infrastructure industry.\n\n"
        f"## Grounding signal (source: {source})\n"
        f'Title: "{title}"\n'
        f"URL: {url}\n"
        f"Context: {summary}\n\n"
        "## What to produce\n"
        "Propose a concrete tool, protocol, or system that addresses a REAL "
        "gap this signal points at. Requirements, all mandatory:\n"
        f"1. ANCHOR: cite the concrete artifact explicitly — reference {url} "
        "and any draft name, RFC number, ballot, or CVE it involves. An item "
        "with no citable anchor is worthless here.\n"
        "2. MECHANISM: state precisely what breaks, technically. Name the "
        "protocol, the data structure, the size or timing constraint. Not "
        "'certificates are hard' — say what fails and at what scale.\n"
        "3. TOOLING GAP: state what engineers do about this TODAY, and why "
        "that is inadequate. If good tooling already exists, say so and pick "
        "a different angle.\n"
        "4. BLAST RADIUS: who and how much breaks when this goes wrong.\n"
        "5. VALIDATION: how someone would prove within a week that this "
        "problem is real and the approach works.\n\n"
        "Reject your own idea if it is a generic certificate-management "
        "dashboard, a 'blockchain for PKI' pitch, or anything a commercial "
        "product already does well. Depth over novelty: one rigorous, "
        "buildable proposal, written for an audience that runs a CA."
    )
