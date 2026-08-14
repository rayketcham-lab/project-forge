"""The library of edges that already work.

Generation that starts from a blank page reinvents "AI-powered trading
bot" every time. This library is the grounding corpus: strategy PRIMITIVES
that are publicly documented and demonstrably real — maker rebates,
per-minute liquidity rewards, funding carry, cash-and-carry basis,
cross-book middling, treasury sweeps.

The tests below are mostly honesty gates. A library that quietly carries a
"risk-free 40% APR" entry would poison every idea generated from it, so
each primitive must name how it decays, what can go wrong, and why it is
legal — and no entry may promise a free lunch.
"""

from __future__ import annotations

import random
import re

import pytest

from project_forge.engine.strategy_library import (
    STRATEGY_LIBRARY,
    StrategyPrimitive,
    by_category,
    by_family,
    library_prompt_block,
    pick_primitive,
)
from project_forge.models import MONEY_CATEGORIES, BotVenueFamily

# Negated forms are honest risk disclosure ("inclusion is not guaranteed"),
# so they're allowed through — only the promise itself is a failure.
_FREE_LUNCH = re.compile(
    r"(?<!not )(?<!never )(?<!no )\b(guaranteed|risk[- ]free|riskless|no risk|cannot lose|"
    r"can't lose|sure thing|free money|always profitable)\b",
    re.IGNORECASE,
)


class TestLibraryShape:
    def test_library_is_populated(self):
        assert len(STRATEGY_LIBRARY) >= 15

    def test_every_entry_is_a_primitive(self):
        for prim in STRATEGY_LIBRARY:
            assert isinstance(prim, StrategyPrimitive)

    def test_keys_are_unique(self):
        keys = [p.key for p in STRATEGY_LIBRARY]
        assert len(keys) == len(set(keys))

    def test_required_text_is_populated(self):
        for prim in STRATEGY_LIBRARY:
            assert prim.name.strip()
            assert prim.mechanism.strip()
            assert prim.decay.strip()
            assert prim.legality_note.strip()
            assert prim.verify_by.strip(), f"{prim.key} must say how to re-verify it"

    def test_every_entry_names_an_api_surface(self):
        for prim in STRATEGY_LIBRARY:
            assert prim.api_primitives, f"{prim.key} has no API primitives"

    def test_every_entry_names_its_risks(self):
        for prim in STRATEGY_LIBRARY:
            assert len(prim.known_risks) >= 2, f"{prim.key} needs at least two honest risks"

    def test_capital_floor_is_sane(self):
        for prim in STRATEGY_LIBRARY:
            assert prim.capital_floor_usd >= 0.0


class TestHonesty:
    def test_no_free_lunch_claims(self):
        for prim in STRATEGY_LIBRARY:
            blob = " ".join([prim.name, prim.mechanism, prim.yield_shape, prim.decay, *prim.known_risks])
            hit = _FREE_LUNCH.search(blob)
            assert hit is None, f"{prim.key} promises a free lunch: {hit.group(0)!r}"

    def test_every_entry_explains_why_it_is_legal(self):
        for prim in STRATEGY_LIBRARY:
            assert len(prim.legality_note) > 20, f"{prim.key} legality note is too thin"


class TestCoverage:
    def test_every_bot_category_has_a_primitive(self):
        covered = {p.category for p in STRATEGY_LIBRARY}
        for cat in MONEY_CATEGORIES:
            assert cat in covered, f"no library primitive for {cat}"

    def test_all_four_venue_families_are_covered(self):
        covered = {p.family for p in STRATEGY_LIBRARY}
        for fam in (
            BotVenueFamily.PREDICTION_MARKETS,
            BotVenueFamily.CRYPTO_DEFI,
            BotVenueFamily.SPORTSBOOK,
            BotVenueFamily.BROKERAGE,
        ):
            assert fam in covered, f"no library primitive for {fam}"


class TestSelectors:
    def test_by_category_filters(self):
        for cat in MONEY_CATEGORIES:
            got = by_category(cat)
            assert got
            assert all(p.category == cat for p in got)

    def test_by_family_filters(self):
        got = by_family(BotVenueFamily.PREDICTION_MARKETS)
        assert got
        assert all(p.family is BotVenueFamily.PREDICTION_MARKETS for p in got)

    def test_pick_primitive_is_deterministic_under_a_seeded_rng(self):
        a = pick_primitive(rng=random.Random(7))
        b = pick_primitive(rng=random.Random(7))
        assert a.key == b.key

    def test_pick_primitive_honours_category(self):
        cat = MONEY_CATEGORIES[0]
        prim = pick_primitive(rng=random.Random(3), category=cat)
        assert prim.category == cat

    def test_pick_primitive_on_empty_category_falls_back(self):
        """A category with no primitives must not explode the caller."""
        from project_forge.models import IdeaCategory

        prim = pick_primitive(rng=random.Random(3), category=IdeaCategory.SECURITY_TOOL)
        assert isinstance(prim, StrategyPrimitive)


class TestPromptBlock:
    def test_block_carries_mechanism_and_decay(self):
        prim = STRATEGY_LIBRARY[0]
        block = library_prompt_block([prim])
        assert prim.name in block
        assert prim.mechanism[:40] in block
        assert prim.decay[:40] in block

    def test_block_handles_empty_input(self):
        assert library_prompt_block([]) == ""

    @pytest.mark.parametrize("count", [1, 3, 5])
    def test_block_scales(self, count):
        block = library_prompt_block(list(STRATEGY_LIBRARY[:count]))
        for prim in STRATEGY_LIBRARY[:count]:
            assert prim.name in block
