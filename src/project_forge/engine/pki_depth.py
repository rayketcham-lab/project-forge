"""Multi-pass depth engine for the PKI board — effort, not just selectivity.

`engine/pki.py` decides WHETHER a finding reaches /pki. This module decides
whether it is worth READING once it does.

The admission gate is strong, but everything it admits is still one LLM
call: a paragraph that sounds right, cites an RFC, and that a CA engineer
would nonetheless not act on, because nobody ever tried to break it. So we
break it ourselves, three ways, before it lands:

    real   — is the problem real, is the mechanism correctly characterized
    solved — does existing tooling / an existing standard already handle it
    wrong  — is there a factual or protocol-level error in it

Each lens is a SEPARATE call with its own adversarial prompt. A lens is not
asked to be balanced; it is asked to find the flaw. Then one revise pass
rewrites the draft to answer what survived and to state its own strongest
remaining counterargument out loud. Up to four calls per admitted item —
cheap, because the board admits roughly one item every few hours.

The panel can also kill: a fatal `solved` hit means someone already built
it, and two high-severity hits anywhere mean the draft is not salvageable
by rewording. The cadence drops those.

Two things keep this from being theatre rather than scrutiny:

  - The `solved` lens is handed the prior-art search results, so it is
    ADJUDICATING named repositories that demonstrably exist instead of
    recalling tool names from weights. Recall is the prompt shape that
    invents a plausible tool and kills a good idea with it; adjudication
    is checkable.
  - The `wrong` lens must cite the spec it is correcting. An uncited
    factual objection is an assertion, and is demoted to a nit rather
    than allowed to count toward a kill.

Keyless is the floor, as everywhere else in this engine: with no cheap
backend we make zero calls and hand the idea straight back. The board must
work with no LLM and no network.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea

logger = logging.getLogger(__name__)


# The three attack angles. Order is the order they run in and is stable so
# the prompts (and any downstream logging) stay comparable across fires.
LENSES: tuple[str, str, str] = ("real", "solved", "wrong")

# What counts as a landed hit rather than a nit. Tuned high on purpose: an
# adversarial prompt will always find *something*, and a board that kills on
# 0.4-severity quibbles would never publish.
HIGH_SEVERITY = 0.70

# Two landed hits across lenses means the draft is wrong in more than one
# dimension — a rewrite would be inventing a different idea, not sharpening
# this one.
KILL_OBJECTION_COUNT = 2

# The lens that kills on its own. "Already solved" is fatal in a way the
# others are not: a mischaracterized mechanism can be corrected by the
# revise pass, but an existing tool cannot be revised away.
FATAL_LENS = "solved"

# A unilateral veto needs a higher bar than a vote. The prompt's own rubric
# says 0.7-0.9 is "a serious problem the author must answer" and only 0.9+ is
# "should not exist as written" — and the `solved` brief explicitly invites
# the partial answer ("say exactly which part is already covered"), which is
# what lands at 0.7-0.8. Every PKI idea has partial prior coverage. Reading
# 0.70 as unsalvageable emptied the board on the expected case.
FATAL_SEVERITY = 0.90

# An objection under this is a nit. It may still inform the rewrite, but it
# is never surfaced on the card as "the strongest counterargument" — a 0.05
# quibble presented as the headline objection is exactly the noise this
# board exists to avoid.
OBJECTION_DISPLAY_FLOOR = 0.40

# The `wrong` lens claims a factual error. Without a citation that is an
# assertion, not a correction, so it is capped at nit severity: it still
# reaches the rewrite, but it cannot vote toward a kill and cannot be shown
# as the headline objection.
UNCITED_SEVERITY_CAP = 0.35

# Prior-art repos shown to the `solved` lens. More than this and the prompt
# turns into a directory listing the model skims.
MAX_PRIOR_ART_SHOWN = 5

# Fields the revise pass is allowed to rewrite. Everything else — id,
# category, content_hash, scores, timestamps — is identity and survives
# untouched, so a revision can never fork the idea or orphan its dedup key.
REVISABLE_FIELDS = ("name", "tagline", "description", "mvp_scope", "market_analysis")

# Guard against a model that "revises" by returning a single word or a
# novel. Both shapes are corruption; fall back to the original instead.
_MIN_FIELD_LEN = 8
_MAX_FIELD_LEN = 8000

# Placeholder text models emit when a lens finds nothing. Not an objection.
_NULL_OBJECTIONS = {"none", "n/a", "na", "no objection", "no objections", "null"}


@dataclass
class Objection:
    """One lens's best shot at the draft."""

    lens: str
    severity: float
    text: str


@dataclass
class DepthResult:
    """Outcome of the panel plus revise pass.

    `idea` is the REVISED idea when the draft survived and the revision
    parsed, and the original object otherwise — callers can rely on getting
    a usable Idea back in every case, including keyless."""

    idea: Idea
    objections: list[Objection] = field(default_factory=list)
    strongest: str | None = None
    survived: bool = True
    passes: int = 0
    revised: bool = False


# --------------------------------------------------------------------------- #
# prompts                                                                     #
# --------------------------------------------------------------------------- #


_LENS_BRIEF: dict[str, str] = {
    "real": (
        "ATTACK THE PREMISE. Is this problem actually real, and is the "
        "mechanism correctly characterized? If the draft describes a failure "
        "mode that does not occur in practice, or attributes it to the wrong "
        "cause, say so and name the specific technical error. Do not comment "
        "on novelty or on implementation detail — only on whether the problem "
        "and its mechanism are real as stated."
    ),
    "solved": (
        "ATTACK THE NOVELTY. Is this already adequately handled by existing "
        "tooling, an existing standard, or a trivial workaround? Name the "
        "specific tool, RFC, CA/Browser Forum requirement, library, or "
        "one-liner that solves it. If candidate prior art is listed below, "
        "adjudicate it: say which listed project does this job and which "
        "merely shares the vocabulary. Do NOT name a tool you are not "
        "confident exists — an invented tool name is worse than no "
        "objection, because it kills real work. If the coverage is only "
        "partial, say exactly which part is already covered, and treat "
        "partial coverage as a problem to answer (0.7-0.85), not as fatal. "
        "Do not comment on whether the problem is real — assume it is."
    ),
    "wrong": (
        "ATTACK THE CORRECTNESS. Is there a factual or protocol-level error "
        "here — wrong key or signature sizes, wrong protocol layer, wrong "
        "threat model, a misread of the cited spec, a confusion between "
        "revocation transports, an algorithm that does not do what the draft "
        "says it does? Quote the erroneous claim and state what is actually "
        "true. If the draft states a size, count, or bandwidth figure, "
        "recompute it from first principles and show the arithmetic — "
        "entries x bytes per entry, signatures x signature size — in your "
        "objection. A recomputation that differs by more than 2x is severity "
        "0.7 or higher. Every correction MUST carry a citation: the exact "
        "document and location that establishes what is true (for example "
        "'FIPS 204 Table 2', 'RFC 5280 s5.2.5', 'CA/BF BR 4.9.7'). An "
        "uncited correction is treated as a nit no matter how confident it "
        "sounds. Do not comment on novelty or on whether the problem matters."
    ),
}

# One worked example per lens. Self-reported severity means nothing without
# a calibration anchor — without these the modal reply is a mid-severity
# generic remark that trips no threshold and teaches nobody.
_SEVERITY_ANCHORS: dict[str, str] = {
    "real": (
        "Calibration: 'CRLs are not fetched by browsers at all any more, so "
        "the described outage cannot happen' is 0.9 — the premise does not "
        "occur. 'The draft says operators size shards by hand; large CAs "
        "mostly script it' is 0.3 — true, and it changes nothing."
    ),
    "solved": (
        "Calibration: 'crlite ships exactly this filter-delta distribution in "
        "Firefox today' is 0.9 — the job is done. 'lego renews ACME certs, so "
        "renewal is covered, but nothing there models blast radius' is 0.35 — "
        "adjacent tooling, different job."
    ),
    "wrong": (
        "Calibration: 'the draft assumes 2420-byte ML-DSA signatures; ML-DSA-65 "
        "is 3309 bytes per FIPS 204 Table 2, so the size budget is off by 37%' "
        "is 0.75 — cited, recomputed, material. 'The draft says OCSP when it "
        "means OCSP stapling' is 0.25 — sloppy wording, same mechanism."
    ),
}


def _prior_art_block(prior_art: list[dict] | None) -> str:
    """Named, existing repositories for the `solved` lens to adjudicate.

    Empty when the search found nothing or could not run — and it says which,
    because 'the search found nothing' is evidence of novelty while 'the
    search could not run' is evidence of nothing at all."""
    if not prior_art:
        return (
            "### Candidate prior art\n"
            "(none supplied — a GitHub search either found no close match or "
            "could not run. Do not treat this as proof of novelty.)\n"
        )
    lines = []
    for repo in prior_art[:MAX_PRIOR_ART_SHOWN]:
        name = str(repo.get("name") or "?")
        stars = repo.get("stars") or 0
        desc = str(repo.get("description") or "").strip()[:200]
        lines.append(f"- {name} ({stars} stars): {desc}")
    return (
        "### Candidate prior art (real repositories, found by search)\n"
        + "\n".join(lines)
        + "\nThese are the closest existing projects by vocabulary overlap. "
        "Sharing vocabulary is not doing the same job — say which of these, "
        "if any, actually does it.\n"
    )


def _idea_block(idea: Idea) -> str:
    return (
        f"## {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP:** {idea.mvp_scope}\n"
        f"**Tech:** {', '.join(idea.tech_stack)}\n"
        f"**Anchor:** {idea.pki_anchor or '(none cited)'}\n"
    )


def lens_prompt(lens: str, idea: Idea, prior_art: list[dict] | None = None) -> str:
    """The adversarial prompt for one lens. Public so tests (and future
    probe logging) can inspect exactly what the panel was asked.

    `prior_art` only reaches the `solved` lens — it is the one lens whose
    question ("does this already exist?") has external evidence available,
    and the one whose hallucinations are fatal."""
    evidence = f"{_prior_art_block(prior_art)}\n" if lens == FATAL_LENS else ""
    schema = (
        '{"objection": "<one or two sentences, or \'none\'>", "severity": 0.0-1.0'
        + (', "citation": "<document and section, or empty>"' if lens == "wrong" else "")
        + "}"
    )
    return (
        "You are a senior PKI engineer on a red team. Your job is to find the "
        "flaw in the proposal below, not to be fair to it. A proposal you "
        "cannot fault is rare; say so plainly when that is the case rather "
        "than manufacturing a nit.\n\n"
        f"{_LENS_BRIEF[lens]}\n\n"
        f"{_idea_block(idea)}\n"
        f"{evidence}"
        f"Respond with JSON only: {schema}\n"
        "severity 0.9+ = fatal, the proposal should not exist as written. "
        "0.7-0.9 = a serious problem the author must answer. Below 0.4 = a "
        "nit. Use 'none' with severity 0.0 when this lens finds nothing.\n"
        f"{_SEVERITY_ANCHORS[lens]}"
    )


def revise_prompt(idea: Idea, objections: list[Objection], prior_art: list[dict] | None = None) -> str:
    """The single rewrite pass. Carries the surviving objections verbatim so
    the model answers the actual criticism instead of polishing prose, and
    the named prior art so it differentiates against real projects.

    Only called when the panel actually landed something — a rewrite with no
    criticism to answer cannot improve the draft and can only dilute it."""
    panel = "\n".join(f"- [{o.lens}, severity {o.severity:.2f}] {o.text}" for o in objections)
    return (
        "You are a senior PKI engineer rewriting your own proposal after a "
        "red-team review. Below is the draft and every objection the panel "
        "raised.\n\n"
        "Rewrite the proposal so that it:\n"
        "  1. sharpens the mechanism into something an engineer could start "
        "on Monday — concrete artifacts, concrete failure mode;\n"
        "  2. drops any claim that did not survive the objections;\n"
        "  3. states the STRONGEST remaining counterargument explicitly, in "
        "the description, and answers it or concedes it;\n"
        "  4. differentiates explicitly against any named prior art below.\n\n"
        "Keep the citation/anchor in the description text. Do not inflate: if "
        "the objections shrank the scope, the rewrite should be smaller than "
        "the draft, not bigger.\n\n"
        f"### Draft\n{_idea_block(idea)}\n"
        f"### Panel objections\n{panel}\n\n"
        f"{_prior_art_block(prior_art)}\n"
        'Respond with JSON only: {"name": "...", "tagline": "...", '
        '"description": "...", "mvp_scope": "...", "market_analysis": "...", '
        '"added_specifics": ["<concrete detail this rewrite adds that the '
        'draft did not have>"], "dropped_claims": ["<claim the objections '
        'killed>"]}\n'
        "added_specifics and dropped_claims are not optional and must not "
        "both be empty. A rewrite that adds no specific and drops no claim is "
        "the draft with different adjectives, and it will be discarded."
    )


# --------------------------------------------------------------------------- #
# parsing                                                                     #
# --------------------------------------------------------------------------- #


def _unfence(raw: str) -> str:
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw.strip()


def _load(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(_unfence(raw))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_objection(lens: str, raw: str | None) -> Objection | None:
    """One lens's reply → an Objection, or None when it found nothing or
    replied with something we cannot read. An unreadable lens is a lost
    opinion, never a reason to kill or to abort the panel."""
    data = _load(raw)
    if data is None:
        logger.info("pki depth: lens %s returned unparseable output", lens)
        return None
    text = str(data.get("objection") or "").strip()
    if not text or text.lower().rstrip(".") in _NULL_OBJECTIONS:
        return None
    try:
        severity = float(data.get("severity", 0.0))
    except (TypeError, ValueError):
        severity = 0.0
    severity = max(0.0, min(1.0, severity))
    if severity <= 0.0:
        return None

    if lens == "wrong":
        citation = str(data.get("citation") or "").strip()
        if citation:
            text = f"{text} [{citation}]"
        elif severity > UNCITED_SEVERITY_CAP:
            # An uncited factual correction is the shape that reads most
            # authoritative and is checkable least. Keep it for the rewrite,
            # deny it a vote.
            logger.info("pki depth: uncited 'wrong' objection demoted from %.2f", severity)
            severity = UNCITED_SEVERITY_CAP
    return Objection(lens=lens, severity=severity, text=text)


def _as_text(value: Any) -> str | None:
    """Coerce a revised field to text. Models routinely return `mvp_scope` as
    a JSON list of phases; discarding the whole revision over the container
    type throws away a call for nothing."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return "\n".join(v.strip() for v in value).strip()
    return None


def _nonempty_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _apply_revision(idea: Idea, raw: str | None) -> Idea | None:
    """Revised Idea, or None when the reply is unusable.

    Identity (`id`, `category`, `content_hash`, every score) is carried over
    by construction: only `REVISABLE_FIELDS` are copied out of the reply.

    The rewrite must also declare what it added and what it dropped. Nothing
    downstream can tell "sharpened the mechanism" from "changed three
    adjectives", so the model has to name the delta; a rewrite that names
    none is discarded in favour of the draft. Costs a call, but a silently
    accepted reword is the whole failure mode this stage exists to prevent."""
    data = _load(raw)
    if data is None:
        return None
    updates: dict[str, str] = {}
    for key in REVISABLE_FIELDS:
        value = _as_text(data.get(key))
        if value is None or not (_MIN_FIELD_LEN <= len(value) <= _MAX_FIELD_LEN):
            return None
        updates[key] = value

    if not (_nonempty_list(data.get("added_specifics")) or _nonempty_list(data.get("dropped_claims"))):
        logger.info("pki depth: revision declared no added specifics and no dropped claims; keeping draft")
        return None

    return idea.model_copy(update=updates)


def _killed(objections: list[Objection]) -> bool:
    """The panel's veto. See FATAL_LENS / KILL_OBJECTION_COUNT.

    The fatal lens vetoes alone, so it answers to a higher bar than the vote
    does — the same bar the prompt's own rubric calls "should not exist as
    written"."""
    if any(o.lens == FATAL_LENS and o.severity >= FATAL_SEVERITY for o in objections):
        return True
    return len([o for o in objections if o.severity >= HIGH_SEVERITY]) >= KILL_OBJECTION_COUNT


def _strongest(objections: list[Objection]) -> str | None:
    """The objection worth showing an operator, or None. Sorted input."""
    for obj in objections:
        if obj.severity >= OBJECTION_DISPLAY_FLOOR:
            return obj.text
    return None


# --------------------------------------------------------------------------- #
# entry point                                                                 #
# --------------------------------------------------------------------------- #


async def deepen(idea: Idea, *, prior_art: list[dict] | None = None) -> DepthResult:
    """Run the three-lens panel and the revise pass over an admitted draft.

    `prior_art` is the prior-art search's near-miss list. It grounds the
    `solved` lens: with it, that lens adjudicates repositories that exist;
    without it, it is recalling tool names from weights, which is the prompt
    shape that invents a plausible tool and kills good work with it.

    Returns the revised idea plus the objections it had to survive. With no
    cheap backend this is a no-op that costs nothing and changes nothing —
    the board still publishes, just without the depth."""
    backend = resolve_cheap_backend()
    if backend is None:
        return DepthResult(idea=idea, objections=[], strongest=None, survived=True, passes=0)

    passes = 0
    objections: list[Objection] = []
    for lens in LENSES:
        # `backend.call` is a blocking subprocess (up to 180s each). On the
        # event loop that stalls the dashboard and every other cadence, so it
        # goes to a thread. Sequential on purpose: the lenses are cheap
        # relative to the hourly cadence, and a stable call order keeps the
        # panel comparable across fires.
        raw = await asyncio.to_thread(backend.call, lens_prompt(lens, idea, prior_art))
        passes += 1
        obj = _parse_objection(lens, raw)
        if obj is not None:
            objections.append(obj)

    objections.sort(key=lambda o: o.severity, reverse=True)
    strongest = _strongest(objections)

    if _killed(objections):
        logger.info("pki depth: panel killed %r (strongest: %s)", idea.name, strongest or objections[0].text)
        return DepthResult(
            idea=idea,
            objections=objections,
            strongest=strongest or objections[0].text,
            survived=False,
            passes=passes,
        )

    if not objections:
        # Nothing to answer. A rewrite here has no criticism to work from and
        # can only dilute a draft the panel could not fault.
        return DepthResult(idea=idea, objections=[], strongest=None, survived=True, passes=passes)

    raw_revision = await asyncio.to_thread(backend.call, revise_prompt(idea, objections, prior_art))
    passes += 1
    revised = _apply_revision(idea, raw_revision)

    return DepthResult(
        idea=revised if revised is not None else idea,
        objections=objections,
        # The objection is published as one the proposal answers. When the
        # rewrite never landed, the text below it answers nothing, so there
        # is nothing honest to show.
        strongest=strongest if revised is not None else None,
        survived=True,
        passes=passes,
        revised=revised is not None,
    )


__all__ = [
    "FATAL_LENS",
    "FATAL_SEVERITY",
    "HIGH_SEVERITY",
    "KILL_OBJECTION_COUNT",
    "LENSES",
    "MAX_PRIOR_ART_SHOWN",
    "OBJECTION_DISPLAY_FLOOR",
    "REVISABLE_FIELDS",
    "UNCITED_SEVERITY_CAP",
    "DepthResult",
    "Objection",
    "deepen",
    "lens_prompt",
    "revise_prompt",
]
