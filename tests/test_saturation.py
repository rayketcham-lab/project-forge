"""Tests for the v0.21 density loop — saturation-aware generation + pool thinning.

The corpus audit (#97) found the engine computes saturation (Cartographer)
but nothing consumes it: the live prompt is density-blind (recency-30
avoid list only), the live pair picker balances exploration not density,
and the siphon only collapses paraphrase-dupes — distinct-but-crowded
zones survive forever. This suite pins the missing wires:

  - engine/saturation.py: category_density / crowded_stems /
    density_prompt_block / inverse weights / pair scoring
  - llm_generator: density block injected into the live prompt
  - horizontal picker: density breaks ties within the least-explored tier
  - siphon_density: reversible per-category thinning (saturation_thin)
  - adaptive back-fill batch (keyless 200 / backend 5)
  - cartographer consumes the shared density function (regression)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "saturation.db")
    await database.connect()
    yield database
    await database.close()


def _idea(name: str, category: IdeaCategory, **over) -> Idea:
    base = dict(
        name=name,
        tagline=f"{name.lower()} does one concrete job for one buyer",
        description="A focused tool that solves one sharp problem for one operator.",
        category=category,
        market_analysis="A specific buyer with budget exists for this.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: core. Phase 2: polish.",
        tech_stack=["python"],
        content_hash=f"h-{name.lower().replace(' ', '-')}",
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# category_density                                                            #
# --------------------------------------------------------------------------- #


class TestCategoryDensity:
    @pytest.mark.asyncio
    async def test_counts_actives_only_and_zero_fills(self, db):
        from project_forge.engine.saturation import category_density

        await db.save_idea(_idea("Active One", IdeaCategory.MICRO_SAAS))
        await db.save_idea(_idea("Active Two", IdeaCategory.MICRO_SAAS))
        gone = _idea("Archived One", IdeaCategory.MICRO_SAAS)
        gone.status = "archived"
        await db.save_idea(gone)
        rej = _idea("Rejected One", IdeaCategory.MICRO_SAAS)
        rej.status = "rejected"
        await db.save_idea(rej)

        density = await category_density(db)
        assert density[IdeaCategory.MICRO_SAAS.value] == 2
        # Every category appears, zero-filled.
        assert density[IdeaCategory.FLIPPING_ARBITRAGE.value] == 0
        assert set(density.keys()) == {c.value for c in IdeaCategory}

    @pytest.mark.asyncio
    async def test_cartographer_uses_shared_density(self, db, monkeypatch):
        """build_atlas must consume saturation.category_density — one source
        of truth, not two copies of the same aggregate."""
        import project_forge.engine.cartographer as carto
        from project_forge.engine import saturation

        calls = {"n": 0}
        real = saturation.category_density

        async def _spy(db_):
            calls["n"] += 1
            return await real(db_)

        monkeypatch.setattr(carto, "category_density", _spy)
        atlas = await carto.build_atlas(db)
        assert calls["n"] == 1
        assert "vertical_coverage" in atlas

    def test_thresholds_reexported_for_backcompat(self):
        # test_cartographer imports these from cartographer; they now LIVE
        # in saturation and are re-exported.
        from project_forge.engine.cartographer import (
            SATURATION_COUNT_THRESHOLD as C_SAT,
        )
        from project_forge.engine.cartographer import (
            WHITE_SPACE_THRESHOLD as C_WS,
        )
        from project_forge.engine.saturation import (
            SATURATION_COUNT_THRESHOLD,
            WHITE_SPACE_THRESHOLD,
        )

        assert C_SAT == SATURATION_COUNT_THRESHOLD
        assert C_WS == WHITE_SPACE_THRESHOLD


# --------------------------------------------------------------------------- #
# crowded_stems — the wheel list                                              #
# --------------------------------------------------------------------------- #


class TestCrowdedStems:
    @pytest.mark.asyncio
    async def test_returns_repeated_stems_desc_including_archived(self, db):
        from project_forge.engine.saturation import crowded_stems

        # 3 actives + 1 archived share a stem; another stem appears twice.
        for i in range(3):
            await db.save_idea(
                _idea(
                    f"Topo Mapper {i}",
                    IdeaCategory.OBSERVABILITY,
                    tagline="distributed system topology auto-mapper for ops teams",
                    content_hash=f"stem-a-{i}",
                )
            )
        arch = _idea(
            "Topo Mapper Old",
            IdeaCategory.OBSERVABILITY,
            tagline="distributed system topology auto-mapper for platform teams",
            content_hash="stem-a-arch",
        )
        arch.status = "archived"
        await db.save_idea(arch)
        for i in range(2):
            await db.save_idea(
                _idea(
                    f"Log Norm {i}",
                    IdeaCategory.OBSERVABILITY,
                    tagline="multi-cluster log aggregation normalizer with retention",
                    content_hash=f"stem-b-{i}",
                )
            )

        stems = await crowded_stems(db, IdeaCategory.OBSERVABILITY, min_count=2, limit=5)
        assert stems, "expected crowded stems"
        top_stem, top_count = stems[0]
        assert "distributed system topology auto-mapper" == top_stem
        assert top_count == 4  # archived counts — the wheel list remembers trims

    @pytest.mark.asyncio
    async def test_excludes_rejected_and_respects_min_count(self, db):
        from project_forge.engine.saturation import crowded_stems

        rej = _idea(
            "Rejected Stem",
            IdeaCategory.PRIVACY,
            tagline="pii vault gateway proxy for healthcare",
            content_hash="stem-rej",
        )
        rej.status = "rejected"
        await db.save_idea(rej)
        await db.save_idea(
            _idea(
                "Lonely",
                IdeaCategory.PRIVACY,
                tagline="a single unique concept nobody repeated",
                content_hash="stem-lone",
            )
        )
        stems = await crowded_stems(db, IdeaCategory.PRIVACY, min_count=2, limit=5)
        assert stems == []


# --------------------------------------------------------------------------- #
# density_prompt_block                                                        #
# --------------------------------------------------------------------------- #


class TestDensityPromptBlock:
    @pytest.mark.asyncio
    async def test_crowded_category_block_names_count_and_stems(self, db):
        from project_forge.engine.saturation import (
            SATURATION_COUNT_THRESHOLD,
            density_prompt_block,
        )

        for i in range(SATURATION_COUNT_THRESHOLD + 2):
            await db.save_idea(
                _idea(
                    f"Crowded {i}",
                    IdeaCategory.MICRO_SAAS,
                    tagline="webhook debugger and replay service for developers",
                    content_hash=f"dpb-{i}",
                )
            )
        block = await density_prompt_block(db, IdeaCategory.MICRO_SAAS)
        assert "Corpus density" in block
        assert str(SATURATION_COUNT_THRESHOLD + 2) in block
        assert "CROWDED" in block
        assert "webhook debugger and replay service" in block

    @pytest.mark.asyncio
    async def test_white_space_category_gets_plant_a_flag_hint(self, db):
        from project_forge.engine.saturation import density_prompt_block

        block = await density_prompt_block(db, IdeaCategory.FLIPPING_ARBITRAGE)
        assert "white space" in block.lower()


# --------------------------------------------------------------------------- #
# weights + pair scoring                                                      #
# --------------------------------------------------------------------------- #


class TestWeights:
    def test_inverse_density_weight_monotone(self):
        from project_forge.engine.saturation import inverse_density_weight

        w0 = inverse_density_weight(0)
        w40 = inverse_density_weight(40)
        w190 = inverse_density_weight(190)
        assert w0 > w40 > w190 > 0.0

    def test_rank_pair_score_prefers_thin_pairs_within_tier(self):
        from project_forge.engine.saturation import rank_pair_score

        thin = rank_pair_score(0, 2, 4)
        dense = rank_pair_score(0, 190, 170)
        assert thin < dense
        # Exploration still dominates: a never-explored dense pair beats a
        # much-explored thin pair only via the tier system, not this score —
        # but the score itself must keep count as the primary term.
        assert rank_pair_score(5, 0, 0) > rank_pair_score(0, 190, 170)

    @pytest.mark.asyncio
    async def test_pick_weighted_category_biases_thin(self, db, monkeypatch):
        from project_forge.engine import saturation

        async def _fake_density(db_):
            d = {c.value: 0 for c in IdeaCategory}
            d[IdeaCategory.MICRO_SAAS.value] = 190
            d[IdeaCategory.COMMERCE_OPS.value] = 2
            return d

        monkeypatch.setattr(saturation, "category_density", _fake_density)

        captured: dict = {}

        class _Rng:
            def choices(self, population, weights, k=1):
                captured["pop"] = list(population)
                captured["weights"] = list(weights)
                return [population[0]]

        cats = [IdeaCategory.MICRO_SAAS, IdeaCategory.COMMERCE_OPS]
        await saturation.pick_weighted_category(db, cats, rng=_Rng())
        w = dict(zip(captured["pop"], captured["weights"], strict=True))
        assert w[IdeaCategory.COMMERCE_OPS] > w[IdeaCategory.MICRO_SAAS]


# --------------------------------------------------------------------------- #
# live prompt wiring                                                          #
# --------------------------------------------------------------------------- #


class TestPromptWiring:
    def test_build_prompt_injects_density_block(self):
        from project_forge.engine.llm_generator import _build_prompt

        prompt = _build_prompt(
            IdeaCategory.MICRO_SAAS,
            "novel",
            "persona",
            ["- Old Idea — old tagline"],
            density_block="## Corpus density for micro-saas\n42 active ideas.",
        )
        assert "Corpus density" in prompt
        assert "Do NOT produce" in prompt  # avoid list retained

    @pytest.mark.asyncio
    async def test_generate_idea_llm_sends_density_block(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        payload = (
            '{"name": "Fresh Angle", "tagline": "a genuinely new angle", '
            '"description": "New thing.", "market_analysis": "Buyers.", '
            '"mvp_scope": "Phase 1.", "tech_stack": ["python"], '
            '"feasibility_score": 0.7}'
        )
        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value=payload)

        result = await generate_idea_llm(db, IdeaCategory.MICRO_SAAS, backend=backend)
        assert result is not None
        sent = backend.call.call_args[0][0]
        assert "Corpus density" in sent

    @pytest.mark.asyncio
    async def test_recent_avoid_list_trimmed_to_15(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        for i in range(25):
            await db.save_idea(_idea(f"Filler {i}", IdeaCategory.MICRO_SAAS, content_hash=f"fill-{i}"))
        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value="not json")  # parse fails; prompt still sent

        await generate_idea_llm(db, IdeaCategory.MICRO_SAAS, backend=backend)
        sent = backend.call.call_args[0][0]
        avoid_section = sent.split("Do NOT produce", 1)[1]
        avoid_lines = [ln for ln in avoid_section.splitlines() if ln.startswith("- ")]
        assert len(avoid_lines) <= 15


# --------------------------------------------------------------------------- #
# pair picker — density breaks ties within the least-explored tier            #
# --------------------------------------------------------------------------- #


class TestPairPickerDensity:
    @pytest.mark.asyncio
    async def test_density_breaks_tie_toward_thin_pair(self, db, monkeypatch):
        import project_forge.cron.horizontal as horizontal

        thin_a, thin_b = IdeaCategory.COMMERCE_OPS, IdeaCategory.DIGITAL_PRODUCTS

        async def _fake_density(db_):
            d = {c.value: 500 for c in IdeaCategory}
            d[thin_a.value] = 1
            d[thin_b.value] = 2
            return d

        monkeypatch.setattr(horizontal, "category_density", _fake_density)
        # Fresh DB → every pair explored 0 times → whole tier ties → the
        # thin pair must win on density.
        cat_a, cat_b = await horizontal.pick_cross_category_pair(db)
        assert {cat_a, cat_b} == {thin_a, thin_b}

    @pytest.mark.asyncio
    async def test_self_improvement_still_excluded(self, db, monkeypatch):
        import project_forge.cron.horizontal as horizontal

        async def _fake_density(db_):
            d = {c.value: 500 for c in IdeaCategory}
            d[IdeaCategory.SELF_IMPROVEMENT.value] = 0
            d[IdeaCategory.PRIVACY.value] = 1
            return d

        monkeypatch.setattr(horizontal, "category_density", _fake_density)
        cat_a, cat_b = await horizontal.pick_cross_category_pair(db)
        assert IdeaCategory.SELF_IMPROVEMENT not in (cat_a, cat_b)


# --------------------------------------------------------------------------- #
# siphon_density — reversible thinning                                        #
# --------------------------------------------------------------------------- #


class TestSiphonDensity:
    async def _seed_over_cap(self, db, cap: int):
        """cap+4 actives in one category: 1 terminal, 1 mission-tagged,
        1 operator-ingested, 1 high-scored, rest low template filler."""
        cat = IdeaCategory.MICRO_SAAS
        terminal = _idea("Kept Terminal", cat, content_hash="sd-term")
        terminal.status = "approved"
        await db.save_idea(terminal)

        missioned = _idea("Kept Mission", cat, content_hash="sd-mis")
        missioned.mission_id = "m123"
        await db.save_idea(missioned)

        ingested = _idea("Kept Ingested", cat, content_hash="sd-url")
        ingested.source_url = "https://example.com/x"
        await db.save_idea(ingested)

        star = _idea("Kept Star", cat, content_hash="sd-star")
        star.fundability_score = 0.95
        star.generation_mode = "novel"
        await db.save_idea(star)

        old = datetime.now(UTC) - timedelta(days=90)
        for i in range(cap):
            filler = _idea(f"Filler {i}", cat, content_hash=f"sd-fill-{i}")
            filler.generated_at = old + timedelta(minutes=i)
            filler.fundability_score = 0.10 + i * 0.001
            await db.save_idea(filler)
        return cat

    @pytest.mark.asyncio
    async def test_thins_to_cap_keeping_protected_and_best(self, db):
        from project_forge.engine.siphon import siphon_density

        cap = 6
        cat = await self._seed_over_cap(db, cap)  # cap+4 = 10 actives
        report = await siphon_density(db, dry_run=False, cap=cap)

        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE category = ? AND status NOT IN ('archived','rejected')",
            (cat.value,),
        )
        assert (await cur.fetchone())[0] == cap
        assert report["archived_count"] == 4

        # Protected + best survive.
        for kept_hash in ("sd-term", "sd-mis", "sd-url", "sd-star"):
            cur = await db.db.execute("SELECT status FROM ideas WHERE content_hash = ?", (kept_hash,))
            assert (await cur.fetchone())["status"] != "archived", kept_hash

        # Victims stamped reversibly.
        cur = await db.db.execute("SELECT COUNT(*) FROM ideas WHERE archived_reason = 'saturation_thin'")
        assert (await cur.fetchone())[0] == 4

    @pytest.mark.asyncio
    async def test_dry_run_mutates_nothing(self, db):
        from project_forge.engine.siphon import siphon_density

        cap = 6
        cat = await self._seed_over_cap(db, cap)
        report = await siphon_density(db, dry_run=True, cap=cap)
        assert report["archived_count"] == 4
        assert report["applied_count"] == 0
        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE category = ? AND status NOT IN ('archived','rejected')",
            (cat.value,),
        )
        assert (await cur.fetchone())[0] == cap + 4

    @pytest.mark.asyncio
    async def test_under_cap_category_untouched(self, db):
        from project_forge.engine.siphon import siphon_density

        await db.save_idea(_idea("Small Pool", IdeaCategory.PRIVACY, content_hash="sd-small"))
        report = await siphon_density(db, dry_run=False, cap=60)
        assert report["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_self_improvement_and_supers_excluded(self, db):
        from project_forge.engine.siphon import siphon_density

        for i in range(5):
            await db.save_idea(_idea(f"SI {i}", IdeaCategory.SELF_IMPROVEMENT, content_hash=f"sd-si-{i}"))
            await db.save_idea(
                _idea(
                    f"[SUPER] Bundle {i}",
                    IdeaCategory.MICRO_SAAS,
                    content_hash=f"sd-sup-{i}",
                )
            )
        report = await siphon_density(db, dry_run=False, cap=2)
        cur = await db.db.execute("SELECT COUNT(*) FROM ideas WHERE archived_reason = 'saturation_thin'")
        n_thinned = (await cur.fetchone())[0]
        assert n_thinned == 0, report

    @pytest.mark.asyncio
    async def test_restore_brings_thinned_back(self, db):
        from project_forge.engine.siphon import restore_dedup_archive, siphon_density

        cap = 6
        cat = await self._seed_over_cap(db, cap)
        await siphon_density(db, dry_run=False, cap=cap)
        restored = await restore_dedup_archive(db)
        assert restored == 4
        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE category = ? AND status NOT IN ('archived','rejected')",
            (cat.value,),
        )
        assert (await cur.fetchone())[0] == cap + 4

    @pytest.mark.asyncio
    async def test_siphon_all_includes_density_pass(self, db):
        from project_forge.engine.siphon import siphon_all

        report = await siphon_all(db, dry_run=True)
        assert "density" in report
        assert set(report.keys()) >= {"atomic", "supers", "verticals", "density", "total_archived"}


# --------------------------------------------------------------------------- #
# adaptive back-fill batch                                                    #
# --------------------------------------------------------------------------- #


class TestAdaptiveBackfill:
    @pytest.mark.asyncio
    async def test_keyless_fundability_backfill_bursts(self, db, monkeypatch):
        import project_forge.engine.fundability as fundability
        import project_forge.engine.llm_backend as llm_backend
        from project_forge.web import lifespan_scheduler as sched

        # Keyless everywhere: the runner's limit switch AND fundability's
        # own borderline-band refine (separate import binding).
        monkeypatch.setattr(llm_backend, "resolve_cheap_backend", lambda: None)
        monkeypatch.setattr(fundability, "resolve_cheap_backend", lambda: None)
        for i in range(8):
            await db.save_idea(_idea(f"Unscored {i}", IdeaCategory.MICRO_SAAS, content_hash=f"ab-{i}"))

        await sched._fire_fundability_score(db)
        cur = await db.db.execute("SELECT COUNT(*) FROM ideas WHERE fundability_score IS NOT NULL")
        assert (await cur.fetchone())[0] == 8  # burst > legacy batch of 5

    @pytest.mark.asyncio
    async def test_backend_present_keeps_small_batch(self, db, monkeypatch):
        import project_forge.engine.fundability as fundability
        import project_forge.engine.llm_backend as llm_backend
        from project_forge.web import lifespan_scheduler as sched

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value="not json")
        monkeypatch.setattr(llm_backend, "resolve_cheap_backend", lambda: backend)
        # Keep the refine path off so scoring stays instant-heuristic.
        monkeypatch.setattr(fundability, "resolve_cheap_backend", lambda: None)
        for i in range(8):
            await db.save_idea(_idea(f"Unscored {i}", IdeaCategory.MICRO_SAAS, content_hash=f"ab2-{i}"))

        await sched._fire_fundability_score(db)
        cur = await db.db.execute("SELECT COUNT(*) FROM ideas WHERE fundability_score IS NOT NULL")
        assert (await cur.fetchone())[0] == 5  # writer-lock discipline retained
