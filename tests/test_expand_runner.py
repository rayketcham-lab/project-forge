"""Tests for expand_runner — the hourly horizontal expansion CLI entry point."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_expand.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(**kwargs) -> Idea:
    defaults = {
        "name": "Expand Test Idea",
        "tagline": "An expansion test",
        "description": "Testing horizontal expansion.",
        "category": IdeaCategory.AUTOMATION,
        "market_analysis": "Market for automation tools.",
        "feasibility_score": 0.75,
        "mvp_scope": "CLI tool with core feature.",
        "tech_stack": ["python"],
    }
    defaults.update(kwargs)
    return Idea(**defaults)


# === Tests for run_horizontal_cycle ===


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.SuperIdeaGenerator")
@patch("project_forge.cron.horizontal.generate_cross_idea")
@patch("project_forge.cron.horizontal.pick_cross_category_pair")
async def test_run_horizontal_cycle_happy_path(mock_pick_pair, mock_gen_cross, mock_super_cls, db):
    """Happy path: cross-category idea is generated and returned."""
    idea1 = _make_idea(name="Cross Idea Alpha")

    mock_pick_pair.return_value = (IdeaCategory.AUTOMATION, IdeaCategory.SECURITY_TOOL)
    mock_gen_cross.return_value = idea1

    # Super idea generator returns None — triggers fallback path, which is already tested separately
    mock_super_instance = AsyncMock()
    mock_super_instance.generate_seeded.return_value = None
    mock_super_cls.return_value = mock_super_instance

    idea_fallback = _make_idea(name="Fallback Alpha")
    mock_gen_cross.side_effect = [idea1, idea_fallback]
    mock_pick_pair.side_effect = [
        (IdeaCategory.AUTOMATION, IdeaCategory.SECURITY_TOOL),
        (IdeaCategory.PRIVACY, IdeaCategory.OBSERVABILITY),
    ]

    with patch.object(db, "record_category_pair", new=AsyncMock()):
        with patch("project_forge.cron.horizontal.review_idea") as mock_review:
            mock_review.return_value = MagicMock(passed=True, reasons=[])
            with patch.object(db, "save_idea", new=AsyncMock(return_value=idea1)):
                from project_forge.cron.horizontal import run_horizontal_cycle

                ideas = await run_horizontal_cycle(db)

    assert len(ideas) >= 1
    assert ideas[0].name == "Cross Idea Alpha"


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.SuperIdeaGenerator")
@patch("project_forge.cron.horizontal.generate_cross_idea")
@patch("project_forge.cron.horizontal.pick_cross_category_pair")
async def test_run_horizontal_cycle_quality_review_rejected(mock_pick_pair, mock_gen_cross, mock_super_cls, db):
    """Cross idea rejected by quality review is not saved; only the fallback idea is saved."""
    rejected_idea = _make_idea(name="Rejected Cross Idea")
    fallback_idea = _make_idea(name="Fallback After Rejection")

    mock_pick_pair.side_effect = [
        (IdeaCategory.PRIVACY, IdeaCategory.COMPLIANCE),
        (IdeaCategory.OBSERVABILITY, IdeaCategory.NIST_STANDARDS),
    ]
    mock_gen_cross.side_effect = [rejected_idea, fallback_idea]

    mock_super_instance = AsyncMock()
    mock_super_instance.generate_seeded.return_value = None
    mock_super_cls.return_value = mock_super_instance

    saved_ideas: list[Idea] = []

    async def capture_save(idea):
        saved_ideas.append(idea)
        return idea

    with patch.object(db, "record_category_pair", new=AsyncMock()):
        with patch("project_forge.cron.horizontal.review_idea") as mock_review:
            # First call (rejected_idea): fails. Second call (fallback): not checked
            # because fallback skips review — but we allow it to pass.
            mock_review.return_value = MagicMock(passed=False, reasons=["Too vague"])
            with patch.object(db, "save_idea", side_effect=capture_save):
                from project_forge.cron.horizontal import run_horizontal_cycle

                await run_horizontal_cycle(db)

    # The originally rejected idea should not be in saved_ideas
    saved_names = [i.name for i in saved_ideas]
    assert "Rejected Cross Idea" not in saved_names
    # Fallback idea gets saved instead
    assert "Fallback After Rejection" in saved_names


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.SuperIdeaGenerator")
@patch("project_forge.cron.horizontal.generate_cross_idea")
@patch("project_forge.cron.horizontal.pick_cross_category_pair")
async def test_run_horizontal_cycle_super_idea_none_triggers_fallback(
    mock_pick_pair, mock_gen_cross, mock_super_cls, db
):
    """When super idea generation returns None, a fallback cross-category idea is generated."""
    idea1 = _make_idea(name="Primary Cross Idea")
    idea_fallback = _make_idea(name="Fallback Cross Idea", category=IdeaCategory.DEVOPS_TOOLING)

    # First call returns primary pair, second call returns fallback pair
    mock_pick_pair.side_effect = [
        (IdeaCategory.AUTOMATION, IdeaCategory.SECURITY_TOOL),
        (IdeaCategory.PRIVACY, IdeaCategory.COMPLIANCE),
    ]
    mock_gen_cross.side_effect = [idea1, idea_fallback]

    mock_super_instance = AsyncMock()
    mock_super_instance.generate_seeded.return_value = None
    mock_super_cls.return_value = mock_super_instance

    with patch.object(db, "record_category_pair", new=AsyncMock()):
        with patch("project_forge.cron.horizontal.review_idea") as mock_review:
            mock_review.return_value = MagicMock(passed=True, reasons=[])
            with patch.object(db, "save_idea", new=AsyncMock(return_value=idea1)):
                from project_forge.cron.horizontal import run_horizontal_cycle

                ideas = await run_horizontal_cycle(db)

    assert len(ideas) == 2
    assert ideas[0].name == "Primary Cross Idea"
    assert ideas[1].name == "Fallback Cross Idea"


# === Tests for expand_runner._run ===
# expand_runner imports run_horizontal_cycle from project_forge.cron.horizontal,
# so we patch the name as it appears in the module's namespace.


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.run_horizontal_cycle")
@patch("project_forge.cron.expand_runner.Database")
async def test_expand_runner_run_success(mock_db_cls, mock_run_cycle):
    """_run() connects to DB, calls run_horizontal_cycle, then closes DB."""
    idea = _make_idea(name="Runner Success Idea")

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_run_cycle.return_value = [idea]

    from project_forge.cron.expand_runner import _run

    await _run()

    mock_db.connect.assert_called_once()
    mock_db.close.assert_called_once()


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.run_horizontal_cycle")
@patch("project_forge.cron.expand_runner.Database")
async def test_expand_runner_run_empty_result(mock_db_cls, mock_run_cycle):
    """_run() handles empty idea list gracefully without crashing."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_run_cycle.return_value = []

    from project_forge.cron.expand_runner import _run

    await _run()

    mock_db.close.assert_called_once()


@pytest.mark.asyncio
@patch("project_forge.cron.horizontal.run_horizontal_cycle")
@patch("project_forge.cron.expand_runner.Database")
async def test_expand_runner_run_closes_db_on_error(mock_db_cls, mock_run_cycle):
    """_run() closes the DB even when run_horizontal_cycle raises an exception."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_run_cycle.side_effect = RuntimeError("Expansion exploded")

    from project_forge.cron.expand_runner import _run

    with pytest.raises(SystemExit):
        await _run()

    mock_db.close.assert_called_once()


# === Tests for pick_cross_category_pair ===


@pytest.mark.asyncio
async def test_pick_cross_category_pair_returns_two_different_categories(db):
    """pick_cross_category_pair always returns two distinct IdeaCategory values."""
    from project_forge.cron.horizontal import pick_cross_category_pair

    cat_a, cat_b = await pick_cross_category_pair(db)
    assert isinstance(cat_a, IdeaCategory)
    assert isinstance(cat_b, IdeaCategory)
    assert cat_a != cat_b


@pytest.mark.asyncio
async def test_pick_cross_category_pair_respects_exclusion(db):
    """Excluded pair is not returned when alternatives exist."""
    from project_forge.cron.horizontal import pick_cross_category_pair

    # Get the least-explored pair first
    cat_a, cat_b = await pick_cross_category_pair(db)
    # Exclude it and ask again — should get a different pair
    cat_c, cat_d = await pick_cross_category_pair(db, exclude=[(cat_a, cat_b)])

    excluded_norm = tuple(sorted([cat_a.value, cat_b.value]))
    result_norm = tuple(sorted([cat_c.value, cat_d.value]))
    assert result_norm != excluded_norm
