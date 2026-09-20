"""Tests for scheduler time-travel behaviour.

Covers:
- Watermark-based scheduling: recent run → delay_query returns > 0 (won't fire again immediately)
- Time-travel: mock/advance "now" so interval passes → delay_query returns 0 (fires again)
- Multiple cadences: different intervals fire at different times
- Fresh scheduler (no watermarks): delay_query returns 0 (immediate fire)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Challenge, Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "time_travel.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(name: str, category: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    return Idea(
        name=name,
        tagline="Test idea",
        description=(
            "A test idea long enough to satisfy the quality review minimum "
            "body length so dedup is the only gate exercised."
        ),
        category=category,
        market_analysis="Test market analysis.",
        feasibility_score=0.75,
        mvp_scope="Minimal viable product.",
        tech_stack=["python"],
    )


# Fake datetime class that delegates to the real datetime but overrides now().
# This is needed because lifespan_scheduler imports `from datetime import
# UTC, datetime, timedelta` at module level — patching the name in that
# namespace must keep classmethods like ``fromisoformat`` working.
class _FakeDatetime:
    _now_override: datetime | None = None

    @staticmethod
    def fromisoformat(s):
        return datetime.fromisoformat(s)

    @staticmethod
    def now(tz=None):
        if _FakeDatetime._now_override is not None:
            return _FakeDatetime._now_override
        return datetime.now(tz)

    @staticmethod
    def replace(**kwargs):
        return datetime.now().replace(**kwargs)


def _set_now(dt: datetime):
    """Pretend ``datetime.now()`` returns *dt* for the duration of a test."""
    _FakeDatetime._now_override = dt


def _reset_now():
    """Restore real ``datetime.now``."""
    _FakeDatetime._now_override = None


# ─── Watermark-based scheduling ───────────────────────────────────────────────


class TestWatermarkScheduling:
    @pytest.mark.asyncio
    async def test_fresh_watermark_returns_positive_delay(self, db):
        """If a cadence just ran, delay_query must return > 0 (won't fire again)."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Fresh expand")
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=30)
        await db.save_idea(idea)

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay > 0
        assert 3500 < delay < 3800

    @pytest.mark.asyncio
    async def test_no_watermark_returns_zero(self, db):
        """With no ideas in the DB, delay_query returns 0 (immediate fire)."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_stale_watermark_returns_zero(self, db):
        """If last run was older than the interval, delay_query returns 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Stale expand")
        idea.generated_at = datetime.now(UTC) - timedelta(hours=2)
        await db.save_idea(idea)

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_introspect_watermark_with_filtered_idea(self, db):
        """A filtered SI idea still counts as a watermark (runner fired)."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        await db.db.execute(
            "INSERT INTO filtered_ideas "
            "(id, idea_name, idea_tagline, idea_category, "
            " filter_reason, filtered_at, original_idea_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "filter-1",
                "Filtered SI",
                "tag",
                "self-improvement",
                "duplicate:test",
                (datetime.now(UTC) - timedelta(minutes=15)).isoformat(),
                "{}",
            ),
        )
        await db.db.commit()

        delay = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        assert delay > 0


# ─── Time-travel tests ────────────────────────────────────────────────────────


class TestTimeTravel:
    @pytest.mark.asyncio
    async def test_advance_time_over_interval(self, db):
        """Patching datetime.now to simulate the future shows delay=0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Time-travel expand")
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=30)
        await db.save_idea(idea)

        delay_normal = await seconds_until_next_expand(db, interval=timedelta(seconds=60))
        assert delay_normal > 0

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            _set_now(datetime.now(UTC) + timedelta(minutes=5))
            delay_travel = await seconds_until_next_expand(db, interval=timedelta(seconds=60))
            assert delay_travel == 0.0
            _reset_now()

    @pytest.mark.asyncio
    async def test_advance_time_but_not_enough(self, db):
        """Patching to a future time within the interval still returns > 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Near-future expand")
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=10)
        await db.save_idea(idea)

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            # 10s ago + 20s future = 50s into future, interval=60s → 10s delay
            _set_now(datetime.now(UTC) + timedelta(seconds=20))
            delay = await seconds_until_next_expand(db, interval=timedelta(seconds=60))
            assert delay > 0
            _reset_now()

    @pytest.mark.asyncio
    async def test_time_travel_skips_multiple_intervals(self, db):
        """Jumping far into the future: delay is still clamped to 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Far-future expand")
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=10)
        await db.save_idea(idea)

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            _set_now(datetime.now(UTC) + timedelta(hours=3))
            delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
            assert delay == 0.0
            _reset_now()

    @pytest.mark.asyncio
    async def test_introspect_time_travel(self, db):
        """Time-travel works for the introspect watermark too."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        idea = _make_idea("Time-travel SI", IdeaCategory.SELF_IMPROVEMENT)
        idea.generated_at = datetime.now(UTC) - timedelta(hours=1)
        await db.save_idea(idea)

        delay_normal = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        assert delay_normal > 0

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            _set_now(datetime.now(UTC) + timedelta(hours=25))
            delay_travel = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
            assert delay_travel == 0.0
            _reset_now()

    @pytest.mark.asyncio
    async def test_snipe_time_travel(self, db):
        """Time-travel works for the snipe watermark too."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_snipe

        idea = _make_idea("Snipe TT")
        idea.generation_mode = "snipe"
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=30)
        await db.save_idea(idea)

        delay_normal = await seconds_until_next_snipe(db, interval=timedelta(seconds=60))
        assert delay_normal > 0

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            _set_now(datetime.now(UTC) + timedelta(minutes=5))
            delay_travel = await seconds_until_next_snipe(db, interval=timedelta(seconds=60))
            assert delay_travel == 0.0
            _reset_now()

    @pytest.mark.asyncio
    async def test_challenge_time_travel(self, db):
        """Time-travel works for the challenge watermark."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_challenge

        challenge = Challenge(
            idea_id="test-idea",
            question="Is this idea viable?",
            challenge_type="freeform",
            focus_area="all",
            tone="skeptical",
            verdict="no_change",
            confidence=0.5,
            created_at=datetime.now(UTC) - timedelta(seconds=30),
        )
        await db.save_challenge(challenge)

        delay_normal = await seconds_until_next_challenge(db, interval=timedelta(seconds=60))
        assert delay_normal > 0

        with patch("project_forge.web.lifespan_scheduler.datetime", _FakeDatetime):
            _set_now(datetime.now(UTC) + timedelta(minutes=5))
            delay_travel = await seconds_until_next_challenge(db, interval=timedelta(seconds=60))
            assert delay_travel == 0.0
            _reset_now()


# ─── Multiple cadences with different intervals ──────────────────────────────


class TestMultipleCadences:
    @pytest.mark.asyncio
    async def test_different_intervals_produce_different_delays(self, db):
        """A 1-minute interval cadence returns smaller delay than a 1-hour one."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Multi cadence")
        idea.generated_at = datetime.now(UTC) - timedelta(seconds=30)
        await db.save_idea(idea)

        delay_1min = await seconds_until_next_expand(db, interval=timedelta(minutes=1))
        delay_1h = await seconds_until_next_expand(db, interval=timedelta(hours=1))

        assert delay_1min < delay_1h
        assert 10 < delay_1min < 100
        assert 3500 < delay_1h < 3800

    @pytest.mark.asyncio
    async def test_different_intervals_both_zero_for_stale(self, db):
        """If last run is older than both intervals, both return 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Stale multi")
        idea.generated_at = datetime.now(UTC) - timedelta(hours=25)
        await db.save_idea(idea)

        delay_short = await seconds_until_next_expand(db, interval=timedelta(minutes=5))
        delay_long = await seconds_until_next_expand(db, interval=timedelta(hours=24))

        assert delay_short == 0.0
        assert delay_long == 0.0

    @pytest.mark.asyncio
    async def test_different_intervals_all_zero_for_fresh_db(self, db):
        """No ideas at all: every interval returns 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        delay_short = await seconds_until_next_expand(db, interval=timedelta(minutes=1))
        delay_long = await seconds_until_next_expand(db, interval=timedelta(hours=24))

        assert delay_short == 0.0
        assert delay_long == 0.0

    @pytest.mark.asyncio
    async def test_short_then_long_interval_order(self, db):
        """Order of interval test doesn't matter: both consistent."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Order test")
        idea.generated_at = datetime.now(UTC) - timedelta(minutes=10)
        await db.save_idea(idea)

        delay_long_first = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        delay_short_second = await seconds_until_next_expand(db, interval=timedelta(minutes=20))

        assert delay_long_first > 0
        assert delay_short_second > 0


# ─── Fresh scheduler (no watermarks) ─────────────────────────────────────────


class TestFreshScheduler:
    @pytest.mark.asyncio
    async def test_expand_no_watermark(self, db):
        """Fresh expand cadence with no ideas → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_snipe_no_watermark(self, db):
        """Fresh snipe cadence with no ideas → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_snipe

        delay = await seconds_until_next_snipe(db, interval=timedelta(hours=6))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_review_no_watermark(self, db):
        """Fresh review cadence with no reviews → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_review

        delay = await seconds_until_next_review(db, interval=timedelta(hours=12))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_challenge_no_watermark(self, db):
        """Fresh challenge cadence with no challenges → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_challenge

        delay = await seconds_until_next_challenge(db, interval=timedelta(hours=168))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_pulse_no_watermark(self, db):
        """Fresh pulse cadence with no pulse-mode ideas → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_pulse

        delay = await seconds_until_next_pulse(db, interval=timedelta(hours=3))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_pkp_no_watermark(self, db):
        """Fresh PKI cadence with no probes → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_pki

        delay = await seconds_until_next_pki(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_bot_no_watermark(self, db):
        """Fresh bot cadence with no probes → delay 0."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_bot

        delay = await seconds_until_next_bot(db, interval=timedelta(hours=2))
        assert delay == 0.0


# ─── delay_from_watermark unit tests ─────────────────────────────────────────


class TestDelayFromWatermark:
    """Direct unit tests for the core _delay_from_watermark function."""

    def test_none_timestamp_returns_zero(self):
        """No last run → fire immediately."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        assert _delay_from_watermark(None, timedelta(hours=1)) == 0.0

    def test_recent_timestamp_returns_positive(self):
        """Watermark is in the past but within interval → positive delay."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        past = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        delay = _delay_from_watermark(past, timedelta(hours=1))
        assert delay > 0
        assert delay < 3600

    def test_stale_timestamp_returns_zero(self):
        """Watermark is older than interval → 0."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        delay = _delay_from_watermark(past, timedelta(hours=1))
        assert delay == 0.0

    def test_microsecond_precision(self):
        """Microsecond timestamps should parse correctly."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        precise = (datetime.now(UTC) - timedelta(seconds=30, microseconds=123456)).isoformat()
        delay = _delay_from_watermark(precise, timedelta(seconds=60))
        assert delay > 0
        assert delay < 60

    def test_zero_interval_with_fresh_watermark(self):
        """Interval of zero means immediate re-fire (delay always 0)."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        delay = _delay_from_watermark(past, timedelta(seconds=0))
        assert delay == 0.0

    def test_custom_timezone_offset(self):
        """ISO timestamps with explicit offset should parse correctly."""
        from project_forge.web.lifespan_scheduler import _delay_from_watermark

        past = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
        delay = _delay_from_watermark(past, timedelta(hours=1))
        assert delay > 0
        assert delay < 3600
