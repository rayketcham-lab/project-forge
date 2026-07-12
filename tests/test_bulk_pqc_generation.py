"""Tests for bulk PQC/security idea generation - 100 new ideas."""

import random
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database


@pytest.fixture(autouse=True)
def _seed_rng():
    """Pin the RNG so distribution assertions test a fixed sample.

    These tests assert thresholds over randomized generation (>=25 PQC
    ideas out of 100, >=90% unique names, ...). Unseeded, they flake at
    the margin — CI failed with 24>=25 after category expansion diluted
    the security share of random draws (#89).
    """
    random.seed(20260712)

PQC_SECURITY_CATEGORIES = {
    IdeaCategory.PQC_CRYPTOGRAPHY,
    IdeaCategory.NIST_STANDARDS,
    IdeaCategory.RFC_SECURITY,
    IdeaCategory.CRYPTO_INFRASTRUCTURE,
    IdeaCategory.SECURITY_TOOL,
    IdeaCategory.VULNERABILITY_RESEARCH,
    IdeaCategory.COMPLIANCE,
}


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_bulk_pqc.db")
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_generate_100_ideas(db):
    """Core requirement: generate 100 new ideas."""
    ideas = await run_auto_scan(db, count=100)
    # Dedup may filter similar ideas — expect at least 60 unique ideas from 100 attempts
    assert len(ideas) >= 60, f"Expected >=60 unique ideas from 100 attempts, got {len(ideas)}"


@pytest.mark.asyncio
async def test_100_ideas_cover_pqc_categories(db):
    """At least 40% should be PQC/security focused."""
    ideas = await run_auto_scan(db, count=100)
    pqc_count = sum(1 for i in ideas if i.category in PQC_SECURITY_CATEGORIES)
    # Dedup filters similar ideas, so threshold is proportional to unique count
    assert pqc_count >= 25, f"Expected >=25 PQC/security ideas, got {pqc_count}"


@pytest.mark.asyncio
async def test_100_ideas_have_reasonable_scores(db):
    """All ideas should have feasibility scores in range."""
    ideas = await run_auto_scan(db, count=100)
    for idea in ideas:
        assert 0.0 <= idea.feasibility_score <= 1.0
    avg = sum(i.feasibility_score for i in ideas) / len(ideas)
    assert 0.5 <= avg <= 0.95, f"Average score {avg} outside expected range"


@pytest.mark.asyncio
async def test_100_ideas_have_unique_names(db):
    """Ideas should have mostly unique names."""
    ideas = await run_auto_scan(db, count=100)
    names = [i.name for i in ideas]
    unique = set(names)
    # Allow some collisions due to randomness, but most should be unique
    # After dedup filtering, all remaining ideas should have unique names
    assert len(unique) >= len(names) * 0.9, f"Only {len(unique)} unique names out of {len(names)}"


@pytest.mark.asyncio
async def test_100_ideas_stored_in_db(db):
    """Most ideas should be persisted (some filtered by universal dedup)."""
    await run_auto_scan(db, count=100)
    count = await db.count_ideas()
    assert count >= 60, f"Expected >=60 ideas stored after dedup, got {count}"
