"""PKI urgency scoring — does this finding actually matter to the industry.

The v0.23 PKI board's axis. Where `fundability_score` asks "can we sell
it" and `cashflow_score` asks "how soon is the first invoice",
`pki_urgency_score` asks a different question entirely:

    deadline pressure  x  blast radius  x  how badly today's tooling fails

That is deliberately not a money question. The PKI board exists to surface
work that would move the industry, and the things that move the industry
are the ones with a real clock on them, a wide failure domain, and no
adequate tooling today.

This module does double duty. It is the board's SORT ORDER, and it is the
board's ADMISSION GATE: the hourly probe (`_fire_pki` in the lifespan
scheduler) discards anything scoring below `PKI_ADMIT_THRESHOLD` and
anything with no concrete anchor. Most hours store nothing, which is the
intended behavior — a short list of things that matter beats a long list
of plausible certificate tools.

Two-stage scoring, same shape as fundability/cashflow:

  1. Heuristic (always runs, ~free): deadline / blast-radius / tooling-gap
     / concrete-anchor signals, penalized for hand-wave with no PKI
     substance.
  2. LLM verification (borderline band only): ask the cheap backend for a
     finer score. With no backend configured the heuristic always stands —
     the axis works fully keyless, like everything else in the engine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import PKI_CATEGORIES, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# A clock is ticking: standards deprecations, compliance mandates, forced
# lifetime reductions, hardware that ages out.
_DEADLINE = re.compile(
    r"\b(deadline|deprecat\w+|sunset\w*|end[- ]of[- ]life|eol\b|mandat\w+|"
    r"required by|must (?:migrate|be replaced|support)|phase[- ]out|"
    r"cnsa|20(?:2[6-9]|3[0-5])\b|compliance date|transition period|"
    r"forced (?:migration|reduction)|no longer (?:accepted|trusted|valid))\b",
    re.IGNORECASE,
)

# When it breaks, how much breaks with it.
_BLAST_RADIUS = re.compile(
    r"\b(fleet[- ]wide|fleet|every (?:certificate|endpoint|device|service)|"
    r"internet[- ]scale|organization[- ]wide|enterprise[- ]wide|mass "
    r"(?:revocation|expiry|rotation)|outage|production down|cascading|"
    r"all (?:clients|certificates|endpoints)|cross[- ]vendor|"
    r"supply chain|critical infrastructure|millions of)\b",
    re.IGNORECASE,
)

# The tell that this is real work: today it is done by hand, or not at all.
_TOOLING_GAP = re.compile(
    r"\b(by hand|manual\w*|no tooling|no way to|cannot (?:prove|answer|tell|verify)|"
    r"unautomated|spreadsheet|word document|undocumented|ad[- ]hoc|"
    r"no (?:inventory|visibility|rehearsed|export path|standard)|"
    r"silently (?:fails?|misses|downgrad\w+)|nobody (?:knows|instruments|checks))\b",
    re.IGNORECASE,
)

# Concrete artifacts a finding can be pinned to. Also drives `extract_anchor`,
# which the admission gate uses — an idea with no anchor is a vibe, not a
# finding, and never reaches the board.
_ANCHOR = re.compile(
    r"("
    r"draft-[a-z0-9\-]+"  # IETF internet-draft
    r"|RFC\s?\d{3,5}"  # published RFC
    r"|NIST\s+(?:SP|IR|FIPS)\s?[\d\-.]+"  # NIST publication
    r"|FIPS\s?\d{3}"
    r"|CNSA\s?2\.0"
    r"|CA/?B(?:rowser)?\s+Forum\s+[A-Za-z]*\s?[Bb]allot\s?[\w\-]*"
    r"|SC-?\d{1,3}\b"  # CA/B Forum server-cert ballot shorthand
    r"|CVE-\d{4}-\d{4,7}"
    r"|ML-(?:DSA|KEM)(?:-\d+)?"  # standardized PQ algorithms
    r"|SLH-DSA|LMS|XMSS"
    r"|https?://\S+"  # tracker issue / spec URL
    r")",
    re.IGNORECASE,
)

# Substance check — real PKI vocabulary, not "blockchain for certificates".
_PKI_SUBSTANCE = re.compile(
    r"\b(x\.?509|crl|ocsp|acme|csr|hsm|pkcs#?11|certificate transparency|ct log|"
    r"sct\b|trust store|root program|intermediate|sub[- ]?ca\b|trust anchor|"
    r"cross[- ]sign\w*|name constraint|key ceremony|attestation|mtls|mutual tls|"
    r"spiffe|code[- ]signing|revocation|stapling|path building|chain|"
    r"post[- ]quantum|pqc|hybrid certificate|cbom|crypto[- ]agility)\b",
    re.IGNORECASE,
)

# Hand-wave with no engineering behind it — the shape the board must reject.
_HAND_WAVE = re.compile(
    r"\b(revolutioniz\w+|next[- ]generation|cutting[- ]edge|paradigm|"
    r"seamless\w*|one[- ]click solution|blockchain[- ]based (?:pki|certificate)|"
    r"ai[- ]powered platform|holistic|synerg\w+|game[- ]chang\w+)\b",
    re.IGNORECASE,
)


_CATEGORY_BONUS: dict[IdeaCategory, float] = {
    # Hard clock plus the widest failure domain in the transition.
    IdeaCategory.PQC_MIGRATION: 0.20,
    # Already half-broken, and PQ signature sizes finish the job.
    IdeaCategory.PKI_REVOCATION: 0.18,
    # Highest consequence per mistake, almost entirely unautomated.
    IdeaCategory.CA_OPERATIONS: 0.15,
    # Breaks production today, not in 2030.
    IdeaCategory.CERT_LIFECYCLE: 0.14,
    # Machine identity outgrew the tooling built for human identity.
    IdeaCategory.CERT_IDENTITY: 0.14,
    # Everything else: 0 — this axis is the PKI board's, not universal.
}


# Score band that triggers the LLM second opinion.
LLM_VERIFY_LOWER = 0.35
LLM_VERIFY_UPPER = 0.75

# Admission gate for the hourly probe. An idea must clear this AND carry a
# concrete anchor to reach the board. Tuned so a well-anchored finding in a
# PKI category with two of three urgency signals gets in, and a generic
# certificate-tool pitch does not.
PKI_ADMIT_THRESHOLD = 0.55


def _blob(idea: Idea) -> str:
    return " ".join(
        [
            idea.name or "",
            idea.tagline or "",
            idea.description or "",
            idea.mvp_scope or "",
            idea.market_analysis or "",
        ]
    )


def extract_anchor(idea: Idea) -> str | None:
    """The concrete artifact this idea is pinned to — an RFC or draft name, a
    NIST publication, a CA/B Forum ballot, a CVE, or a spec/tracker URL.

    Returns None when the text cites nothing concrete, which the admission
    gate treats as disqualifying. An explicitly set `pki_anchor` wins over
    anything scraped from the prose."""
    existing = (getattr(idea, "pki_anchor", None) or "").strip()
    if existing:
        return existing
    m = _ANCHOR.search(_blob(idea))
    if m is None:
        return None
    return m.group(1).strip()


def score_pki_urgency_heuristic(idea: Idea) -> float:
    """Cheap, deterministic urgency score in [0.0, 1.0]."""
    score = 0.10  # baseline — being about certificates is not itself urgent

    blob = _blob(idea)

    # A clock is running on it.
    if _DEADLINE.search(blob):
        score += 0.16

    # It fails wide, not narrow.
    if _BLAST_RADIUS.search(blob):
        score += 0.16

    # Today it is manual, or impossible.
    if _TOOLING_GAP.search(blob):
        score += 0.16

    # Pinned to something real that a skeptic could go read.
    if _ANCHOR.search(blob):
        score += 0.12

    # Category bonus + any learned nudge (Scoreboard auto-tune; 0.0 unless
    # opted in via FORGE_SCOREBOARD_AUTOTUNE).
    score += _CATEGORY_BONUS.get(idea.category, 0.0)
    from project_forge.engine.scoreboard import learned_nudge

    score += learned_nudge("pki_urgency", idea.category)

    # No actual PKI vocabulary anywhere: it is a certificate-flavored product
    # pitch, not a finding about the infrastructure.
    if not _PKI_SUBSTANCE.search(blob):
        score -= 0.20

    # Marketing language where engineering should be.
    if _HAND_WAVE.search(blob):
        score -= 0.15

    return max(0.0, min(1.0, score))


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer score when the heuristic is borderline.
    Falls back to the heuristic on any backend / parse failure."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "You are a senior PKI engineer triaging proposed work. Rate this "
        "idea's URGENCY TO THE PKI INDUSTRY on a 0.0-1.0 scale, where the "
        "score is deadline pressure x blast radius x how badly today's "
        "tooling fails.\n\n"
        "1.0 = a dated, standards-driven or operationally forced problem "
        "that breaks a wide class of systems and has no adequate tooling "
        "today. 0.0 = a certificate-flavored product pitch with no clock, "
        "no wide failure domain, and existing tools that already do it.\n\n"
        "Ignore commercial appeal entirely — do NOT reward it for being "
        "sellable. Penalize vagueness: if it does not name a concrete "
        "mechanism, score it low.\n"
        "Respond with JSON only, single key 'score'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP:** {idea.mvp_scope}\n"
        f"**Tech:** {', '.join(idea.tech_stack)}\n\n"
        'Reply: {"score": 0.0-1.0}'
    )
    raw = (backend.call(prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data: dict[str, Any] = json.loads(raw)
        s = float(data["score"])
    except Exception:
        logger.info("pki urgency LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_pki_urgency(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_pki_urgency_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


# --------------------------------------------------------------------------- #
# Admission                                                                   #
# --------------------------------------------------------------------------- #


def admits(idea: Idea, score: float) -> tuple[bool, str]:
    """The PKI board's bar. Returns (admitted, reason).

    Three ways to fail, all of them deliberate:
      - wrong board (not a PKI category)
      - no concrete anchor — nothing a skeptic could go read
      - below the urgency threshold — real, but not industry-moving

    The hourly probe stores NOTHING when this returns False. That is the
    feature: an empty hour is honest, a padded hour is landfill."""
    if idea.category not in PKI_CATEGORIES:
        return False, f"not a PKI category: {idea.category.value}"
    if extract_anchor(idea) is None:
        return False, "no concrete anchor (no RFC/draft/ballot/CVE/URL cited)"
    if score < PKI_ADMIT_THRESHOLD:
        return False, f"urgency {score:.2f} below admit threshold {PKI_ADMIT_THRESHOLD:.2f}"
    return True, "admitted"


# --------------------------------------------------------------------------- #
# Bulk back-fill                                                              #
# --------------------------------------------------------------------------- #


async def score_pending_pki_urgency(db: Database, limit: int = 50) -> dict[str, Any]:
    """Score active PKI-board ideas that don't yet have a pki_urgency_score.

    Scoped to PKI_CATEGORIES *and* `generation_mode = 'pki'` — the axis is the
    board's ranking, not a universal property, and a score is what puts an
    idea on the board. Without the mode filter this cadence was the back
    door: it scored every PKI-category idea the ordinary rotation produced,
    putting ungated content on a board that advertises a hard admission gate.

    Also back-fills `pki_anchor` when the prose cites one. Idempotent;
    returns a summary."""
    placeholders = ",".join("?" * len(PKI_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE pki_urgency_score IS NULL "
        f"AND category IN ({placeholders}) "
        f"AND generation_mode = 'pki' "
        f"AND status NOT IN ('archived', 'rejected') "
        f"ORDER BY generated_at DESC LIMIT ?",
        (*[c.value for c in PKI_CATEGORIES], limit),
    )
    rows = await cur.fetchall()
    scored = 0
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is None:
            continue
        idea.pki_urgency_score = await score_pki_urgency(idea)
        if not idea.pki_anchor:
            idea.pki_anchor = extract_anchor(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
