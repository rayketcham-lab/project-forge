"""Tests for the feed-refresh cadence wired into the lifespan scheduler.

External feeds (NVD / arXiv / IETF) drive prompt-seed material. The cron
shell wrapper that used to refresh them lives in `scripts/refresh-feeds.sh`
which fired from a systemd timer — unreachable from the bwrap runtime.
The cadence replaces that with an in-process refresh."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "feeds.db")
    await database.connect()
    yield database
    await database.close()


class TestFireFeedRefresh:
    @pytest.mark.asyncio
    async def test_invokes_each_feed_fetcher(self, db, tmp_path, monkeypatch):
        """_fire_feed_refresh calls nvd, arxiv, and ietf fetch() with caches."""
        from project_forge.web import lifespan_scheduler

        monkeypatch.setenv("FORGE_FEEDS_DIR", str(tmp_path / "feeds"))

        nvd_mock = AsyncMock(return_value=[])
        arxiv_mock = AsyncMock(return_value=[])
        ietf_mock = AsyncMock(return_value=[])

        # Wrap sync fetchers as async-compatible callables.
        with patch("project_forge.feeds.nvd.fetch", nvd_mock):
            with patch("project_forge.feeds.arxiv.fetch", arxiv_mock):
                with patch("project_forge.feeds.ietf.fetch", ietf_mock):
                    await lifespan_scheduler._fire_feed_refresh(db)

        nvd_mock.assert_called_once()
        arxiv_mock.assert_called_once()
        ietf_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_feed_failing_does_not_block_others(self, db, tmp_path, monkeypatch):
        from project_forge.web import lifespan_scheduler

        monkeypatch.setenv("FORGE_FEEDS_DIR", str(tmp_path / "feeds"))

        def _boom(*a, **kw):
            raise RuntimeError("nvd down")

        arxiv_mock = AsyncMock(return_value=[{"id": "a1"}])
        ietf_mock = AsyncMock(return_value=[])

        with patch("project_forge.feeds.nvd.fetch", _boom):
            with patch("project_forge.feeds.arxiv.fetch", arxiv_mock):
                with patch("project_forge.feeds.ietf.fetch", ietf_mock):
                    # Must not raise — runner swallows per-feed failure.
                    await lifespan_scheduler._fire_feed_refresh(db)

        arxiv_mock.assert_called_once()
        ietf_mock.assert_called_once()


class TestCadenceRegistration:
    def test_feed_refresh_registered_in_defaults(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        cadences = default_cadences()
        names = [c.name for c in cadences]
        assert "feed_refresh" in names
        feed_cad = next(c for c in cadences if c.name == "feed_refresh")
        # 24h default with no DB watermark.
        assert feed_cad.interval == timedelta(hours=24)
        assert feed_cad.delay_query is None
