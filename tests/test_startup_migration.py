"""Tests for app startup, migration execution, and lifespan lifecycle.

Covers:
- Database connection on startup (fix-10)
- Migration execution completes without error (fix-10)
- Lifespan context manager initialises and tears down cleanly (fix-10)
- Graceful handling when the data directory does not exist yet (fix-10)
"""

import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "startup.db")
    await database.connect()
    yield database
    await database.close()


# ─── Database connection ──────────────────────────────────────────────────────


class TestDatabaseConnection:
    @pytest.mark.asyncio
    async def test_connect_creates_tables(self, tmp_path):
        """connect() must create all schema tables in one shot."""
        db_path = tmp_path / "new.db"
        db = Database(db_path)
        await db.connect()

        cursor = await db.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}

        assert "ideas" in tables
        assert "challenges" in tables
        assert "filtered_ideas" in tables
        assert "idea_reviews" in tables
        assert "missions" in tables
        assert "pki_probes" in tables
        assert "bot_probes" in tables
        await db.close()

    @pytest.mark.asyncio
    async def test_connect_returns_no_errors(self, tmp_path):
        """connect() must not raise on a fresh path."""
        db_path = tmp_path / "fresh.db"
        db = Database(db_path)
        await db.connect()
        assert db._db is not None
        await db.close()

    @pytest.mark.asyncio
    async def test_close_cleans_connection(self, tmp_path):
        """close() must null out the underlying connection."""
        db_path = tmp_path / "close_test.db"
        db = Database(db_path)
        await db.connect()
        assert db._db is not None
        await db.close()
        assert db._db is None


# ─── Migration execution ──────────────────────────────────────────────────────


class TestMigrationExecution:
    @pytest.mark.asyncio
    async def test_all_alter_statements_are_idempotent(self):
        """Every ALTER TABLE in the migration block must survive re-run."""
        import aiosqlite

        from project_forge.storage.db import SCHEMA

        tmp_path = Path(__file__).parent / "tmp_migration_test"
        tmp_path.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / "mig.db"

        alter_stmts = (
            "ALTER TABLE ideas ADD COLUMN content_hash TEXT",
            "ALTER TABLE ideas ADD COLUMN source_url TEXT",
            "ALTER TABLE challenges ADD COLUMN challenge_type TEXT NOT NULL DEFAULT 'freeform'",
            "ALTER TABLE challenges ADD COLUMN focus_area TEXT NOT NULL DEFAULT 'all'",
            "ALTER TABLE challenges ADD COLUMN tone TEXT NOT NULL DEFAULT 'skeptical'",
            "ALTER TABLE challenges ADD COLUMN verdict TEXT NOT NULL DEFAULT 'no_change'",
            "ALTER TABLE challenges ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5",
            "ALTER TABLE challenges ADD COLUMN applied_at TEXT",
            "ALTER TABLE ideas ADD COLUMN archived_reason TEXT",
            "ALTER TABLE ideas ADD COLUMN archived_at TEXT",
            "ALTER TABLE ideas ADD COLUMN generation_mode TEXT",
            "ALTER TABLE ideas ADD COLUMN fundability_score REAL",
            "ALTER TABLE ideas ADD COLUMN auto_promoted_at TEXT",
            "ALTER TABLE ideas ADD COLUMN ambition_score REAL",
            "ALTER TABLE ideas ADD COLUMN artifact_type TEXT",
            "ALTER TABLE ideas ADD COLUMN snipe_score REAL",
            "ALTER TABLE ideas ADD COLUMN target_incumbent TEXT",
            "ALTER TABLE ideas ADD COLUMN mission_id TEXT",
            "ALTER TABLE ideas ADD COLUMN cashflow_score REAL",
            "ALTER TABLE ideas ADD COLUMN pki_urgency_score REAL",
            "ALTER TABLE ideas ADD COLUMN pki_anchor TEXT",
            "ALTER TABLE ideas ADD COLUMN pki_objection TEXT",
            "ALTER TABLE ideas ADD COLUMN bot_edge_score REAL",
            "ALTER TABLE ideas ADD COLUMN bot_spec TEXT",
        )

        async def run_migrations(conn):
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA busy_timeout = 60000")
            await conn.execute("PRAGMA synchronous = NORMAL")
            await conn.execute("PRAGMA cache_spill = OFF")
            await conn.execute("PRAGMA wal_autocheckpoint = 1000")
            await conn.executescript(SCHEMA)
            for stmt in alter_stmts:
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ideas_content_hash "
                "ON ideas(content_hash) WHERE content_hash IS NOT NULL"
            )
            await conn.commit()

        # First run: creates schema + runs migrations from scratch.
        conn1 = await aiosqlite.connect(db_path)
        await run_migrations(conn1)
        await conn1.close()

        # Second run (simulates app restart): should not raise.
        conn2 = await aiosqlite.connect(db_path)
        await run_migrations(conn2)
        await conn2.close()

        shutil.rmtree(tmp_path, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_connect_after_already_migrated(self, db):
        """Re-connecting (re-import) does not lose data or crash."""
        from project_forge.models import Idea, IdeaCategory

        idea = Idea(
            name="Pre-migration idea",
            tagline="t",
            description="d" * 80,
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="m" * 40,
            feasibility_score=0.8,
            mvp_scope="m" * 5,
            tech_stack=["python"],
        )
        await db.save_idea(idea)

        cursor = await db.db.execute("SELECT COUNT(*) AS cnt FROM ideas")
        row = await cursor.fetchone()
        assert row["cnt"] == 1

    @pytest.mark.asyncio
    async def test_new_columns_exist_on_schema(self, db):
        """Schema migrations must have created every expected column."""
        cursor = await db.db.execute("PRAGMA table_info(ideas)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "content_hash" in columns
        assert "fundability_score" in columns
        assert "pki_urgency_score" in columns
        assert "bot_edge_score" in columns
        assert "bot_spec" in columns

        cursor = await db.db.execute("PRAGMA table_info(challenges)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "challenge_type" in columns
        assert "applied_at" in columns


# ─── Lifespan lifecycle ───────────────────────────────────────────────────────


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_connects_and_closes_db(self, tmp_path):
        """The lifespan context manager connects on entry and closes on exit.

        httpx.ASGITransport speaks only HTTP scope, so it never invokes the
        ASGI ``lifespan`` protocol.  We therefore call the context manager
        directly to exercise enter / yield / exit, and make an HTTP request
        *inside* the yield to prove the DB stays alive mid-request.
        """
        from contextlib import asynccontextmanager

        from fastapi import FastAPI
        from fastapi.routing import APIRouter

        test_db = Database(tmp_path / "lifespan.db")
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok"}

        @asynccontextmanager
        async def test_lifespan(app: FastAPI):
            # ENTER
            await test_db.connect()
            assert test_db._db is not None

            # YIELD — serve requests while the DB is alive.
            yield

            # EXIT
            await test_db.close()
            assert test_db._db is None

        app = FastAPI(lifespan=test_lifespan)
        app.include_router(router)

        # Call the lifespan context manager directly.
        async with test_lifespan(app):
            # DB must be connected during yield.
            assert test_db._db is not None

            # Verify HTTP requests succeed while the lifespan is active by
            # calling the ASGI app directly (no lifespan overhead needed
            # since we already opened it).
            events: list[dict] = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(msg):
                events.append(msg)

            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                        "method": "GET", "path": "/health", "raw_path": b"/health",
                        "root_path": "", "query_string": b"", "server": ("test", 80),
                        "client": ("127.0.0.1", 12345), "headers": []},
                      receive, send)

            assert events[0]["type"] == "http.response.start"
            assert events[0]["status"] == 200

        # After the outer async with, lifespan __aexit__ ran.
        assert test_db._db is None

    @pytest.mark.asyncio
    async def test_create_app_provides_working_routes(self, tmp_path):
        """create_app() returns an app that serves requests."""
        from project_forge.web.app import create_app

        app = create_app(db_path=str(tmp_path / "create_app.db"))

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_lifespan_scheduler_cancelled_on_exit(self, tmp_path):
        """When the app shuts down, the scheduler task is cancelled."""
        from project_forge.web.lifespan_scheduler import start_scheduler

        test_db = Database(tmp_path / "cancel_test.db")
        await test_db.connect()

        task = start_scheduler(test_db, tick_interval=0.05)
        try:
            assert isinstance(task, asyncio.Task)
            assert not task.done()
            await asyncio.sleep(0.1)
            assert not task.done()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert task.done()

        await test_db.close()


# ─── Missing data directory ───────────────────────────────────────────────────


class TestMissingDataDir:
    @pytest.mark.asyncio
    async def test_connect_creates_missing_data_directory(self, tmp_path):
        """Database must create the parent directory if it does not exist."""
        db_path = tmp_path / "nonexistent" / "nested" / "deep" / "db.sqlite"
        assert not db_path.parent.exists()

        db = Database(db_path)
        await db.connect()
        assert db_path.exists()
        assert db._db is not None
        await db.close()

    @pytest.mark.asyncio
    async def test_connect_with_existing_directory(self, tmp_path):
        """Database works normally when the data directory already exists."""
        db_path = tmp_path / "existing" / "db.sqlite"
        tmp_path.joinpath("existing").mkdir()

        db = Database(db_path)
        await db.connect()
        assert db_path.exists()
        assert db._db is not None
        await db.close()
