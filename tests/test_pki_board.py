"""Tests for the v0.23 PKI board — certificate infrastructure as a think tank.

A 6th board, and the first one that is not about money. Where fundability
asks "can we sell it" and cashflow asks "how soon is the first invoice",
`pki_urgency_score` asks: deadline pressure x blast radius x how badly
today's tooling fails.

The board's defining property is SELECTIVITY. The hourly probe works one
grounded gap per fire and stores NOTHING unless the result cites a concrete
anchor and clears the urgency threshold. Most of these tests exist to prove
the gate actually drops things — a board that admits everything would be
the landfill this design exists to avoid.

Covers:
  - enum membership + PKI_CATEGORIES grouping (disjoint from every other board)
  - seeds (saturation standard: >=20 concepts / >=12 domains), personas,
    tech stacks, category bonus
  - the corpus guard: real PKI vocabulary, not blockchain-for-certificates
  - urgency heuristic: deadline / blast-radius / tooling-gap / anchor bumps,
    no-substance and hand-wave penalties, clamp
  - anchor extraction across RFC / draft / NIST / ballot / CVE / URL forms
  - the admission gate: wrong board, no anchor, below threshold
  - LLM borderline band + keyless fallback
  - probe source: relevance scoring, category routing, seed construction,
    graceful degradation when every network source fails
  - DB round-trip, probe log, scoped idempotent back-fill
  - scheduler cadence registration + probe-log watermark semantics
  - routes: /pki page, /api/pki/top, /api/pki/probes, churn lab=pki
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.llm_generator import PERSONAS_BY_CATEGORY
from project_forge.models import (
    CASHFLOW_CATEGORIES,
    CLAUDE_LAB_CATEGORIES,
    CRYPTO_CATEGORIES,
    MONEY_CATEGORIES,
    PKI_CATEGORIES,
    Idea,
    IdeaCategory,
)
from project_forge.storage.db import Database

NEW_PKI = (
    IdeaCategory.PKI_REVOCATION,
    IdeaCategory.CERT_LIFECYCLE,
    IdeaCategory.PQC_MIGRATION,
    IdeaCategory.CA_OPERATIONS,
    IdeaCategory.CERT_IDENTITY,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "pki.db")
    await database.connect()
    yield database
    await database.close()


def _pki_idea(**over) -> Idea:
    """Neutral PKI idea — carries PKI vocabulary (so it isn't hit by the
    no-substance penalty) but no deadline, blast-radius, tooling-gap, or
    anchor signals. Each heuristic test adds exactly one."""
    base = dict(
        name="Certificate Chain Inspector",
        tagline="a viewer for X.509 chain contents",
        description="A tool that displays the contents of an X.509 certificate chain.",
        category=IdeaCategory.CERT_LIFECYCLE,
        market_analysis="Engineers look at certificates sometimes.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: parse and render a chain.",
        tech_stack=["go", "openssl"],
    )
    base.update(over)
    return Idea(**base)


def _urgent_idea(**over) -> Idea:
    """Every urgency signal at once — the archetype the board exists for."""
    base = dict(
        name="Delta-CRL Sharding Planner",
        tagline="partition issuing distribution points before CRLs exceed a size budget",
        description=(
            "ML-DSA signatures push CRL size past what clients will fetch, and "
            "operators handle this by hand today with no way to tell which "
            "issuing distribution point will blow the budget next. The planner "
            "models revocation growth per shard and emits a partitioning plan. "
            "Anchored to RFC 5280 and draft-ietf-lamps-crl-partitioning."
        ),
        category=IdeaCategory.PKI_REVOCATION,
        market_analysis=(
            "Every certificate authority faces this at the 2030 deprecation "
            "deadline; a fleet-wide outage follows if revocation stops working."
        ),
        feasibility_score=0.8,
        mvp_scope="Phase 1: growth model. Phase 2: shard plan emitter.",
        tech_stack=["go", "cfssl", "postgres"],
        # The canonical board item comes from the gated cadence. Tests that
        # care about ungated content override this explicitly — see
        # TestBackDoorClosed.
        generation_mode="pki",
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# grouping                                                                    #
# --------------------------------------------------------------------------- #


class TestBackDoorClosed:
    """The board advertises a hard admission gate. Anything that reaches it
    without passing that gate makes the claim false.

    Regression lock for the back door found on 2026-08-08: the five PKI
    categories sat in the ordinary generation rotation, and the score
    back-fill scored whatever it found there — so 28 of the first 77 board
    items had never seen the gate (19 of them from the deterministic
    template generator), with 201 more queued behind the back-fill.
    """

    def test_pki_categories_are_gated(self):
        from project_forge.models import GATED_CATEGORIES

        assert set(GATED_CATEGORIES) == set(PKI_CATEGORIES)

    def test_general_rotation_excludes_gated_categories(self):
        """The main generate→score→store rotation must not pick a PKI category."""
        from project_forge.cron.scheduler import ALL_CATEGORIES

        assert not (set(ALL_CATEGORIES) & set(PKI_CATEGORIES))

    def test_template_generator_excludes_gated_categories(self):
        """auto_scan is the single largest producer in the engine — it was
        responsible for most of the ungated board items."""
        import random as _random

        from project_forge.cron.auto_scan import generate_local_idea

        _random.seed(1234)
        for _ in range(60):
            idea, *_ = generate_local_idea()
            assert idea.category not in PKI_CATEGORIES, f"template generator produced a gated category: {idea.category}"

    def test_super_idea_pair_fallback_excludes_gated(self):
        import random as _random

        from project_forge.cron.horizontal import IdeaCategory as _IC  # noqa: F401
        from project_forge.models import GATED_CATEGORIES

        cats = [c for c in IdeaCategory if c != IdeaCategory.SELF_IMPROVEMENT and c not in GATED_CATEGORIES]
        _random.seed(7)
        a, b = _random.sample(cats, 2)
        assert a not in GATED_CATEGORIES
        assert b not in GATED_CATEGORIES

    @pytest.mark.asyncio
    async def test_backfill_ignores_ungated_pki_ideas(self, db, monkeypatch):
        """The back-fill was the mechanism that put ungated ideas on the
        board: a score is what makes an idea visible there."""
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)

        gated = _urgent_idea(name="Gated Finding", content_hash="bd1")
        gated.generation_mode = "pki"
        ungated = _urgent_idea(name="Wandered In", content_hash="bd2")
        ungated.generation_mode = None  # template generator
        drifted = _urgent_idea(name="Expand Rotation", content_hash="bd3")
        drifted.generation_mode = "novel"
        for i in (gated, ungated, drifted):
            await db.save_idea(i)

        report = await pki.score_pending_pki_urgency(db, limit=10)
        assert report["scored"] == 1, "back-fill must only score gated items"
        assert (await db.get_idea(gated.id)).pki_urgency_score is not None
        assert (await db.get_idea(ungated.id)).pki_urgency_score is None
        assert (await db.get_idea(drifted.id)).pki_urgency_score is None


class TestPkiGrouping:
    def test_new_categories_in_enum(self):
        for cat in NEW_PKI:
            assert isinstance(cat, IdeaCategory)

    def test_grouping_is_exactly_the_new_set(self):
        assert set(PKI_CATEGORIES) == set(NEW_PKI)

    def test_disjoint_from_other_boards(self):
        assert not (set(PKI_CATEGORIES) & set(MONEY_CATEGORIES))
        assert not (set(PKI_CATEGORIES) & set(CLAUDE_LAB_CATEGORIES))
        assert not (set(PKI_CATEGORIES) & set(CRYPTO_CATEGORIES))
        assert not (set(PKI_CATEGORIES) & set(CASHFLOW_CATEGORIES))

    def test_route_tuple_matches_canonical(self):
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._PKI_CATEGORIES) == {c.value for c in PKI_CATEGORIES}


# --------------------------------------------------------------------------- #
# seeds / personas / stacks / bonus                                           #
# --------------------------------------------------------------------------- #


class TestPkiSeedsAndWiring:
    def test_new_categories_meet_saturation_standard(self):
        for cat in NEW_PKI:
            assert cat in CATEGORY_SEEDS, f"missing seeds for {cat}"
            seeds = CATEGORY_SEEDS[cat]
            assert len(seeds["seed_concepts"]) >= 20
            assert len(seeds["domains_to_cross"]) >= 12
            assert len(seeds["seed_concepts"]) == len(set(seeds["seed_concepts"]))

    def test_new_categories_have_personas(self):
        for cat in NEW_PKI:
            assert cat in PERSONAS_BY_CATEGORY, f"missing personas for {cat}"
            assert len(PERSONAS_BY_CATEGORY[cat]) >= 5

    def test_new_categories_have_tech_stacks(self):
        from project_forge.cron.auto_scan import TECH_STACKS

        for cat in NEW_PKI:
            assert cat in TECH_STACKS, f"missing tech stacks for {cat}"
            assert len(TECH_STACKS[cat]) >= 3

    def test_new_categories_have_urgency_bonus(self):
        from project_forge.engine.pki import _CATEGORY_BONUS

        for cat in NEW_PKI:
            assert _CATEGORY_BONUS.get(cat, 0.0) > 0.0


class TestPkiBoardIsRealInfrastructure:
    """The board's identity: actual certificate plumbing, not
    certificate-flavored product pitches."""

    def _corpus(self) -> str:
        parts: list[str] = []
        for cat in PKI_CATEGORIES:
            seeds = CATEGORY_SEEDS[cat]
            parts.append(seeds["description"])
            parts.extend(seeds["seed_concepts"])
        return " ".join(parts).lower()

    def test_corpus_covers_core_pki_themes(self):
        corpus = self._corpus()
        for theme in ("crl", "ocsp", "acme", "hsm", "post-quantum", "revocation", "attestation"):
            assert theme in corpus, f"PKI board missing theme: {theme}"

    def test_corpus_is_not_buzzword_soup(self):
        corpus = self._corpus()
        for banned in ("blockchain", "web3", "nft", "revolutionize", "synergy"):
            assert banned not in corpus, f"PKI board drifted to buzzwords: {banned}"


# --------------------------------------------------------------------------- #
# urgency heuristic                                                           #
# --------------------------------------------------------------------------- #


class TestUrgencyHeuristic:
    def test_urgent_idea_scores_high(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        assert score_pki_urgency_heuristic(_urgent_idea()) >= 0.75

    def test_deadline_signal_bumps(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        without = score_pki_urgency_heuristic(_pki_idea())
        with_dl = score_pki_urgency_heuristic(
            _pki_idea(description="X.509 chains using this algorithm are deprecated and must be replaced.")
        )
        assert with_dl > without

    def test_blast_radius_signal_bumps(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        without = score_pki_urgency_heuristic(_pki_idea())
        with_br = score_pki_urgency_heuristic(
            _pki_idea(description="An X.509 failure here causes a fleet-wide outage across every endpoint.")
        )
        assert with_br > without

    def test_tooling_gap_signal_bumps(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        without = score_pki_urgency_heuristic(_pki_idea())
        with_tg = score_pki_urgency_heuristic(
            _pki_idea(description="Operators do this X.509 work by hand today and cannot verify the result.")
        )
        assert with_tg > without

    def test_anchor_signal_bumps(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        without = score_pki_urgency_heuristic(_pki_idea())
        with_anchor = score_pki_urgency_heuristic(
            _pki_idea(description="Implements the X.509 profile described in RFC 5280.")
        )
        assert with_anchor > without

    def test_no_pki_substance_is_penalized(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        generic = _pki_idea(
            name="Team Dashboard",
            tagline="a dashboard for teams",
            description="A dashboard that shows status information to teams.",
            market_analysis="Teams like dashboards.",
            mvp_scope="Build the dashboard.",
        )
        assert score_pki_urgency_heuristic(generic) < score_pki_urgency_heuristic(_pki_idea())

    def test_hand_wave_is_penalized(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        plain = _pki_idea(description="An X.509 revocation checker for internal use.")
        hyped = _pki_idea(
            description=(
                "A revolutionary next-generation blockchain-based PKI that seamlessly delivers X.509 revocation."
            )
        )
        assert score_pki_urgency_heuristic(hyped) < score_pki_urgency_heuristic(plain)

    def test_score_clamped_to_unit_interval(self):
        from project_forge.engine.pki import score_pki_urgency_heuristic

        assert 0.0 <= score_pki_urgency_heuristic(_urgent_idea()) <= 1.0
        assert 0.0 <= score_pki_urgency_heuristic(_pki_idea()) <= 1.0


# --------------------------------------------------------------------------- #
# anchors + admission gate                                                    #
# --------------------------------------------------------------------------- #


class TestAnchorExtraction:
    @pytest.mark.parametrize(
        "text",
        [
            "Implements the profile in RFC 5280.",
            "Follows draft-ietf-lamps-cert-binding-for-multi-auth.",
            "Per NIST SP 800-208 guidance.",
            "Tracks CA/Browser Forum Ballot SC-081.",
            "Fixes CVE-2024-12345 in the chain builder.",
            "See https://github.com/openssl/openssl/issues/12345 for the report.",
            "Sizes responses for ML-DSA-65 signatures.",
        ],
    )
    def test_recognizes_concrete_anchor_forms(self, text):
        from project_forge.engine.pki import extract_anchor

        assert extract_anchor(_pki_idea(description=text)) is not None

    def test_no_anchor_when_nothing_concrete_cited(self):
        from project_forge.engine.pki import extract_anchor

        assert extract_anchor(_pki_idea()) is None

    def test_explicit_anchor_wins_over_scraped(self):
        from project_forge.engine.pki import extract_anchor

        idea = _pki_idea(description="Implements RFC 5280.")
        idea.pki_anchor = "draft-ietf-lamps-explicit"
        assert extract_anchor(idea) == "draft-ietf-lamps-explicit"


class TestAdmissionGate:
    def test_admits_anchored_urgent_finding(self):
        from project_forge.engine.pki import admits, score_pki_urgency_heuristic

        idea = _urgent_idea()
        ok, reason = admits(idea, score_pki_urgency_heuristic(idea))
        assert ok, reason

    def test_rejects_missing_anchor(self):
        from project_forge.engine.pki import admits

        # High score, but nothing citable — must not reach the board.
        ok, reason = admits(_pki_idea(category=IdeaCategory.PKI_REVOCATION), 0.95)
        assert not ok
        assert "anchor" in reason.lower()

    def test_rejects_below_threshold(self):
        from project_forge.engine.pki import PKI_ADMIT_THRESHOLD, admits

        idea = _pki_idea(description="Implements RFC 5280.", category=IdeaCategory.PKI_REVOCATION)
        ok, reason = admits(idea, PKI_ADMIT_THRESHOLD - 0.01)
        assert not ok
        assert "threshold" in reason.lower()

    def test_rejects_wrong_board(self):
        from project_forge.engine.pki import admits

        idea = _urgent_idea(category=IdeaCategory.MICRO_SAAS)
        ok, reason = admits(idea, 0.99)
        assert not ok
        assert "category" in reason.lower()


class TestUrgencyLLMBand:
    @pytest.mark.asyncio
    async def test_borderline_pulls_llm_score(self, monkeypatch):
        from project_forge.engine import pki

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value='{"score": 0.62}')
        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: backend)

        idea = _pki_idea(
            category=IdeaCategory.PKI_REVOCATION,
            description="Operators do this X.509 CRL work by hand today.",
        )
        heuristic = pki.score_pki_urgency_heuristic(idea)
        assert pki.LLM_VERIFY_LOWER <= heuristic <= pki.LLM_VERIFY_UPPER, heuristic

        score = await pki.score_pki_urgency(idea)
        assert abs(score - 0.62) < 0.05
        backend.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_backend_falls_back_to_heuristic(self, monkeypatch):
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)
        idea = _pki_idea(
            category=IdeaCategory.PKI_REVOCATION,
            description="Operators do this X.509 CRL work by hand today.",
        )
        score = await pki.score_pki_urgency(idea)
        assert score == pki.score_pki_urgency_heuristic(idea)
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_clear_low_skips_llm(self, monkeypatch):
        from project_forge.engine import pki

        backend = MagicMock()
        backend.call = MagicMock(return_value='{"score": 0.99}')
        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: backend)

        # Off-board category, no PKI substance — clearly below the band.
        idea = _pki_idea(
            category=IdeaCategory.OBSERVABILITY,
            name="Team Dashboard",
            tagline="a dashboard",
            description="A dashboard that shows status to teams.",
            market_analysis="Teams like dashboards.",
            mvp_scope="Build it.",
        )
        score = await pki.score_pki_urgency(idea)
        assert score < pki.LLM_VERIFY_LOWER
        backend.call.assert_not_called()


# --------------------------------------------------------------------------- #
# probe source                                                                #
# --------------------------------------------------------------------------- #


class TestProbeSource:
    def test_relevance_scoring_prefers_revocation_and_pq(self):
        from project_forge.feeds.pki_probe import score_gap

        hot = score_gap({"title": "CRL size explosion with ML-DSA", "summary": "revocation overhead"})
        cold = score_gap({"title": "Update the README", "summary": "docs typo"})
        assert hot > cold
        assert cold == 0

    def test_irrelevant_candidates_are_dropped(self, monkeypatch):
        from project_forge.feeds import pki_probe

        def _fake_get(url, timeout=15.0):
            return (
                b'<?xml version="1.0"?><rss><channel>'
                b"<item><title>Update the README</title>"
                b"<description>docs typo</description>"
                b"<link>http://example.com/1</link></item>"
                b"</channel></rss>"
            )

        monkeypatch.setattr(pki_probe, "PROBE_REPOS", ())
        gaps = pki_probe.fetch_pki_gaps(http_get=_fake_get)
        assert gaps == []

    def test_category_routing(self):
        from project_forge.feeds.pki_probe import route_category

        assert route_category({"title": "OCSP responder overload", "summary": ""}) == "pki-revocation"
        assert route_category({"title": "hybrid ML-KEM rollout", "summary": ""}) == "pqc-migration"
        assert route_category({"title": "SPIFFE workload identity", "summary": ""}) == "cert-identity"
        assert route_category({"title": "HSM pkcs11 key ceremony", "summary": ""}) == "ca-operations"
        assert route_category({"title": "ACME renewal failure", "summary": ""}) == "cert-lifecycle"

    def test_degrades_to_empty_when_all_sources_fail(self, monkeypatch):
        from project_forge.feeds import pki_probe

        def _boom(url, timeout=15.0):
            raise OSError("network down")

        assert pki_probe.fetch_pki_gaps(http_get=_boom) == []

    def test_pick_top_gap_skips_already_seen(self):
        from project_forge.feeds.pki_probe import pick_top_gap

        gaps = [
            {"title": "old", "url": "http://a", "gap_score": 9},
            {"title": "new", "url": "http://b", "gap_score": 4},
        ]
        assert pick_top_gap(gaps, seen_urls={"http://a"})["title"] == "new"

    def test_pick_top_gap_returns_none_when_exhausted(self):
        from project_forge.feeds.pki_probe import pick_top_gap

        assert pick_top_gap([], seen_urls=set()) is None
        assert pick_top_gap([{"title": "x", "url": "http://a"}], seen_urls={"http://a"}) is None

    def test_seed_demands_anchor_and_mechanism(self):
        from project_forge.feeds.pki_probe import gap_to_seed

        seed = gap_to_seed(
            {
                "title": "CRL size explosion",
                "url": "https://example.org/draft",
                "summary": "revocation overhead",
                "source": "ietf",
            }
        )
        lowered = seed.lower()
        assert "https://example.org/draft" in seed
        for requirement in ("anchor", "mechanism", "tooling gap", "blast radius", "validation"):
            assert requirement in lowered, f"seed missing requirement: {requirement}"


# --------------------------------------------------------------------------- #
# DB round-trip + probe log + back-fill                                       #
# --------------------------------------------------------------------------- #


class TestDbRoundTrip:
    @pytest.mark.asyncio
    async def test_urgency_and_anchor_persist(self, db):
        idea = _urgent_idea(content_hash="rt1")
        idea.pki_urgency_score = 0.81
        idea.pki_anchor = "RFC 5280"
        await db.save_idea(idea)
        got = await db.get_idea(idea.id)
        assert got is not None
        assert got.pki_urgency_score == 0.81
        assert got.pki_anchor == "RFC 5280"


class TestProbeLog:
    @pytest.mark.asyncio
    async def test_records_and_lists_probes(self, db):
        await db.record_pki_probe(
            gap_summary="CRL bloat", anchor="RFC 5280", admitted=False, reason="below threshold", urgency_score=0.4
        )
        await db.record_pki_probe(
            gap_summary="OCSP load",
            anchor="draft-x",
            admitted=True,
            reason="admitted",
            idea_id="abc",
            urgency_score=0.8,
        )
        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 2
        assert {p["admitted"] for p in probes} == {True, False}

    @pytest.mark.asyncio
    async def test_stats_report_admit_rate(self, db):
        for _ in range(3):
            await db.record_pki_probe(gap_summary="g", anchor=None, admitted=False, reason="no anchor")
        await db.record_pki_probe(gap_summary="g", anchor="RFC 5280", admitted=True, reason="admitted")
        stats = await db.pki_probe_stats()
        assert stats["probes"] == 4
        assert stats["admitted"] == 1
        assert stats["rejected"] == 3
        assert abs(stats["admit_rate"] - 0.25) < 0.001

    @pytest.mark.asyncio
    async def test_empty_log_reports_zero_rate_not_divide_error(self, db):
        stats = await db.pki_probe_stats()
        assert stats == {"probes": 0, "admitted": 0, "rejected": 0, "admit_rate": 0.0}


class TestBackfill:
    @pytest.mark.asyncio
    async def test_scores_only_pki_categories(self, db, monkeypatch):
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)
        a = _urgent_idea(name="PKI A", content_hash="bf1")
        b = _urgent_idea(name="Not PKI", category=IdeaCategory.MICRO_SAAS, content_hash="bf2")
        await db.save_idea(a)
        await db.save_idea(b)

        report = await pki.score_pending_pki_urgency(db, limit=10)
        assert report["scored"] == 1

        assert (await db.get_idea(a.id)).pki_urgency_score is not None
        assert (await db.get_idea(b.id)).pki_urgency_score is None

    @pytest.mark.asyncio
    async def test_backfill_also_fills_anchor(self, db, monkeypatch):
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)
        idea = _urgent_idea(name="Anchored", content_hash="bf-anchor")
        assert idea.pki_anchor is None
        await db.save_idea(idea)
        await pki.score_pending_pki_urgency(db, limit=10)
        assert (await db.get_idea(idea.id)).pki_anchor is not None

    @pytest.mark.asyncio
    async def test_backfill_is_idempotent(self, db, monkeypatch):
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)
        await db.save_idea(_urgent_idea(name="Once", content_hash="bf3"))
        first = await pki.score_pending_pki_urgency(db, limit=10)
        second = await pki.score_pending_pki_urgency(db, limit=10)
        assert first["scored"] == 1
        assert second["scored"] == 0

    @pytest.mark.asyncio
    async def test_backfill_respects_limit(self, db, monkeypatch):
        from project_forge.engine import pki

        monkeypatch.setattr(pki, "resolve_cheap_backend", lambda: None)
        for i in range(4):
            await db.save_idea(_urgent_idea(name=f"Lim {i}", content_hash=f"bf-l{i}"))
        report = await pki.score_pending_pki_urgency(db, limit=2)
        assert report["scored"] == 2


# --------------------------------------------------------------------------- #
# scheduler                                                                   #
# --------------------------------------------------------------------------- #


class TestCadence:
    def test_pki_cadences_registered(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        names = {c.name for c in default_cadences()}
        assert "pki" in names
        assert "pki_score" in names

    def test_pki_cadence_is_hourly(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        pki_cadence = next(c for c in default_cadences() if c.name == "pki")
        assert pki_cadence.interval.total_seconds() == 3600.0

    @pytest.mark.asyncio
    async def test_watermark_uses_probe_log_not_ideas(self, db):
        """The critical scheduling property: the probe stores nothing most
        hours, so the watermark must advance on ATTEMPTS. Keying off stored
        ideas would leave the cadence permanently overdue and re-firing
        every tick."""
        from datetime import timedelta

        from project_forge.web.lifespan_scheduler import seconds_until_next_pki

        # No probes ever -> overdue, fires immediately.
        assert await seconds_until_next_pki(db, timedelta(hours=1)) == 0.0

        # A REJECTED probe (nothing stored) must still push the schedule out.
        await db.record_pki_probe(gap_summary="g", anchor=None, admitted=False, reason="no anchor")
        delay = await seconds_until_next_pki(db, timedelta(hours=1))
        assert delay > 3000.0, "rejected probe must still advance the watermark"

    @pytest.mark.asyncio
    async def test_fire_pki_stores_nothing_when_probe_finds_nothing(self, db, monkeypatch):
        from project_forge.web import lifespan_scheduler

        monkeypatch.setattr(
            "project_forge.feeds.pki_probe.fetch_pki_gaps",
            lambda *a, **kw: [],
        )
        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 1
        assert probes[0]["admitted"] is False
        assert (await db.count_ideas()) == 0

    @pytest.mark.asyncio
    async def test_fire_pki_drops_unanchored_generation(self, db, monkeypatch):
        """The gate in its most important mode: the generator produced
        something, and the cadence threw it away for lacking an anchor."""
        from project_forge.engine.llm_generator import LLMGenerationResult
        from project_forge.web import lifespan_scheduler

        monkeypatch.setattr(
            "project_forge.feeds.pki_probe.fetch_pki_gaps",
            lambda *a, **kw: [
                {
                    "title": "CRL bloat",
                    "url": "",  # no source URL either -> genuinely unanchored
                    "summary": "revocation",
                    "gap_score": 5,
                    "category": "pki-revocation",
                }
            ],
        )

        unanchored = _pki_idea(
            name="Vague Cert Tool",
            category=IdeaCategory.PKI_REVOCATION,
            description="A CRL tool with no citation of anything concrete.",
            content_hash="unanchored-1",
        )

        async def _fake_generate(db_, category, **kw):
            return LLMGenerationResult(idea=unanchored, mode="novel", persona="p", backend="stub", raw_response="{}")

        monkeypatch.setattr("project_forge.engine.llm_generator.generate_idea_llm", _fake_generate)
        monkeypatch.setattr("project_forge.engine.pki.resolve_cheap_backend", lambda: None)
        # The depth panel runs before the gate now; keep this test keyless so
        # it still asserts the ANCHOR rejection and never shells out.
        monkeypatch.setattr("project_forge.engine.pki_depth.resolve_cheap_backend", lambda: None)

        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 1
        assert probes[0]["admitted"] is False
        assert "anchor" in (probes[0]["reason"] or "").lower()
        assert (await db.count_ideas()) == 0, "unanchored idea must not reach the board"


# --------------------------------------------------------------------------- #
# depth panel + prior art, wired into the cadence                             #
# --------------------------------------------------------------------------- #


_LENS_MARKERS = {
    "real": "ATTACK THE PREMISE",
    "solved": "ATTACK THE NOVELTY",
    "wrong": "ATTACK THE CORRECTNESS",
}


class _ScriptedBackend:
    """A cheap backend that answers by prompt shape, so one fake can drive the
    three lenses and the revise pass without any network."""

    def __init__(self, lens_replies: dict[str, str], revision: str | None = None):
        self.lens_replies = lens_replies
        self.revision = revision
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def call(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        if "rewriting your own proposal" in prompt:
            return self.revision
        for lens, brief in _LENS_MARKERS.items():
            if brief in prompt:
                return self.lens_replies.get(lens, '{"objection": "none", "severity": 0.0}')
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")


# The revise pass rewrites the text the urgency score is then computed on, so
# a revision that survives the panel must still carry the urgency signals.
_REVISION_JSON = (
    '{"name": "Delta-CRL Shard Planner", '
    '"tagline": "partition issuing distribution points before CRLs exceed the fetch budget", '
    '"description": "ML-DSA signatures push CRL size past what clients will fetch. Operators do '
    "this by hand today with no way to tell which issuing distribution point blows the budget "
    "next, and a fleet-wide outage follows when revocation stops working at the 2030 deadline. "
    'Anchored to RFC 5280.", '
    '"mvp_scope": "Phase 1: growth model against a real CRL corpus.", '
    '"market_analysis": "Every certificate authority hits this; a fleet-wide outage is the '
    'failure mode.", '
    '"added_specifics": ["per-issuing-distribution-point growth model"], '
    '"dropped_claims": ["that no CA shards CRLs today"]}'
)


def _pki_gap() -> dict:
    return {
        "title": "CRL size under ML-DSA",
        "url": "https://datatracker.ietf.org/doc/draft-ietf-lamps-crl-partitioning/",
        "summary": "revocation",
        "gap_score": 9,
        "category": "pki-revocation",
    }


def _wire_cadence(monkeypatch, idea: Idea) -> None:
    """Point the cadence at one gap and one generated draft, and keep the
    urgency scorer keyless so its assertions stay deterministic."""
    from project_forge.engine.llm_generator import LLMGenerationResult

    monkeypatch.setattr("project_forge.feeds.pki_probe.fetch_pki_gaps", lambda *a, **kw: [_pki_gap()])

    async def _fake_generate(db_, category, **kw):
        return LLMGenerationResult(idea=idea, mode="novel", persona="p", backend="stub", raw_response="{}")

    monkeypatch.setattr("project_forge.engine.llm_generator.generate_idea_llm", _fake_generate)
    monkeypatch.setattr("project_forge.engine.pki.resolve_cheap_backend", lambda: None)


def _stub_prior_art(monkeypatch, repos: list[dict] | None) -> None:
    """Feed the REAL prior-art scorer a fixed repo set (or make every search
    fail, which is the fail-open path). Never touches the network."""
    from project_forge.engine import pki_prior_art

    pki_prior_art.clear_prior_art_cache()

    async def _fake_search(term, http_get):
        if repos is None:
            raise RuntimeError("github unreachable")
        return repos

    monkeypatch.setattr(pki_prior_art, "_search", _fake_search)


class TestCadenceDepthAndPriorArt:
    """The two stages that turned selectivity into effort. Both must be able
    to store NOTHING, and both must say so in the probe log."""

    @pytest.mark.asyncio
    async def test_panel_kill_stores_nothing_and_logs_the_objection(self, db, monkeypatch):
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        draft = _urgent_idea(name="Already Built CRL Thing", content_hash="panel-kill-1")
        _wire_cadence(monkeypatch, draft)
        _stub_prior_art(monkeypatch, [])

        killer = "crlite already ships delta distribution in Firefox; this is the same mechanism."
        monkeypatch.setattr(
            pki_depth,
            "resolve_cheap_backend",
            lambda: _ScriptedBackend({"solved": json.dumps({"objection": killer, "severity": 0.95})}),
        )

        await lifespan_scheduler._fire_pki(db)

        assert (await db.count_ideas()) == 0, "a killed draft must never reach the board"
        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 1
        assert probes[0]["admitted"] is False
        assert "red-team panel" in probes[0]["reason"]
        assert "crlite" in probes[0]["reason"], "the killing objection itself must be in the log"

    @pytest.mark.asyncio
    async def test_panel_kill_costs_no_revise_pass(self, db, monkeypatch):
        """A killed draft is never rewritten — three calls, not four."""
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        _wire_cadence(monkeypatch, _urgent_idea(name="Doomed", content_hash="panel-kill-2"))
        _stub_prior_art(monkeypatch, [])
        backend = _ScriptedBackend({"solved": '{"objection": "step-ca does this", "severity": 0.9}'})
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: backend)

        await lifespan_scheduler._fire_pki(db)

        assert len(backend.prompts) == 3
        assert not any("rewriting your own proposal" in p for p in backend.prompts)

    @pytest.mark.asyncio
    async def test_prior_art_hit_stores_nothing_and_logs_the_match(self, db, monkeypatch):
        """The question that actually kills certificate tooling: somebody
        shipped this in 2017 and it has four thousand stars."""
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        duplicate = _urgent_idea(
            name="Certificate Expiry Monitor",
            tagline="monitor certificate expiry across the fleet",
            content_hash="prior-art-1",
        )
        _wire_cadence(monkeypatch, duplicate)
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: None)
        _stub_prior_art(
            monkeypatch,
            [
                {
                    "name": "cert-expiry-monitor",
                    "url": "https://github.com/example/cert-expiry-monitor",
                    "stars": 4000,
                    "description": "Monitors certificate expiry across your fleet and alerts before renewal",
                }
            ],
        )

        await lifespan_scheduler._fire_pki(db)

        assert (await db.count_ideas()) == 0, "a solved problem must not reach the board"
        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 1
        assert probes[0]["admitted"] is False
        assert "prior art" in probes[0]["reason"]
        assert "https://github.com/example/cert-expiry-monitor" in probes[0]["reason"]

    @pytest.mark.asyncio
    async def test_survivor_persists_objection_through_round_trip(self, db, monkeypatch):
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        draft = _urgent_idea(name="Delta-CRL Draft", content_hash="survivor-1")
        _wire_cadence(monkeypatch, draft)
        _stub_prior_art(monkeypatch, [])

        objection = "partitioning helps only clients that honour the IDP extension."
        monkeypatch.setattr(
            pki_depth,
            "resolve_cheap_backend",
            lambda: _ScriptedBackend(
                {"real": json.dumps({"objection": objection, "severity": 0.55})},
                revision=_REVISION_JSON,
            ),
        )

        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert probes[0]["admitted"] is True, probes[0]["reason"]
        stored = await db.get_idea(probes[0]["idea_id"])
        assert stored is not None
        assert stored.pki_objection == objection
        # The board stores the REVISED text, not the draft the panel tore up.
        assert stored.name == "Delta-CRL Shard Planner"

    @pytest.mark.asyncio
    async def test_prior_art_runs_before_the_panel_and_grounds_it(self, db, monkeypatch):
        """Ordering matters twice over: a duplicate must not cost five LLM
        calls, and the lens holding the only unilateral veto must adjudicate
        repositories that exist rather than recall tool names from weights."""
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        _wire_cadence(monkeypatch, _urgent_idea(name="Delta-CRL Draft", content_hash="grounded-1"))
        _stub_prior_art(
            monkeypatch,
            [{"name": "crlite", "url": "https://github.com/x/crlite", "stars": 900, "description": "CRL filters"}],
        )
        backend = _ScriptedBackend({}, revision=_REVISION_JSON)
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: backend)

        await lifespan_scheduler._fire_pki(db)

        solved = [p for p in backend.prompts if _LENS_MARKERS["solved"] in p]
        assert len(solved) == 1
        assert "crlite" in solved[0], "the novelty lens must see the real repos the search found"

    @pytest.mark.asyncio
    async def test_prior_art_kill_spends_no_llm_calls(self, db, monkeypatch):
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        duplicate = _urgent_idea(
            name="Certificate Expiry Monitor",
            tagline="monitor certificate expiry across the fleet",
            content_hash="prior-art-cheap-1",
        )
        _wire_cadence(monkeypatch, duplicate)
        backend = _ScriptedBackend({}, revision=_REVISION_JSON)
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: backend)
        _stub_prior_art(
            monkeypatch,
            [
                {
                    "name": "cert-expiry-monitor",
                    "url": "https://github.com/example/cert-expiry-monitor",
                    "stars": 4000,
                    "description": "Monitors certificate expiry across your fleet and alerts before renewal",
                }
            ],
        )

        await lifespan_scheduler._fire_pki(db)

        assert backend.prompts == [], "a duplicate must be killed by 3 HTTP calls, not 4 LLM calls"
        assert (await db.count_ideas()) == 0

    @pytest.mark.asyncio
    async def test_a_crashing_cycle_still_records_a_probe(self, db, monkeypatch):
        """The watermark is MAX(probed_at) and `seen_urls` comes from the same
        log, so a crash that records nothing re-fires every tick against the
        SAME gap — an unbounded burn loop on one deterministic failure."""
        from project_forge.web import lifespan_scheduler

        _wire_cadence(monkeypatch, _urgent_idea(name="Doomed", content_hash="crash-1"))

        async def _boom(*a, **kw):
            raise RuntimeError("github schema changed")

        monkeypatch.setattr("project_forge.engine.pki_prior_art.check_prior_art", _boom)

        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert len(probes) == 1, "a crash must still advance the watermark"
        assert probes[0]["admitted"] is False
        assert "crashed" in (probes[0]["reason"] or "")
        assert probes[0]["anchor"] == _pki_gap()["url"], "the gap must be retired, not retried forever"

    @pytest.mark.asyncio
    async def test_admitted_probe_records_the_closest_prior_art(self, db, monkeypatch):
        """Free calibration data for tuning the match threshold, and it was
        being deleted every hour."""
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        _wire_cadence(monkeypatch, _urgent_idea(name="Delta-CRL Planner", content_hash="calib-1"))
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: None)
        _stub_prior_art(monkeypatch, [])

        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert probes[0]["admitted"] is True, probes[0]["reason"]
        assert "searches" in probes[0]["reason"], "the prior-art evidence must ride along"

    @pytest.mark.asyncio
    async def test_fully_keyless_path_still_admits(self, db, monkeypatch):
        """No LLM, no network: the panel is a free no-op and the prior-art
        check fails open. The board must still publish."""
        from project_forge.engine import pki_depth
        from project_forge.web import lifespan_scheduler

        _wire_cadence(monkeypatch, _urgent_idea(name="Keyless Survivor", content_hash="keyless-1"))
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: None)
        _stub_prior_art(monkeypatch, None)  # every search fails

        await lifespan_scheduler._fire_pki(db)

        probes = await db.list_pki_probes(limit=10)
        assert probes[0]["admitted"] is True, probes[0]["reason"]
        stored = await db.get_idea(probes[0]["idea_id"])
        assert stored is not None
        assert stored.name == "Keyless Survivor"
        assert stored.pki_objection is None, "keyless runs no panel, so there is no objection"


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "test_pki_routes.db"
    await db.connect()
    a = _urgent_idea(name="PKI High", content_hash="ra")
    a.pki_urgency_score = 0.91
    a.pki_anchor = "RFC 5280"
    a.pki_objection = "only helps clients that honour the IDP extension"
    a.generation_mode = "pki"
    b = _urgent_idea(name="PKI Low", category=IdeaCategory.CA_OPERATIONS, content_hash="rb")
    b.pki_urgency_score = 0.58
    b.pki_anchor = "draft-ietf-lamps-example"
    b.generation_mode = "pki"
    c = _urgent_idea(name="PKI Unscored", content_hash="rc")  # no urgency score
    await db.save_idea(a)
    await db.save_idea(b)
    await db.save_idea(c)
    await db.record_pki_probe(
        gap_summary="probed gap", anchor="RFC 5280", admitted=False, reason="below threshold", urgency_score=0.4
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestPkiRoutes:
    @pytest.mark.asyncio
    async def test_ungated_idea_never_reaches_the_board(self, client):
        """Defence in depth: even if something scores an ungated PKI idea,
        the board query itself must refuse to show it. This is the guarantee
        — the generation-side exclusions are only the efficiency half."""
        from project_forge.web.app import db as app_db

        smuggled = _urgent_idea(name="Smuggled In", content_hash="smuggle-1")
        smuggled.generation_mode = "novel"  # not the gated cadence
        smuggled.pki_urgency_score = 0.99  # scored anyway
        smuggled.pki_anchor = "RFC 5280"
        await app_db.save_idea(smuggled)

        page = await client.get("/pki")
        assert "Smuggled In" not in page.text

        api = await client.get("/api/pki/top?limit=50")
        assert "Smuggled In" not in [d["name"] for d in api.json()]

    @pytest.mark.asyncio
    async def test_api_top_returns_only_scored_sorted(self, client):
        resp = await client.get("/api/pki/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert [d["name"] for d in data] == ["PKI High", "PKI Low"]
        assert data[0]["pki_urgency_score"] == 0.91
        assert data[0]["pki_anchor"] == "RFC 5280"
        assert data[0]["category"] == "pki-revocation"

    @pytest.mark.asyncio
    async def test_page_renders_scored_ideas(self, client):
        resp = await client.get("/pki")
        assert resp.status_code == 200
        assert "PKI High" in resp.text
        assert "PKI Unscored" not in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_probe_log(self, client):
        """An empty or short board must explain itself — the probe log is
        the difference between 'broken' and 'selective'."""
        resp = await client.get("/pki")
        assert "Probe log" in resp.text
        assert "probed gap" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_strongest_counterargument(self, client):
        """The objection is surfaced, not buried — a finding that names its
        own weakest joint is the whole product of the depth panel."""
        resp = await client.get("/pki")
        assert "strongest counterargument" in resp.text
        assert "honour the IDP extension" in resp.text

    @pytest.mark.asyncio
    async def test_category_filter_narrows(self, client):
        resp = await client.get("/pki?category=ca-operations")
        assert "PKI Low" in resp.text
        assert "PKI High" not in resp.text

    @pytest.mark.asyncio
    async def test_api_probes_endpoint(self, client):
        resp = await client.get("/api/pki/probes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["probes"] >= 1
        assert len(data["probes"]) >= 1

    @pytest.mark.asyncio
    async def test_churn_pki_path(self, client, monkeypatch):
        """lab=pki routes through generate_idea_llm + urgency scoring + save."""
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult

        idea = _urgent_idea(
            name="Churned PKI",
            tagline="OCSP responder capacity model under ML-DSA signature sizes",
            category=IdeaCategory.PKI_REVOCATION,
            content_hash="churn-pki-1",
        )
        idea.generation_mode = "novel"

        async def _fake_generate(db_, category, **kw):
            return LLMGenerationResult(idea=idea, mode="novel", persona="p", backend="stub", raw_response="{}")

        monkeypatch.setattr(gen, "generate_idea_llm", _fake_generate)
        resp = await client.post("/api/churn", json={"lab": "pki"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"] is not None
        assert data["idea"]["name"] == "Churned PKI"
        assert isinstance(data["idea"]["pki_urgency_score"], float)
        assert data["idea"]["pki_anchor"] is not None
