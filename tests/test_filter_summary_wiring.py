"""TDD: Wire telemetry.build_filter_summary + IdeaGenerator integration.

Phase 4 plumbing was landed; this wires it end-to-end so the cron runner
actually injects the saturation summary into Claude's prompt.

- engine/telemetry.build_filter_summary(db, ...) returns the dict shape
  expected by build_generation_prompt(filter_summary=...).
- IdeaGenerator.generate(... filter_summary=...) forwards it to the
  prompt builder so cron only needs to build the summary once and pass it in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from project_forge.models import FilteredIdea, Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, *, category: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    return Idea(
        name=name,
        tagline=f"tag for {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=0.8,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


def _filtered(name: str, *, category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
              days_ago: int = 1) -> FilteredIdea:
    fi = FilteredIdea(
        idea_name=name,
        idea_tagline="t",
        idea_category=category,
        filter_reason="duplicate:tagline_similarity:0.9",
        original_idea_json="{}",
    )
    fi.filtered_at = datetime.now(UTC) - timedelta(days=days_ago)
    return fi


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "wire_filter.db")
    await d.connect()
    yield d
    await d.close()


# ── build_filter_summary: produces shape consumed by build_generation_prompt


class TestBuildFilterSummary:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self, db):
        from project_forge.engine.telemetry import build_filter_summary

        summary = await build_filter_summary(db)
        assert "saturated_concepts" in summary
        assert "high_filter_rate_categories" in summary

    @pytest.mark.asyncio
    async def test_lists_top_saturated_concepts(self, db):
        from project_forge.engine.telemetry import build_filter_summary

        for name in (
            "certificate alpha", "certificate beta", "certificate gamma",
            "certificate delta", "certificate epsilon",
        ):
            await db.save_filtered_idea(_filtered(name))
        for name in ("detection one", "detection two"):
            await db.save_filtered_idea(_filtered(name))

        summary = await build_filter_summary(db, top_concepts=2)
        # Most-rejected concept first
        assert summary["saturated_concepts"][0] == "certificate"
        assert len(summary["saturated_concepts"]) <= 2

    @pytest.mark.asyncio
    async def test_lists_high_filter_rate_categories(self, db):
        from project_forge.engine.telemetry import build_filter_summary

        # security-tool: 1 accept, 4 reject → 0.80
        await db.save_idea(_idea("Accept ST"))
        for n in ("R1", "R2", "R3", "R4"):
            await db.save_filtered_idea(_filtered(n))
        # privacy: 1 accept, 0 reject → 0.00 (well below threshold)
        await db.save_idea(_idea("Accept PR", category=IdeaCategory.PRIVACY))

        summary = await build_filter_summary(db, rate_threshold=0.5)
        cats = [c for c, _ in summary["high_filter_rate_categories"]]
        assert "security-tool" in cats
        assert "privacy" not in cats

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_lists(self, db):
        from project_forge.engine.telemetry import build_filter_summary

        summary = await build_filter_summary(db)
        assert summary["saturated_concepts"] == []
        assert summary["high_filter_rate_categories"] == []

    @pytest.mark.asyncio
    async def test_categories_sorted_by_rate_descending(self, db):
        from project_forge.engine.telemetry import build_filter_summary

        # security-tool: 4 of 5 = 0.80
        await db.save_idea(_idea("Accept ST"))
        for n in ("R1", "R2", "R3", "R4"):
            await db.save_filtered_idea(_filtered(n, category=IdeaCategory.SECURITY_TOOL))
        # automation: 7 of 10 = 0.70
        for i in range(3):
            await db.save_idea(_idea(f"Acc auto {i}", category=IdeaCategory.AUTOMATION))
        for i in range(7):
            await db.save_filtered_idea(_filtered(f"Rej auto {i}", category=IdeaCategory.AUTOMATION))

        summary = await build_filter_summary(db, rate_threshold=0.6)
        rates = summary["high_filter_rate_categories"]
        # First entry must have higher rate than second
        assert len(rates) >= 2
        assert rates[0][1] >= rates[1][1]


# ── IdeaGenerator forwards filter_summary to build_generation_prompt


class TestIdeaGeneratorFilterSummaryForwarding:
    def test_generate_accepts_filter_summary_kwarg(self, monkeypatch):
        """IdeaGenerator.generate must accept and forward filter_summary."""
        import asyncio

        from project_forge.engine.generator import IdeaGenerator

        captured = {}

        _FAKE_JSON = (
            '{"name":"X","tagline":"t","description":"d","category":"security-tool",'
            '"market_analysis":"m","feasibility_score":0.8,"mvp_scope":"mvp",'
            '"tech_stack":["py"]}'
        )

        # Stub anthropic + the prompt builder so we observe what was passed
        class _StubMessages:
            def create(self, **kwargs):  # noqa: ARG002
                class _Resp:
                    content = [type("X", (), {"text": _FAKE_JSON})]
                return _Resp()

        class _StubClient:
            messages = _StubMessages()

        def stub_build_prompt(**kwargs):
            captured.update(kwargs)
            return "PROMPT"

        gen = IdeaGenerator.__new__(IdeaGenerator)
        gen.client = _StubClient()
        gen.model = "stub-model"
        monkeypatch.setattr(
            "project_forge.engine.generator.build_generation_prompt",
            stub_build_prompt,
        )

        summary = {
            "saturated_concepts": ["certificate"],
            "high_filter_rate_categories": [("security-tool", 0.80)],
        }

        asyncio.run(gen.generate(
            category=IdeaCategory.SECURITY_TOOL,
            filter_summary=summary,
        ))

        assert captured.get("filter_summary") == summary
