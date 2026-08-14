"""The red team for money-bot strategies.

Selectivity is not enough on its own. A gate that admits one draft in five
still admits a single LLM pass: a paragraph that names a venue and a
mechanism and that no trader would fund, because nobody tried to break it.
This module tries to break it.

Four lenses, because these strategies die in four distinct ways and a
generic "is this good?" reviewer catches none of them:

  arithmetic  — the costs eat it. Fees on both legs, slippage at working
                size, adverse selection, gas, funding. Recompute the P&L.
  competition — the edge is already gone, or has no capacity. Who is
                already doing this, and how many dollars does it absorb
                before the return stops being worth the risk?
  legality    — the venue's terms forbid automation, the operator is not
                eligible, the strategy needs something a regulator would
                call manipulation, or it depends on a licence nobody has.
  operations  — it cannot run unattended. Partial fills, API outages, a
                venue halt mid-position, key handling, what happens when
                the hedge leg fails at 3am.

A draft is knocked down by one FATAL hit (>= 0.85) or by two LANDED hits
(>= 0.60) — being wrong twice is a pattern. But knocked down is not dead:
in practice almost every real kill is "your arithmetic is wrong" or "that
return is asserted, not derived", and a draft can answer that honestly by
restating a smaller, correct number. So a knocked-down draft gets ONE
rewrite, and then the lens that hit hardest is re-asked against the
rewrite. Still fatal, or no usable rewrite → dead. That keeps the panel's
teeth (a rewrite has to survive the same attack) without throwing away a
real strategy over a bad estimate.

A revision may not touch the name, venue, mechanism or API primitives:
those are the strategy's identity and its dedup key, and a rewrite that
changes them has escaped the review it just had.

Anything that survives keeps its strongest objection, which is written into
the spec and published on the card. A strategy that admits its own weakest
point is worth more to whoever funds it than one that pretends the
objection was never raised.

Keyless this is a free no-op: no backend, no panel, survives untouched.
Nothing here mutates the idea's identity fields, so dedup keys are stable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from project_forge.engine.llm_backend import resolve_role_backend
from project_forge.models import Idea

logger = logging.getLogger(__name__)

LENSES: tuple[str, str, str, str] = ("arithmetic", "competition", "legality", "operations")

# A single objection this severe kills the draft outright.
FATAL_SEVERITY = 0.85
# Two objections at or above this kill it together.
LANDED_SEVERITY = 0.60
# Below this, an objection is noise and is not even published.
NOISE_SEVERITY = 0.30

_NULL_OBJECTIONS = {"none", "n/a", "na", "no objection", "no objections", "null", ""}


@dataclass
class Objection:
    """One lens's best shot at the strategy."""

    lens: str
    severity: float
    text: str


@dataclass
class StressResult:
    """Outcome of the panel.

    `idea` is always a usable Idea — the same object, with the surviving
    objection written into its spec when one landed."""

    idea: Idea
    objections: list[Objection] = field(default_factory=list)
    strongest: str | None = None
    survived: bool = True
    passes: int = 0
    # True when the draft was killed by the panel, rewritten to answer the
    # objections, and the re-check accepted the rewrite.
    revised: bool = False
    # True when the review could not be completed — a backend call failed or
    # timed out. Distinct from `survived=False`, which means the panel
    # actually judged it. A stopwatch is not an objection.
    incomplete: bool = False


_LENS_BRIEF: dict[str, str] = {
    "arithmetic": (
        "ATTACK THE ARITHMETIC. Do the costs eat this edge? Recompute the "
        "economics from first principles and SHOW THE WORKING: fees on every "
        "leg, slippage at the stated capital, adverse selection on passive "
        "fills, gas or transfer costs, funding paid while positioned. State "
        "the breakeven the strategy must clear and whether the described "
        "mechanism clears it. If the draft quotes a return without netting "
        "fees, that alone is a landed objection. Do not comment on legality "
        "or competition — only on whether the numbers work."
    ),
    "competition": (
        "ATTACK THE EDGE'S EXISTENCE AND CAPACITY. Is this already arbitraged "
        "away, and how much capital can it actually absorb? Name who is "
        "already doing it — professional market makers, existing bots, the "
        "venue's own liquidity partners. State the capacity ceiling in "
        "dollars: at what size does the return stop being worth the risk? A "
        "strategy that only works at trivial size, or that requires beating "
        "professionals on latency, is a landed objection. Do not comment on "
        "the arithmetic or on legality."
    ),
    "legality": (
        "ATTACK THE LEGITIMACY. Does this actually clear the venue's terms and "
        "the law? Consider: does the venue permit programmatic access at all; "
        "is the operator plausibly eligible in their jurisdiction; would a "
        "regulator characterise any part of this as manipulation, wash "
        "trading, or trading on non-public information; does it require a "
        "licence (money transmission, broker-dealer, gambling) nobody has; "
        "does it depend on exploiting a bug or on multiple accounts a venue "
        "forbids. If the strategy is fine but the draft never checked the "
        "terms of service, say so — that is a real objection at moderate "
        "severity. Do not comment on profitability."
    ),
    "operations": (
        "ATTACK THE OPERATIONS. Can a bot really run this unattended? Work "
        "through the failure modes: a partial fill that leaves one leg naked, "
        "an API outage or rate limit mid-cycle, a venue halt or maintenance "
        "window while positioned, a websocket that silently stops updating, "
        "key and withdrawal-permission handling, what happens when the hedge "
        "leg fails overnight. Does the draft's kill criteria actually fire in "
        "those cases? A strategy whose stated stop cannot be evaluated by a "
        "program is a landed objection. Do not comment on profitability or "
        "legality."
    ),
}

# Self-reported severity is meaningless without a calibration anchor —
# without these the modal reply is a mid-severity generic remark that trips
# no threshold and teaches nobody.
#
# The scale is about FIXABILITY, not about how wrong the draft is. >= 0.85
# is reserved for objections no rewrite could answer: the mechanism does not
# exist, the venue forbids it, it cannot run unattended at all. An
# overstated return, a missing fee, or an unstated assumption is a 0.5-0.7
# — genuinely wrong, and answerable by restating the number honestly. Early
# runs of this panel rated every arithmetic error 0.9, which killed drafts
# whose mechanism was real and whose estimate was merely optimistic.
_SEVERITY_SCALE = (
    "Severity is about FIXABILITY, not about how annoyed you are.\n"
    "  >= 0.85 — no rewrite can answer this. The mechanism does not exist at "
    "this venue, the terms forbid it, or it fundamentally cannot run "
    "unattended. Use this sparingly and only when you are certain.\n"
    "  0.60-0.80 — a real, material error the draft could answer by restating "
    "itself honestly: an overstated return, an omitted cost, a capacity claim "
    "that does not hold, an assumption never checked.\n"
    "  0.30-0.55 — true and worth saying, but it does not change whether the "
    "strategy is worth running.\n"
    "  below 0.30 — a nit. Reply 'none' instead."
)

_SEVERITY_ANCHORS: dict[str, str] = {
    "arithmetic": (
        "Calibration: 'this venue charges a positive maker fee, there is no "
        "rebate, so the described income does not exist at all' is 0.9 — the "
        "mechanism is absent. 'The return is overstated roughly 3x once both "
        "legs' fees are netted, though the mechanism does pay something' is "
        "0.65 — material and fixable by restating it. 'The estimate ignores a "
        "$0.30 withdrawal fee' is 0.25 — immaterial at the stated size."
    ),
    "competition": (
        "Calibration: 'this requires beating colocated firms on latency in the "
        "most contested book on the venue' is 0.9 — structurally unavailable. "
        "'The reward pool caps deployable capital near $5k, not the $50k "
        "claimed' is 0.65 — the edge is real but far smaller than stated. 'Two "
        "other bots likely do this, halving the share' is 0.4 — dilution."
    ),
    "legality": (
        "Calibration: 'the strategy needs two accounts trading against each "
        "other, which is wash trading' is 0.95 — fatal and not fixable. 'The "
        "venue's terms prohibit programmatic placement entirely' is 0.9. 'The "
        "draft never states whether the venue permits API order placement' is "
        "0.55 — must be checked before funding, and the draft can say so."
    ),
    "operations": (
        "Calibration: 'the hedge leg is on a venue with no programmatic "
        "cancel, so a partial fill can never be unwound without a human' is "
        "0.88 — cannot run unattended at all. 'The stated kill criterion is "
        "not measurable by a program as written' is 0.65 — fixable by stating "
        "a measurable one. 'No retry on a rate-limited cancel' is 0.3."
    ),
}


def _spec_block(idea: Idea) -> str:
    spec = idea.bot_spec
    if spec is None:
        return ""
    return (
        f"**Venue:** {spec.venue} ({spec.family.value})\n"
        f"**Docs:** {spec.venue_url or '(none cited)'}\n"
        f"**API primitives:** {', '.join(spec.api_primitives)}\n"
        f"**Mechanism:** {spec.mechanism}\n"
        f"**Capital:** ${spec.capital_floor_usd:,.0f} floor / ${spec.capital_target_usd:,.0f} target\n"
        f"**Expected return:** {spec.expected_return}\n"
        f"**Edge decay:** {spec.edge_decay}\n"
        f"**Kill criteria:** {'; '.join(spec.kill_criteria)}\n"
        f"**Validation:** {'; '.join(spec.validation_plan)}\n"
        f"**Legality note:** {spec.legality_note}\n"
        f"**Human touchpoints:** {spec.human_touchpoints}\n"
    )


def lens_prompt(lens: str, idea: Idea) -> str:
    """The prompt for one adversarial lens."""
    from project_forge.config import settings

    jurisdiction = (settings.operator_jurisdiction or "").strip()
    where = (
        f"\nThe operator is based in {jurisdiction}. Judge eligibility against that, not against a guess.\n"
        if jurisdiction and lens == "legality"
        else ""
    )
    return (
        "You are reviewing a proposed capital-deployment bot with the "
        "explicit goal of KILLING it. Your job is not to be fair — it is to "
        "find the reason this loses money or cannot be run.\n\n"
        f"## Your lens\n{_LENS_BRIEF[lens]}\n\n"
        f"{where}"
        f"## Severity\n{_SEVERITY_SCALE}\n\n{_SEVERITY_ANCHORS[lens]}\n\n"
        f"## The strategy\n"
        f"**Name:** {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**Scope:** {idea.mvp_scope}\n"
        f"{_spec_block(idea)}\n"
        "## Output\n"
        "Respond with JSON only:\n"
        '{"severity": 0.0-1.0, "objection": "one specific, concrete objection '
        'in your lens — or \\"none\\" if you genuinely cannot find one"}\n\n'
        "Be specific. A vague objection is worthless; quote the claim you are "
        "attacking. If you must invent a fact to make the objection, do not "
        "make it — say none instead."
    )


def _unfence(raw: str) -> str:
    raw = raw.strip()
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _parse_objection(lens: str, raw: str | None) -> Objection | None:
    """One lens reply → an Objection, or None when it found nothing usable."""
    if not raw:
        return None
    try:
        data: dict[str, Any] = json.loads(_unfence(raw))
    except Exception:  # noqa: BLE001 — an unparseable lens simply abstains
        return None
    if not isinstance(data, dict):
        return None
    text = str(data.get("objection", "")).strip()
    if text.lower() in _NULL_OBJECTIONS:
        return None
    try:
        severity = float(data.get("severity", 0.0))
    except (TypeError, ValueError):
        return None
    severity = max(0.0, min(1.0, severity))
    if severity < NOISE_SEVERITY:
        return None
    return Objection(lens=lens, severity=severity, text=text[:600])


def revise_prompt(idea: Idea, objections: list[Objection]) -> str:
    """Ask for ONE rewrite that answers the objections honestly.

    The identity fields are off limits. A rewrite that swaps the venue or
    the mechanism is not a revision, it is a different strategy wearing the
    same name — and it would escape the panel that just examined the
    original."""
    listed = "\n".join(f"- [{o.lens}, severity {o.severity:.2f}] {o.text}" for o in objections)
    spec = idea.bot_spec
    return (
        "A review panel attacked this strategy and landed the objections "
        "below. Rewrite it to ANSWER them honestly.\n\n"
        "Honest means: if the return was overstated, state the smaller real "
        "one and show the arithmetic. If the capital was wrong, correct it. "
        "If the strategy only works at a size the objection allows, say so. "
        "If the objection is fatal and cannot be answered, reply with "
        '{"unfixable": true} and nothing else — that is a valid answer and '
        "a better one than pretending.\n\n"
        "You may NOT change the venue, the mechanism, the API primitives, or "
        "the name. Those are the strategy's identity; changing them makes "
        "this a different proposal that has not been reviewed.\n\n"
        f"## Objections\n{listed}\n\n"
        f"## The strategy\n"
        f"**Name:** {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**Scope:** {idea.mvp_scope}\n"
        f"**Expected return:** {spec.expected_return if spec else ''}\n"
        f"**Edge decay:** {spec.edge_decay if spec else ''}\n"
        f"**Capital:** ${spec.capital_floor_usd:,.0f} / ${spec.capital_target_usd:,.0f}\n"
        if spec
        else ""
        "\n## Output\n"
        "Respond with JSON only. Include only the fields you are changing:\n"
        '{"tagline": "...", "description": "...", "market_analysis": "...", '
        '"mvp_scope": "...", "expected_return": "...", "edge_decay": "...", '
        '"capital_floor_usd": 0, "capital_target_usd": 0, "kill_criteria": ["..."]}'
    )


# Fields a revision may rewrite. Name, venue, mechanism and api_primitives
# are excluded on purpose: they are the strategy's identity and its dedup
# key, and a rewrite that changes them has escaped the review it just had.
REVISABLE_IDEA_FIELDS = ("tagline", "description", "market_analysis", "mvp_scope")
REVISABLE_SPEC_TEXT = ("expected_return", "edge_decay")
_MIN_FIELD_LEN = 8
_MAX_FIELD_LEN = 8000


async def _safe_call(backend, prompt: str) -> str | None:
    """One backend call, off the event loop, never raising.

    The CLI backend shells out to `claude --print`, which blocks for tens of
    seconds per call — and this panel makes up to six. Running that inline
    would freeze every request the web app is serving for minutes at a time,
    which is exactly what happened the first time this cadence fired."""
    try:
        return await asyncio.to_thread(backend.call, prompt)
    except Exception as exc:  # noqa: BLE001 — a failed call is just no answer
        logger.info("bot depth: call failed (%s)", str(exc)[:120])
        return None


def _apply_revision(idea: Idea, raw: str | None) -> Idea | None:
    """Apply a revision to a COPY of the idea. None when unusable.

    Returning a copy means a failed re-check leaves the original draft
    untouched for the drop record."""
    if not raw:
        return None
    try:
        data = json.loads(_unfence(raw))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("unfixable") is True:
        return None

    revised = idea.model_copy(deep=True)
    touched = False

    for field_name in REVISABLE_IDEA_FIELDS:
        value = data.get(field_name)
        if isinstance(value, str) and _MIN_FIELD_LEN <= len(value.strip()) <= _MAX_FIELD_LEN:
            setattr(revised, field_name, value.strip())
            touched = True

    spec = revised.bot_spec
    if spec is not None:
        for field_name in REVISABLE_SPEC_TEXT:
            value = data.get(field_name)
            if isinstance(value, str) and value.strip():
                setattr(spec, field_name, value.strip()[:600])
                touched = True
        floor = data.get("capital_floor_usd")
        target = data.get("capital_target_usd")
        if isinstance(floor, (int, float)) and floor >= 0:
            spec.capital_floor_usd = float(floor)
            touched = True
        if isinstance(target, (int, float)) and target >= 0:
            spec.capital_target_usd = float(target)
            touched = True
        if spec.capital_target_usd < spec.capital_floor_usd:
            spec.capital_floor_usd, spec.capital_target_usd = (
                spec.capital_target_usd,
                spec.capital_floor_usd,
            )
        kills = data.get("kill_criteria")
        if isinstance(kills, list):
            cleaned = [str(k).strip()[:400] for k in kills if str(k).strip()][:12]
            if cleaned:
                spec.kill_criteria = cleaned
                touched = True

    return revised if touched else None


def _killed(objections: list[Objection]) -> bool:
    if any(o.severity >= FATAL_SEVERITY for o in objections):
        return True
    return sum(1 for o in objections if o.severity >= LANDED_SEVERITY) >= 2


def _strongest(objections: list[Objection]) -> str | None:
    landed = [o for o in objections if o.severity >= LANDED_SEVERITY]
    if not landed:
        return None
    return max(landed, key=lambda o: o.severity).text


async def stress(idea: Idea) -> StressResult:
    """Run the four-lens panel over a strategy.

    Returns a StressResult in every case, including keyless and including
    a strategy with no spec — callers can always rely on getting a usable
    Idea back."""
    if idea.bot_spec is None:
        # Nothing concrete to attack. The gate refuses it for a clearer
        # reason than a panel could give, and running four LLM calls over a
        # spec-less draft would just be spend.
        return StressResult(idea=idea, survived=True, passes=0)

    backend = resolve_role_backend("review")
    if backend is None:
        return StressResult(idea=idea, survived=True, passes=0)

    objections: list[Objection] = []
    passes = 0
    for lens in LENSES:
        raw = await _safe_call(backend, lens_prompt(lens, idea))
        passes += 1
        parsed = _parse_objection(lens, raw)
        if parsed is not None:
            objections.append(parsed)

    strongest = _strongest(objections)

    if _killed(objections):
        # One rewrite, then the hardest objection is re-asked against the
        # revision. Almost every kill in practice is "your arithmetic is
        # wrong" or "that return is asserted, not derived" — which a draft
        # can answer honestly by restating a smaller, correct number. Killing
        # outright would throw away a real strategy over a bad estimate;
        # accepting the rewrite unchecked would make the panel decorative.
        # So: rewrite, then re-run the lens that hit hardest.
        hardest = max(objections, key=lambda o: o.severity)
        raw_revision = await _safe_call(backend, revise_prompt(idea, objections))
        revised = _apply_revision(idea, raw_revision)
        if revised is None:
            # No answer at all (timeout, dead backend) is not the same as the
            # model saying "this cannot be fixed". Only the latter is a
            # judgement, and only a judgement should read as a rejection.
            incomplete = raw_revision is None
            logger.info(
                "bot depth: %s %r (%s)",
                "could not complete review of" if incomplete else "panel killed",
                idea.name,
                "revision call failed" if incomplete else "no usable revision",
            )
            return StressResult(
                idea=idea,
                objections=objections,
                strongest=strongest or objections[0].text,
                survived=False,
                passes=passes + 1,
                incomplete=incomplete,
            )

        recheck = _parse_objection(hardest.lens, await _safe_call(backend, lens_prompt(hardest.lens, revised)))
        passes += 2
        if recheck is not None and recheck.severity >= FATAL_SEVERITY:
            logger.info("bot depth: revision failed to answer %s (%s)", hardest.lens, recheck.text[:100])
            return StressResult(
                idea=idea,
                objections=[*objections, recheck],
                strongest=recheck.text,
                survived=False,
                passes=passes,
            )

        # The rewrite answered it. Publish whatever the re-check still says.
        surviving = recheck.text if recheck is not None else None
        if revised.bot_spec is not None:
            revised.bot_spec.surviving_objection = surviving
        return StressResult(
            idea=revised,
            objections=[*objections, *([recheck] if recheck else [])],
            strongest=surviving,
            survived=True,
            passes=passes,
            revised=True,
        )

    # Survived. Publish the strongest surviving objection with the strategy.
    if strongest is not None and idea.bot_spec is not None:
        idea.bot_spec.surviving_objection = strongest

    return StressResult(
        idea=idea,
        objections=objections,
        strongest=strongest,
        survived=True,
        passes=passes,
    )
