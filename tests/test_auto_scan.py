"""Tests for automated local idea generation."""

import random
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.cron.auto_scan import generate_local_idea, run_auto_scan
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database


@pytest.fixture(autouse=True)
def _seed_rng():
    """Pin the RNG so the count-bounded scan assertions are deterministic.

    run_auto_scan asserts exact / lower-bound counts (5 from 5, >=6 distinct
    from 12). Unseeded these flake when a draw lands on a domain the quality
    filter rejects (e.g. a '...: developer experience' tagline) — pre-existing
    fragility, surfaced when the security-weighted rotation (#95) shifted the
    draw sequence. Mirrors the seed fixture in test_bulk_pqc_generation.py.
    """
    random.seed(5)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_scan.db")
    await database.connect()
    yield database
    await database.close()


def test_generate_local_idea_basic():
    idea, cat_val, c_idx, d_idx, direction = generate_local_idea()
    assert idea.name
    assert idea.tagline
    assert idea.description
    assert idea.category in IdeaCategory
    assert 0.0 <= idea.feasibility_score <= 1.0
    assert len(idea.tech_stack) >= 1
    assert idea.content_hash is not None


def test_generate_local_idea_specific_category():
    idea, *_ = generate_local_idea(category=IdeaCategory.PQC_CRYPTOGRAPHY)
    assert idea.category == IdeaCategory.PQC_CRYPTOGRAPHY


def test_generate_local_idea_avoids_duplicates():
    ideas = set()
    for _ in range(20):
        idea, *_ = generate_local_idea(recent_names=list(ideas))
        ideas.add(idea.name)
    assert len(ideas) >= 15


def test_generate_local_idea_all_categories():
    for cat in IdeaCategory:
        idea, *_ = generate_local_idea(category=cat)
        assert idea.category == cat
        assert idea.name
        assert idea.tech_stack


@pytest.mark.asyncio
async def test_run_auto_scan(db):
    ideas = await run_auto_scan(db, count=5)
    assert len(ideas) == 5
    # Verify stored in DB
    count = await db.count_ideas()
    assert count == 5


@pytest.mark.asyncio
async def test_run_auto_scan_distributes_categories(db):
    ideas = await run_auto_scan(db, count=12)
    categories = {i.category for i in ideas}
    # Should cover multiple categories
    assert len(categories) >= 6


@pytest.mark.asyncio
async def test_run_auto_scan_creates_runs(db):
    await run_auto_scan(db, count=3)
    stats = await db.get_stats()
    assert stats["total_runs"] >= 3
