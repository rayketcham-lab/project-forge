"""Grounded bot-strategy generation and the adversarial edge panel.

Two halves of the same job:

  * `generate_bot_llm` turns a probed venue program plus a known-working
    mechanism into an Idea carrying a real BotSpec. A generation that comes
    back without a parseable spec is worthless to this board and returns
    None rather than a half-idea the gate would reject anyway.

  * `bot_depth.stress` is the red team. A strategy that survives one LLM
    pass is a paragraph that sounds plausible; the panel attacks the four
    ways these actually die — the arithmetic (fees and slippage eat it),
    the competition (the edge is already gone, or has no capacity), the
    legality (the venue's terms forbid it), and the operations (it cannot
    run unattended).
"""

from __future__ import annotations

import json

import pytest

from project_forge.engine import bot_depth
from project_forge.engine.llm_generator import generate_bot_llm
from project_forge.engine.strategy_library import STRATEGY_LIBRARY
from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory

_PROGRAM = {
    "venue": "Polymarket",
    "family": BotVenueFamily.PREDICTION_MARKETS.value,
    "category": IdeaCategory.INCENTIVE_CAPTURE.value,
    "title": "Liquidity rewards: qualifying spread not documented",
    "url": "https://github.com/Polymarket/py-clob-client/issues/42",
    "summary": "Reward budget per market and max spread band are unclear.",
    "source": "github-issue",
    "program_score": 6,
}

_GOOD_PAYLOAD = {
    "name": "Reward Minute Maker",
    "tagline": "rest two-sided quotes inside the qualifying band and collect the reward budget",
    "description": (
        "Quotes both sides of high-reward books through the CLOB REST API, keeping size "
        "resting inside the qualifying spread so it earns reward minutes. Income is the "
        "venue's published budget, split pro-rata, so yield decays as makers arrive."
    ),
    "market_analysis": "Reward budgets are published per market; most books have few makers.",
    "mvp_scope": "Phase 1 one book at floor capital. Phase 2 multi-book allocation.",
    "tech_stack": ["python", "websockets"],
    "feasibility_score": 0.72,
    "bot_spec": {
        "venue": "Polymarket",
        "venue_url": "https://docs.polymarket.com/rewards",
        "family": "prediction-markets",
        "api_primitives": ["CLOB REST order placement", "websocket book feed", "rewards endpoint"],
        "mechanism": "Venue pays a published per-minute liquidity reward for two-sided quotes in band.",
        "capital_floor_usd": 500,
        "capital_target_usd": 10000,
        "expected_return": "Pro-rata share of the published reward budget",
        "edge_decay": "Fixed pool split pro-rata — yield falls as competing makers arrive",
        "kill_criteria": ["reward per minute below fees plus adverse selection"],
        "validation_plan": ["one book, floor capital, 14 days, measure realised share"],
        "legality_note": "Published venue program, public rules, no manipulation",
        "human_touchpoints": "Weekly book selection review",
    },
}


class _Backend:
    """Deterministic backend double."""

    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def call(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _spec(**over) -> BotSpec:
    base = dict(_GOOD_PAYLOAD["bot_spec"])
    base.update(over)
    return BotSpec(**base)


def _idea(**over) -> Idea:
    base = dict(
        name="Reward Minute Maker",
        tagline="rest two-sided quotes and collect the reward budget",
        description=_GOOD_PAYLOAD["description"],
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis=_GOOD_PAYLOAD["market_analysis"],
        feasibility_score=0.72,
        mvp_scope=_GOOD_PAYLOAD["mvp_scope"],
        tech_stack=["python"],
    )
    base.update(over)
    idea = Idea(**base)
    if "bot_spec" not in over:
        idea.bot_spec = _spec()
    return idea


# --------------------------------------------------------------------------- #
# Generation                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestGenerateBotLlm:
    async def test_builds_an_idea_with_a_spec(self, db):
        backend = _Backend(json.dumps(_GOOD_PAYLOAD))
        result = await generate_bot_llm(
            db,
            IdeaCategory.INCENTIVE_CAPTURE,
            program=_PROGRAM,
            primitive=STRATEGY_LIBRARY[0],
            backend=backend,
        )
        assert result is not None
        assert result.mode == "bot"
        assert result.idea.generation_mode == "bot"
        assert result.idea.category is IdeaCategory.INCENTIVE_CAPTURE
        spec = result.idea.bot_spec
        assert spec is not None
        assert spec.venue == "Polymarket"
        assert spec.capital_floor_usd == 500.0
        assert "rewards endpoint" in spec.api_primitives

    async def test_no_backend_returns_none(self, db, monkeypatch):
        import project_forge.engine.llm_generator as gen

        monkeypatch.setattr(gen, "resolve_cheap_backend", lambda: None)
        assert await generate_bot_llm(db, IdeaCategory.MARKET_MAKING, program=_PROGRAM) is None

    async def test_unparseable_json_returns_none(self, db):
        backend = _Backend("here's a great idea for you!")
        assert await generate_bot_llm(db, IdeaCategory.MARKET_MAKING, program=_PROGRAM, backend=backend) is None

    async def test_missing_spec_returns_none(self, db):
        """An idea with no spec can never be admitted — don't half-build it."""
        payload = {k: v for k, v in _GOOD_PAYLOAD.items() if k != "bot_spec"}
        backend = _Backend(json.dumps(payload))
        assert await generate_bot_llm(db, IdeaCategory.MARKET_MAKING, program=_PROGRAM, backend=backend) is None

    async def test_unusable_spec_returns_none(self, db):
        payload = dict(_GOOD_PAYLOAD)
        payload["bot_spec"] = {"venue": "", "api_primitives": [], "mechanism": ""}
        backend = _Backend(json.dumps(payload))
        assert await generate_bot_llm(db, IdeaCategory.MARKET_MAKING, program=_PROGRAM, backend=backend) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("500", 500.0), ("$1,000", 1000.0), ("2.5k", 2500.0), (None, 0.0), ("lots", 0.0)],
    )
    async def test_capital_is_coerced_from_whatever_the_model_writes(self, db, raw, expected):
        payload = json.loads(json.dumps(_GOOD_PAYLOAD))
        payload["bot_spec"]["capital_floor_usd"] = raw
        payload["bot_spec"]["capital_target_usd"] = 99999
        backend = _Backend(json.dumps(payload))
        result = await generate_bot_llm(db, IdeaCategory.INCENTIVE_CAPTURE, program=_PROGRAM, backend=backend)
        assert result is not None
        assert result.idea.bot_spec.capital_floor_usd == expected

    async def test_inverted_capital_band_is_repaired_not_dropped(self, db):
        payload = json.loads(json.dumps(_GOOD_PAYLOAD))
        payload["bot_spec"]["capital_floor_usd"] = 10000
        payload["bot_spec"]["capital_target_usd"] = 500
        backend = _Backend(json.dumps(payload))
        result = await generate_bot_llm(db, IdeaCategory.INCENTIVE_CAPTURE, program=_PROGRAM, backend=backend)
        assert result is not None
        spec = result.idea.bot_spec
        assert spec.capital_target_usd >= spec.capital_floor_usd

    async def test_prompt_carries_the_grounding_and_the_demands(self, db):
        backend = _Backend(json.dumps(_GOOD_PAYLOAD))
        await generate_bot_llm(
            db,
            IdeaCategory.INCENTIVE_CAPTURE,
            program=_PROGRAM,
            primitive=STRATEGY_LIBRARY[0],
            backend=backend,
        )
        prompt = backend.prompts[0]
        assert "Polymarket" in prompt
        assert _PROGRAM["url"] in prompt
        assert STRATEGY_LIBRARY[0].name in prompt
        assert "bot_spec" in prompt
        lowered = prompt.lower()
        for demand in ("kill", "decay", "capital", "api", "legal"):
            assert demand in lowered


# --------------------------------------------------------------------------- #
# The edge panel                                                              #
# --------------------------------------------------------------------------- #


def _objection(severity: float, text: str = "the fees eat it") -> str:
    return json.dumps({"severity": severity, "objection": text})


class _PanelBackend:
    """Returns replies in order, one per lens call."""

    name = "fake"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def call(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "{}"


# An un-awaited coroutine only warns, so the panel silently skipped its
# re-check for one commit. Promote that warning to a failure here: every
# backend call in this module is async now, and a missed `await` must break
# the build rather than quietly disable a review stage.
@pytest.mark.filterwarnings("error::RuntimeWarning")
@pytest.mark.asyncio
class TestEdgePanel:
    async def test_keyless_is_a_no_op_that_survives(self, monkeypatch):
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: None)
        result = await bot_depth.stress(_idea())
        assert result.survived
        assert result.passes == 0
        assert result.strongest is None

    async def test_a_fatal_objection_survives_only_if_the_revision_answers_it(self, monkeypatch):
        """One rewrite, then the hardest objection is re-asked. Still fatal → dead."""
        replies = (
            [_objection(0.9, "maker fee is positive here, the rebate does not exist")]
            + [_objection(0.1) for _ in range(len(bot_depth.LENSES) - 1)]
            + [json.dumps({"description": "revised text that does not fix the fee"})]
            + [_objection(0.9, "the fee is still positive")]
        )
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert not result.survived
        assert "still positive" in (result.strongest or "")

    async def test_a_revision_that_answers_the_objection_survives(self, monkeypatch):
        replies = (
            [_objection(0.9, "the claimed return is asserted, never derived")]
            + [_objection(0.1) for _ in range(len(bot_depth.LENSES) - 1)]
            + [
                json.dumps(
                    {
                        "description": "Revised: derives the return from the published budget, net of fees.",
                        "expected_return": "0.4-0.9% monthly on deployed capital, net of both legs",
                    }
                )
            ]
            + [_objection(0.4, "still thin, but the arithmetic now holds")]
        )
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert result.survived
        assert result.revised
        assert "derives the return" in result.idea.description
        assert "net of both legs" in result.idea.bot_spec.expected_return
        assert "still thin" in (result.strongest or "")

    async def test_revision_cannot_rename_or_move_the_strategy(self, monkeypatch):
        """The venue and the mechanism ARE the strategy — a rewrite may not swap them."""
        replies = (
            [_objection(0.9, "numbers are wrong")]
            + [_objection(0.1) for _ in range(len(bot_depth.LENSES) - 1)]
            + [
                json.dumps(
                    {
                        "name": "Totally Different Bot",
                        "venue": "Binance",
                        "mechanism": "something else entirely",
                        "description": "Revised with honest arithmetic.",
                    }
                )
            ]
            + [_objection(0.3, "acceptable now")]
        )
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert result.survived
        assert result.idea.name == "Reward Minute Maker"
        assert result.idea.bot_spec.venue == "Polymarket"
        assert "published per-minute liquidity reward" in result.idea.bot_spec.mechanism

    async def test_unparseable_revision_leaves_it_dead(self, monkeypatch):
        replies = (
            [_objection(0.9, "numbers are wrong")]
            + [_objection(0.1) for _ in range(len(bot_depth.LENSES) - 1)]
            + ["I'm not sure how to fix this"]
        )
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert not result.survived

    async def test_two_landed_hits_also_trigger_the_revision_path(self, monkeypatch):
        replies = (
            [_objection(0.7, "fees exceed the spread"), _objection(0.7, "no capacity")]
            + [_objection(0.1) for _ in range(len(bot_depth.LENSES) - 2)]
            + ["not parseable"]
        )
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert not result.survived

    async def test_one_landed_hit_survives_and_is_recorded(self, monkeypatch):
        replies = [_objection(0.65, "capacity caps this near $20k")] + [
            _objection(0.1) for _ in range(len(bot_depth.LENSES) - 1)
        ]
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        idea = _idea()
        result = await bot_depth.stress(idea)
        assert result.survived
        assert "capacity" in (result.strongest or "")
        # The surviving objection is published with the strategy, not buried.
        assert result.idea.bot_spec.surviving_objection == result.strongest

    async def test_unparseable_lens_replies_are_ignored(self, monkeypatch):
        replies = ["not json at all" for _ in bot_depth.LENSES]
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: _PanelBackend(replies))
        result = await bot_depth.stress(_idea())
        assert result.survived
        assert result.strongest is None

    async def test_every_lens_is_asked_something_specific(self, monkeypatch):
        backend = _PanelBackend([_objection(0.1) for _ in bot_depth.LENSES])
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: backend)
        await bot_depth.stress(_idea())
        joined = " ".join(backend.prompts).lower()
        assert "fee" in joined and "slippage" in joined
        assert "competit" in joined or "capacity" in joined
        assert "terms" in joined or "legal" in joined
        assert "unattended" in joined or "outage" in joined

    async def test_lens_prompt_includes_the_spec(self, monkeypatch):
        backend = _PanelBackend([_objection(0.1) for _ in bot_depth.LENSES])
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: backend)
        await bot_depth.stress(_idea())
        assert "Polymarket" in backend.prompts[0]

    async def test_specless_idea_is_not_panelled(self, monkeypatch):
        """Nothing to attack — the gate rejects it for a better reason."""
        backend = _PanelBackend([_objection(0.9) for _ in bot_depth.LENSES])
        monkeypatch.setattr(bot_depth, "resolve_cheap_backend", lambda: backend)
        result = await bot_depth.stress(_idea(bot_spec=None))
        assert result.passes == 0
        assert not backend.prompts
