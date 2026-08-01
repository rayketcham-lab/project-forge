"""SQLite storage for ideas, projects, and generation runs.

Hardened with WAL mode, busy_timeout, content fingerprinting,
and input-tuple tracking for deduplication at scale.
"""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import aiosqlite

from project_forge.models import (
    MONEY_CATEGORIES,
    Challenge,
    FilteredIdea,
    GenerationRun,
    Idea,
    IdeaCategory,
    IdeaDenial,
    IdeaStatus,
    Mission,
    RepoEntry,
    Resource,
    SelectionRound,
)

logger = logging.getLogger(__name__)

# Query profiling threshold in seconds
_SLOW_QUERY_THRESHOLD = 0.1  # 100ms

# IdeaStatus is a Literal, which is erased at runtime -- enforce it explicitly
# at the DB boundary so typos never reach persisted rows.
VALID_IDEA_STATUSES: frozenset[str] = frozenset(get_args(IdeaStatus))


def _validate_status(status: str | None) -> None:
    """Raise ValueError if ``status`` is not a declared IdeaStatus. None is allowed."""
    if status is not None and status not in VALID_IDEA_STATUSES:
        raise ValueError(f"Invalid idea status {status!r}; expected one of {sorted(VALID_IDEA_STATUSES)}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tagline TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    market_analysis TEXT NOT NULL,
    feasibility_score REAL NOT NULL,
    mvp_scope TEXT NOT NULL,
    tech_stack TEXT NOT NULL DEFAULT '[]',
    generated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    github_issue_url TEXT,
    project_repo_url TEXT,
    content_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_ideas_category ON ideas(category);
CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_score ON ideas(feasibility_score);
CREATE INDEX IF NOT EXISTS idx_ideas_generated ON ideas(generated_at);
CREATE INDEX IF NOT EXISTS idx_ideas_status_category ON ideas(status, category);

CREATE TABLE IF NOT EXISTS generation_runs (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    idea_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS used_tuples (
    category TEXT NOT NULL,
    concept_idx INTEGER NOT NULL,
    domain_idx INTEGER NOT NULL,
    direction TEXT NOT NULL,
    used_at TEXT NOT NULL,
    PRIMARY KEY (category, concept_idx, domain_idx, direction)
);

CREATE TABLE IF NOT EXISTS category_pair_log (
    cat_a TEXT NOT NULL,
    cat_b TEXT NOT NULL,
    idea_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (cat_a, cat_b, idea_id)
);

CREATE TABLE IF NOT EXISTS idea_reviews (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    reasoning TEXT NOT NULL DEFAULT '',
    suggestions TEXT NOT NULL DEFAULT '[]',
    reviewed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_idea ON idea_reviews(idea_id);
CREATE INDEX IF NOT EXISTS idx_reviews_at ON idea_reviews(reviewed_at);

CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    question TEXT NOT NULL,
    challenge_type TEXT NOT NULL DEFAULT 'freeform',
    focus_area TEXT NOT NULL DEFAULT 'all',
    tone TEXT NOT NULL DEFAULT 'skeptical',
    response TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT 'no_change',
    confidence REAL NOT NULL DEFAULT 0.5,
    changes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_challenges_idea ON challenges(idea_id);

CREATE TABLE IF NOT EXISTS filtered_ideas (
    id TEXT PRIMARY KEY,
    idea_name TEXT NOT NULL,
    idea_tagline TEXT NOT NULL,
    idea_category TEXT NOT NULL,
    filter_reason TEXT NOT NULL,
    original_idea_json TEXT NOT NULL,
    filtered_at TEXT NOT NULL,
    similar_to_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_filtered_category ON filtered_ideas(idea_category);
CREATE INDEX IF NOT EXISTS idx_filtered_reason ON filtered_ideas(filter_reason);
CREATE INDEX IF NOT EXISTS idx_filtered_filtered_at ON filtered_ideas(filtered_at);
CREATE INDEX IF NOT EXISTS idx_filtered_similar_to ON filtered_ideas(similar_to_id) WHERE similar_to_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT,
    categories TEXT NOT NULL DEFAULT '[]',
    idea_count INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idea_denials (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    denied_by TEXT,
    denied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_denials_idea ON idea_denials(idea_id);

CREATE TABLE IF NOT EXISTS selection_rounds (
    id TEXT PRIMARY KEY,
    round_number INTEGER NOT NULL,
    idea_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    results TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_registry (
    id TEXT PRIMARY KEY,
    repo_full_name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    topics TEXT NOT NULL DEFAULT '[]',
    last_synced TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_decisions (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_repo TEXT,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    decided_at TEXT NOT NULL
);

-- v0.17 Scoreboard: realized outcome signals for the engine's bets, so
-- build_calibration can check predicted-vs-realized per axis/category.
CREATE TABLE IF NOT EXISTS outcome_signals (
    id TEXT PRIMARY KEY,
    idea_id TEXT,
    axis TEXT NOT NULL,
    predicted REAL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    entity_ref TEXT,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outcome_idea ON outcome_signals(idea_id);
CREATE INDEX IF NOT EXISTS idx_outcome_axis ON outcome_signals(axis);

-- v0.17 Scoreboard auto-tune: learned per-(category, axis) score nudges,
-- applied by the heuristic scorers when present. Empty by default.
CREATE TABLE IF NOT EXISTS calibration_weights (
    category TEXT NOT NULL,
    axis TEXT NOT NULL,
    nudge REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (category, axis)
);

-- v0.18 Missions (#84): operator directives the think tank generates
-- against. last_generated_at is the mission cadence's watermark.
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    urls TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_generated_at TEXT
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self.query_times: list[float] = []  # recent query durations in seconds
        # Asyncio-level write serializer. aiosqlite already serializes
        # statements on a single connection, but cadences can interleave
        # their multi-statement transactions (an LLM call mid-transaction
        # holds the writer for seconds) and SQLite returns "database is
        # locked" when busy_timeout exhausts. The lock makes writes wait
        # politely in-process instead of racing through fcntl into
        # busy-timeout territory. Acquired by the `_write_serialized`
        # helper; safe methods (reads) skip it.
        import asyncio

        self._write_lock = asyncio.Lock()

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # Hardening: WAL mode + generous busy_timeout for concurrent safety.
        # 60s wins over a user-visible 500 every time. Cadences holding the
        # writer for tens of seconds (Haiku-mid-transaction) just have to
        # wait politely behind the asyncio lock above.
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 60000")
        # synchronous=NORMAL is the standard WAL pairing — durable on commit
        # but skips the fsync after every page write that FULL imposes.
        await self._db.execute("PRAGMA synchronous = NORMAL")
        # cache_spill=OFF keeps a tiny transaction from triggering disk I/O
        # mid-flight; matters because our typical write is one INSERT/UPDATE.
        await self._db.execute("PRAGMA cache_spill = OFF")
        # WAL autocheckpoint at 1000 pages (~4MB) — keeps the WAL bounded.
        await self._db.execute("PRAGMA wal_autocheckpoint = 1000")
        await self._db.executescript(SCHEMA)
        # Migration discipline: CREATE TABLE IF NOT EXISTS does NOTHING when
        # the table already exists, so adding a column to the SCHEMA literal
        # above is invisible to existing DBs unless we ALTER TABLE here.
        # Issue #68 ate this lesson the hard way (challenges table). Every
        # column added to an existing table must be mirrored below.
        for stmt in (
            # ideas table
            "ALTER TABLE ideas ADD COLUMN content_hash TEXT",
            "ALTER TABLE ideas ADD COLUMN source_url TEXT",
            # challenges table — these columns shipped in the SCHEMA literal
            # without migrations, so prod DBs created before that change
            # were stuck on a 6-column table. Issue #68.
            "ALTER TABLE challenges ADD COLUMN challenge_type TEXT NOT NULL DEFAULT 'freeform'",
            "ALTER TABLE challenges ADD COLUMN focus_area TEXT NOT NULL DEFAULT 'all'",
            "ALTER TABLE challenges ADD COLUMN tone TEXT NOT NULL DEFAULT 'skeptical'",
            "ALTER TABLE challenges ADD COLUMN verdict TEXT NOT NULL DEFAULT 'no_change'",
            "ALTER TABLE challenges ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5",
            # Issue #70 — track whether a challenge's proposed changes
            # have been applied to the idea (for idempotency).
            "ALTER TABLE challenges ADD COLUMN applied_at TEXT",
            # Issue #71 — track WHY an idea was archived and WHEN, so the
            # retroactive siphon is fully reversible and visible in the UI.
            "ALTER TABLE ideas ADD COLUMN archived_reason TEXT",
            "ALTER TABLE ideas ADD COLUMN archived_at TEXT",
            # v0.13 — which llm_generator mode produced this idea (null for
            # template-only generations) and how monetizable the engine
            # judges it (separate axis from feasibility).
            "ALTER TABLE ideas ADD COLUMN generation_mode TEXT",
            "ALTER TABLE ideas ADD COLUMN fundability_score REAL",
            # v0.14 — auto-promote stamp so the money-flipper cadence
            # is idempotent. NULL = never auto-promoted; non-NULL = the
            # weekly picker should skip this idea.
            "ALTER TABLE ideas ADD COLUMN auto_promoted_at TEXT",
            # v0.15 — frontier scoring axis (parallel to fundability_score)
            # for the Claude-ecosystem categories. 0.0 = derivative,
            # 1.0 = paradigm-shift potential. Sorted DESC on /claude-lab.
            "ALTER TABLE ideas ADD COLUMN ambition_score REAL",
            # v0.15a — which SHAPE of artifact this idea pitches. Only the
            # Claude Lab categories rotate through 8 types; everything else
            # stays NULL (= default project-pitch shape).
            "ALTER TABLE ideas ADD COLUMN artifact_type TEXT",
            # v0.16 — Sniper board: competitive-displacement axis (parallel
            # to fundability/ambition) and the named incumbent the wedge
            # targets. Both NULL for non-snipe ideas. Sorted DESC on /sniper.
            "ALTER TABLE ideas ADD COLUMN snipe_score REAL",
            "ALTER TABLE ideas ADD COLUMN target_incumbent TEXT",
            # v0.18 — Missions (#84): link an idea to the operator directive
            # it was generated against. NULL for the engine's own rotation.
            "ALTER TABLE ideas ADD COLUMN mission_id TEXT",
            # v0.20 — Cashflow board (#96): time-to-first-dollar axis
            # (parallel to fundability/ambition/snipe). NULL outside the
            # cashflow categories. Sorted DESC on /cashflow.
            "ALTER TABLE ideas ADD COLUMN cashflow_score REAL",
        ):
            try:
                await self._db.execute(stmt)
            except Exception:  # noqa: S110, BLE001
                pass  # column already exists — idempotent
        # Add indexes (safe to re-run)
        await self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ideas_content_hash "
            "ON ideas(content_hash) WHERE content_hash IS NOT NULL"
        )
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("Database not connected")
        return self._db

    async def _profile_query(self, sql: str, params: tuple | list = (), *, fetch: str = "all"):
        """Execute a query with timing. Logs slow queries as warnings."""
        start = time.monotonic()
        cursor = await self.db.execute(sql, params)
        if fetch == "one":
            result = await cursor.fetchone()
        else:
            result = await cursor.fetchall()
        elapsed = time.monotonic() - start

        # Keep last 1000 timings
        self.query_times.append(elapsed)
        if len(self.query_times) > 1000:
            self.query_times = self.query_times[-1000:]

        elapsed_ms = elapsed * 1000
        if elapsed >= _SLOW_QUERY_THRESHOLD:
            logger.warning("Slow query (%.1fms): %s", elapsed_ms, sql[:120])
        else:
            logger.debug("Query completed (%.1fms): %s", elapsed_ms, sql[:80])

        return result

    def get_query_stats(self) -> dict:
        """Return profiling statistics for recent queries."""
        if not self.query_times:
            return {"total_queries": 0, "avg_ms": 0.0, "max_ms": 0.0, "slow_count": 0}
        return {
            "total_queries": len(self.query_times),
            "avg_ms": round(sum(self.query_times) / len(self.query_times) * 1000, 2),
            "max_ms": round(max(self.query_times) * 1000, 2),
            "slow_count": sum(1 for t in self.query_times if t >= _SLOW_QUERY_THRESHOLD),
        }

    # === IDEA CRUD ===

    async def save_idea(self, idea: Idea) -> Idea:
        content_hash = getattr(idea, "content_hash", None)
        auto_ts = getattr(idea, "auto_promoted_at", None)
        async with self._write_lock:
            await self.db.execute(
                """INSERT OR REPLACE INTO ideas
                (id, name, tagline, description, category, market_analysis,
                 feasibility_score, mvp_scope, tech_stack, generated_at, status,
                 github_issue_url, project_repo_url, content_hash, source_url,
                 generation_mode, fundability_score, auto_promoted_at, ambition_score,
                artifact_type, snipe_score, target_incumbent, mission_id, cashflow_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    idea.id,
                    idea.name,
                    idea.tagline,
                    idea.description,
                    idea.category.value,
                    idea.market_analysis,
                    idea.feasibility_score,
                    idea.mvp_scope,
                    json.dumps(idea.tech_stack),
                    idea.generated_at.isoformat(),
                    idea.status,
                    idea.github_issue_url,
                    idea.project_repo_url,
                    content_hash,
                    idea.source_url,
                    getattr(idea, "generation_mode", None),
                    getattr(idea, "fundability_score", None),
                    auto_ts.isoformat() if auto_ts is not None else None,
                    getattr(idea, "ambition_score", None),
                    getattr(idea, "artifact_type", None),
                    getattr(idea, "snipe_score", None),
                    getattr(idea, "target_incumbent", None),
                    getattr(idea, "mission_id", None),
                    getattr(idea, "cashflow_score", None),
                ),
            )
            await self.db.commit()
        return idea

    async def get_idea(self, idea_id: str) -> Idea | None:
        cursor = await self.db.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_idea(row)

    async def list_ideas(
        self,
        status: IdeaStatus | None = None,
        category: IdeaCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Idea]:
        _validate_status(status)
        query = "SELECT * FROM ideas WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category.value)
        query += " ORDER BY generated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await self._profile_query(query, params)
        return [self._row_to_idea(row) for row in rows]

    async def update_idea_status(self, idea_id: str, status: IdeaStatus) -> Idea | None:
        _validate_status(status)
        async with self._write_lock:
            await self.db.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))
            await self.db.commit()
        return await self.get_idea(idea_id)

    async def delete_idea(self, idea_id: str) -> None:
        """Hard-delete an idea by ID. No-op if the idea does not exist."""
        async with self._write_lock:
            await self.db.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
            await self.db.commit()

    async def update_idea_urls(
        self, idea_id: str, github_issue_url: str | None = None, project_repo_url: str | None = None
    ) -> Idea | None:
        async with self._write_lock:
            if github_issue_url is not None:
                await self.db.execute("UPDATE ideas SET github_issue_url = ? WHERE id = ?", (github_issue_url, idea_id))
            if project_repo_url is not None:
                await self.db.execute("UPDATE ideas SET project_repo_url = ? WHERE id = ?", (project_repo_url, idea_id))
            await self.db.commit()
        return await self.get_idea(idea_id)

    # === COUNTING & SEARCH (SQL-optimized, no Python-side filtering) ===

    async def count_ideas(self, status: IdeaStatus | None = None) -> int:
        _validate_status(status)
        if status:
            cursor = await self.db.execute("SELECT COUNT(*) FROM ideas WHERE status = ?", (status,))
        else:
            cursor = await self.db.execute("SELECT COUNT(*) FROM ideas")
        row = await cursor.fetchone()
        return row[0]

    async def count_ideas_by_category(self) -> dict[str, int]:
        """SQL GROUP BY for category counts -- no in-memory loading."""
        cursor = await self.db.execute("SELECT category, COUNT(*) FROM ideas GROUP BY category")
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def list_challenged_ideas(self, limit: int = 50, offset: int = 0) -> list[Idea]:
        """Ideas that have at least one challenge filed against them."""
        cursor = await self.db.execute(
            """SELECT i.* FROM ideas i
            WHERE EXISTS (SELECT 1 FROM challenges c WHERE c.idea_id = i.id)
            ORDER BY i.generated_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_idea(row) for row in rows]

    async def count_challenged_ideas(self) -> int:
        """Distinct ideas with one or more challenges."""
        cursor = await self.db.execute(
            """SELECT COUNT(DISTINCT idea_id) FROM challenges
            WHERE idea_id IN (SELECT id FROM ideas)"""
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def search_ideas(self, query: str, limit: int = 50, offset: int = 0) -> list[Idea]:
        """SQL LIKE search -- no Python-side filtering."""
        like_q = f"%{query}%"
        cursor = await self.db.execute(
            """SELECT * FROM ideas
            WHERE name LIKE ? OR tagline LIKE ? OR description LIKE ?
            ORDER BY feasibility_score DESC LIMIT ? OFFSET ?""",
            (like_q, like_q, like_q, limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_idea(row) for row in rows]

    async def get_all_idea_names(self) -> list[str]:
        """Return just names -- lightweight, no full object loading."""
        cursor = await self.db.execute("SELECT name FROM ideas ORDER BY generated_at DESC")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_recent_categories(self, limit: int = 3) -> list[str]:
        cursor = await self.db.execute("SELECT category FROM ideas ORDER BY generated_at DESC LIMIT ?", (limit,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # === USED TUPLES (input-space dedup) ===

    async def record_used_tuple(self, category: str, concept_idx: int, domain_idx: int, direction: str) -> None:
        """Record a (category, concept, domain, direction) tuple as used."""
        await self.db.execute(
            """INSERT OR IGNORE INTO used_tuples
            (category, concept_idx, domain_idx, direction, used_at)
            VALUES (?, ?, ?, ?, ?)""",
            (category, concept_idx, domain_idx, direction, datetime.now(UTC).isoformat()),
        )
        await self.db.commit()

    async def is_tuple_used(self, category: str, concept_idx: int, domain_idx: int, direction: str) -> bool:
        """Check if a generation tuple has been used."""
        cursor = await self.db.execute(
            """SELECT 1 FROM used_tuples
            WHERE category = ? AND concept_idx = ? AND domain_idx = ? AND direction = ?""",
            (category, concept_idx, domain_idx, direction),
        )
        return await cursor.fetchone() is not None

    async def get_unused_tuple_count(self, category: str) -> int:
        """Count how many tuples have NOT been used for a category.

        This is approximate -- based on the seed data dimensions.
        """
        from project_forge.engine.categories import CATEGORY_SEEDS

        cat_enum = IdeaCategory(category)
        seeds = CATEGORY_SEEDS.get(cat_enum, {})
        n_concepts = len(seeds.get("seed_concepts", []))
        n_domains = len(seeds.get("domains_to_cross", []))
        total = n_concepts * n_domains * 4  # 4 directions

        cursor = await self.db.execute("SELECT COUNT(*) FROM used_tuples WHERE category = ?", (category,))
        row = await cursor.fetchone()
        used = row[0] if row else 0
        return max(0, total - used)

    # === CATEGORY PAIR TRACKING (horizontal expansion) ===

    async def record_category_pair(self, cat_a: str, cat_b: str, idea_id: str) -> None:
        """Record that an idea bridges two categories. Normalizes cat_a < cat_b."""
        a, b = (cat_a, cat_b) if cat_a < cat_b else (cat_b, cat_a)
        await self.db.execute(
            """INSERT OR IGNORE INTO category_pair_log
            (cat_a, cat_b, idea_id, generated_at) VALUES (?, ?, ?, ?)""",
            (a, b, idea_id, datetime.now(UTC).isoformat()),
        )
        await self.db.commit()

    async def get_least_explored_pairs(self, limit: int = 78) -> list[tuple[str, str, int]]:
        """Return all 66 category pairs sorted by exploration count ascending."""
        all_cats = [c.value for c in IdeaCategory]
        all_pairs = []
        for i, a in enumerate(all_cats):
            for b in all_cats[i + 1 :]:
                all_pairs.append((a, b) if a < b else (b, a))

        cursor = await self.db.execute(
            "SELECT cat_a, cat_b, COUNT(*) as cnt FROM category_pair_log GROUP BY cat_a, cat_b"
        )
        rows = await cursor.fetchall()
        counts = {(row[0], row[1]): row[2] for row in rows}

        result = [(a, b, counts.get((a, b), 0)) for a, b in all_pairs]
        result.sort(key=lambda x: x[2])
        return result[:limit]

    # === SUPER IDEAS ===

    async def list_super_ideas(self, limit: int | None = None) -> list[Idea]:
        """List super ideas deduped by keyword base name.

        Groups variants like "[SUPER] Well Known Defense Suite" and
        "[SUPER] Well Known Operations Center" together (same keywords, different
        synthesis suffix), keeping the highest-scored non-archived variant per base.
        Pass limit=N to cap the result; omit for all active super ideas.
        """
        from project_forge.engine.dedup import _super_base_name

        cursor = await self.db.execute(
            "SELECT * FROM ideas WHERE name LIKE '[SUPER]%' "
            "AND status NOT IN ('rejected', 'archived', 'contributed', 'implemented') "
            "ORDER BY feasibility_score DESC",
        )
        rows = await cursor.fetchall()

        seen_bases: dict[str, None] = {}
        result: list[Idea] = []
        for row in rows:
            base = _super_base_name(row["name"])
            if base not in seen_bases:
                seen_bases[base] = None
                result.append(self._row_to_idea(row))
                if limit is not None and len(result) >= limit:
                    break
        return result

    # === GENERATION RUNS ===

    async def save_run(self, run: GenerationRun) -> GenerationRun:
        await self.db.execute(
            """INSERT OR REPLACE INTO generation_runs
            (id, category, idea_id, started_at, completed_at, success, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run.id,
                run.category.value,
                run.idea_id,
                run.started_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
                1 if run.success else 0,
                run.error,
            ),
        )
        await self.db.commit()
        return run

    # === STATS ===

    async def get_stats(self) -> dict:
        ideas_by_status = {}
        cursor = await self.db.execute("SELECT status, COUNT(*) FROM ideas GROUP BY status")
        for row in await cursor.fetchall():
            ideas_by_status[row[0]] = row[1]

        # Active vs archived split — after the #71 siphon, total_ideas
        # alone is misleading because most rows are archived dedup victims.
        # The dashboard headline should be active.
        ARCHIVED_STATUSES = {"archived", "rejected"}
        total_active = sum(v for k, v in ideas_by_status.items() if k not in ARCHIVED_STATUSES)
        total_archived = ideas_by_status.get("archived", 0)
        total_rejected = ideas_by_status.get("rejected", 0)

        ideas_by_category = await self.count_ideas_by_category()

        cursor = await self.db.execute("SELECT COUNT(*) FROM generation_runs")
        row = await cursor.fetchone()
        total_runs = row[0] if row else 0

        cursor = await self.db.execute("SELECT AVG(feasibility_score) FROM ideas")
        row = await cursor.fetchone()
        avg_score = round(row[0], 2) if row and row[0] else 0.0

        # Average feasibility over ACTIVE ideas only — archived rows would
        # drag the visible average toward historical noise.
        cursor = await self.db.execute(
            "SELECT AVG(feasibility_score) FROM ideas WHERE status NOT IN ('archived', 'rejected')",
        )
        row = await cursor.fetchone()
        avg_feasibility_active = round(row[0], 2) if row and row[0] else 0.0

        super_count = len(await self.list_super_ideas())

        cursor = await self.db.execute("SELECT COUNT(*) FROM challenges")
        row = await cursor.fetchone()
        challenge_count = row[0] if row else 0

        cursor = await self.db.execute("SELECT COUNT(*) FROM selection_rounds")
        row = await cursor.fetchone()
        total_rounds = row[0] if row else 0

        cursor = await self.db.execute("SELECT COUNT(*) FROM idea_denials")
        row = await cursor.fetchone()
        total_denials = row[0] if row else 0

        # v0.14 money-bot tile signals. Derived from the canonical grouping
        # so the count always matches what /money-bots actually lists.
        money_cats = tuple(c.value for c in MONEY_CATEGORIES)
        placeholders = ",".join("?" * len(money_cats))
        cursor = await self.db.execute(
            f"SELECT COUNT(*) FROM ideas WHERE category IN ({placeholders}) "  # noqa: S608
            f"AND status NOT IN ('archived', 'rejected')",
            money_cats,
        )
        row = await cursor.fetchone()
        money_bot_count = row[0] if row else 0

        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE auto_promoted_at IS NOT NULL",
        )
        row = await cursor.fetchone()
        auto_promoted_count = row[0] if row else 0

        return {
            # total_ideas kept for backward compat (existing tests / integrations).
            # Dashboard prefers total_active for the headline tile.
            "total_ideas": sum(ideas_by_status.values()),
            "total_active": total_active,
            "total_archived": total_archived,
            "total_rejected": total_rejected,
            "avg_feasibility_active": avg_feasibility_active,
            "ideas_by_status": ideas_by_status,
            "ideas_by_category": ideas_by_category,
            "total_runs": total_runs,
            "avg_feasibility_score": avg_score,
            "super_ideas": super_count,
            "total_challenges": challenge_count,
            "total_rounds": total_rounds,
            "total_denials": total_denials,
            "money_bot_count": money_bot_count,
            "auto_promoted_count": auto_promoted_count,
        }

    # === CHALLENGES ===

    async def save_challenge(self, challenge: Challenge) -> Challenge:
        await self.db.execute(
            """INSERT INTO challenges
            (id, idea_id, question, challenge_type, focus_area, tone,
             response, verdict, confidence, changes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                challenge.id,
                challenge.idea_id,
                challenge.question,
                challenge.challenge_type,
                challenge.focus_area,
                challenge.tone,
                challenge.response,
                challenge.verdict,
                challenge.confidence,
                json.dumps(challenge.changes),
                challenge.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return challenge

    async def get_challenge(self, challenge_id: str) -> Challenge | None:
        cursor = await self.db.execute(
            "SELECT * FROM challenges WHERE id = ?",
            (challenge_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        def _parse_ts(s: str | None) -> datetime | None:
            if not s:
                return None
            ts = datetime.fromisoformat(s)
            return ts.replace(tzinfo=UTC) if "+" not in s else ts

        return Challenge(
            id=row["id"],
            idea_id=row["idea_id"],
            question=row["question"],
            challenge_type=row["challenge_type"],
            focus_area=row["focus_area"],
            tone=row["tone"],
            response=row["response"],
            verdict=row["verdict"],
            confidence=row["confidence"],
            changes=json.loads(row["changes"]),
            created_at=_parse_ts(row["created_at"]) or datetime.now(UTC),
            applied_at=_parse_ts(row["applied_at"]),
        )

    async def is_challenge_applied(self, challenge_id: str) -> bool:
        """True if the challenge has already been applied via mark_challenge_applied."""
        cursor = await self.db.execute(
            "SELECT applied_at FROM challenges WHERE id = ?",
            (challenge_id,),
        )
        row = await cursor.fetchone()
        return bool(row and row["applied_at"])

    async def mark_challenge_applied(self, challenge_id: str) -> None:
        await self.db.execute(
            "UPDATE challenges SET applied_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), challenge_id),
        )
        await self.db.commit()

    async def apply_idea_field_updates(
        self,
        idea_id: str,
        updates: dict[str, str | float | list],
    ) -> Idea | None:
        """Apply a dict of field→new_value updates to an idea.

        Whitelisted fields only: mvp_scope, description, market_analysis,
        tagline, feasibility_score, tech_stack. Returns the refreshed Idea.
        """
        ALLOWED = {
            "mvp_scope",
            "description",
            "market_analysis",
            "tagline",
            "feasibility_score",
            "tech_stack",
        }
        clean: dict[str, object] = {}
        for k, v in updates.items():
            if k not in ALLOWED:
                continue
            if k == "feasibility_score":
                try:
                    clean[k] = max(0.0, min(1.0, float(v)))
                except (TypeError, ValueError):
                    continue
            elif k == "tech_stack":
                if isinstance(v, list):
                    clean[k] = json.dumps(v)
                elif isinstance(v, str):
                    clean[k] = json.dumps([s.strip() for s in v.split(",") if s.strip()])
            else:
                clean[k] = str(v)

        if not clean:
            return await self.get_idea(idea_id)

        set_clause = ", ".join(f"{k} = ?" for k in clean)
        params = list(clean.values()) + [idea_id]
        await self.db.execute(
            f"UPDATE ideas SET {set_clause} WHERE id = ?",  # noqa: S608 (allowlist'd keys)
            params,
        )
        await self.db.commit()
        return await self.get_idea(idea_id)

    async def list_challenges(self, idea_id: str) -> list[Challenge]:
        cursor = await self.db.execute(
            "SELECT * FROM challenges WHERE idea_id = ? ORDER BY created_at ASC",
            (idea_id,),
        )
        rows = await cursor.fetchall()

        def _parse_ts(s: str | None) -> datetime | None:
            if not s:
                return None
            ts = datetime.fromisoformat(s)
            return ts.replace(tzinfo=UTC) if "+" not in s else ts

        return [
            Challenge(
                id=row["id"],
                idea_id=row["idea_id"],
                question=row["question"],
                challenge_type=row["challenge_type"],
                focus_area=row["focus_area"],
                tone=row["tone"],
                response=row["response"],
                verdict=row["verdict"],
                confidence=row["confidence"],
                changes=json.loads(row["changes"]),
                created_at=_parse_ts(row["created_at"]) or datetime.now(UTC),
                applied_at=_parse_ts(row["applied_at"]),
            )
            for row in rows
        ]

    # === IDEA REVIEWS ===

    async def fetch_ideas_for_review(self, limit: int = 10, min_age_days: int = 7) -> list[Idea]:
        """Fetch ideas needing review: never reviewed or reviewed > min_age_days ago.

        Skips rejected/archived ideas. Returns oldest-generated first.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=min_age_days)).isoformat()
        cursor = await self.db.execute(
            """SELECT i.* FROM ideas i
            LEFT JOIN (
                SELECT idea_id, MAX(reviewed_at) AS last_reviewed
                FROM idea_reviews GROUP BY idea_id
            ) r ON i.id = r.idea_id
            WHERE i.status NOT IN ('rejected', 'archived')
              AND (r.last_reviewed IS NULL OR r.last_reviewed < ?)
            ORDER BY i.generated_at ASC
            LIMIT ?""",
            (cutoff, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_idea(row) for row in rows]

    async def record_review(
        self,
        idea_id: str,
        verdict: str,
        confidence: float,
        reasoning: str = "",
        suggestions: list | None = None,
        reviewed_at: datetime | None = None,
    ) -> None:
        """Store a review verdict for an idea."""
        from uuid import uuid4

        review_id = uuid4().hex[:12]
        ts = (reviewed_at or datetime.now(UTC)).isoformat()
        await self.db.execute(
            """INSERT INTO idea_reviews (id, idea_id, verdict, confidence, reasoning, suggestions, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (review_id, idea_id, verdict, confidence, reasoning, json.dumps(suggestions or []), ts),
        )
        await self.db.commit()

    async def get_idea_reviews(self, idea_id: str) -> list[dict]:
        """Return all reviews for an idea, oldest first."""
        cursor = await self.db.execute(
            "SELECT * FROM idea_reviews WHERE idea_id = ? ORDER BY reviewed_at ASC",
            (idea_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "idea_id": row["idea_id"],
                "verdict": row["verdict"],
                "confidence": row["confidence"],
                "reasoning": row["reasoning"],
                "suggestions": json.loads(row["suggestions"]),
                "reviewed_at": row["reviewed_at"],
            }
            for row in rows
        ]

    # === IDEA DENIALS ===

    async def save_denial(self, denial: IdeaDenial) -> IdeaDenial:
        """Save a denial record and set the idea status to 'rejected'."""
        await self.db.execute(
            """INSERT INTO idea_denials (id, idea_id, reason, denied_by, denied_at)
            VALUES (?, ?, ?, ?, ?)""",
            (denial.id, denial.idea_id, denial.reason, denial.denied_by, denial.denied_at.isoformat()),
        )
        await self.db.execute("UPDATE ideas SET status = 'rejected' WHERE id = ?", (denial.idea_id,))
        await self.db.commit()
        return denial

    async def get_denials(self, idea_id: str) -> list[IdeaDenial]:
        """Return all denials for an idea, oldest first."""
        cursor = await self.db.execute(
            "SELECT * FROM idea_denials WHERE idea_id = ? ORDER BY denied_at ASC",
            (idea_id,),
        )
        rows = await cursor.fetchall()
        return [
            IdeaDenial(
                id=row["id"],
                idea_id=row["idea_id"],
                reason=row["reason"],
                denied_by=row["denied_by"],
                denied_at=datetime.fromisoformat(row["denied_at"]),
            )
            for row in rows
        ]

    # === SELECTION ROUNDS ===

    async def save_round(self, sr: SelectionRound) -> SelectionRound:
        """Save a selection round."""
        await self.db.execute(
            """INSERT INTO selection_rounds (id, round_number, idea_ids, status, results, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                sr.id,
                sr.round_number,
                json.dumps(sr.idea_ids),
                sr.status,
                json.dumps(sr.results),
                sr.created_at.isoformat(),
            ),
        )
        await self.db.commit()
        return sr

    async def get_round(self, round_id: str) -> SelectionRound | None:
        """Get a selection round by ID."""
        cursor = await self.db.execute("SELECT * FROM selection_rounds WHERE id = ?", (round_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return SelectionRound(
            id=row["id"],
            round_number=row["round_number"],
            idea_ids=json.loads(row["idea_ids"]),
            status=row["status"],
            results=json.loads(row["results"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def list_rounds(self) -> list[SelectionRound]:
        """List all selection rounds, newest first."""
        cursor = await self.db.execute("SELECT * FROM selection_rounds ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [
            SelectionRound(
                id=row["id"],
                round_number=row["round_number"],
                idea_ids=json.loads(row["idea_ids"]),
                status=row["status"],
                results=json.loads(row["results"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    async def update_round_status(self, round_id: str, status: str) -> SelectionRound | None:
        """Update a round's status."""
        await self.db.execute("UPDATE selection_rounds SET status = ? WHERE id = ?", (status, round_id))
        await self.db.commit()
        return await self.get_round(round_id)

    async def save_round_results(self, round_id: str, results: list[dict]) -> SelectionRound | None:
        """Save comparison results and mark round completed."""
        await self.db.execute(
            "UPDATE selection_rounds SET results = ?, status = 'completed' WHERE id = ?",
            (json.dumps(results), round_id),
        )
        await self.db.commit()
        return await self.get_round(round_id)

    # === DEDUP CLEANUP ===

    async def deduplicate_si_ideas(self) -> dict:
        """Deduplicate existing self-improvement ideas by normalized tagline.

        Groups active (non-rejected) SI ideas by normalized tagline.
        Within each group, keeps the best one (approved beats new; then highest score).
        Rejects the rest.

        Returns dict with 'kept', 'rejected', and 'groups' counts.
        """
        from project_forge.engine.dedup import SIMILARITY_THRESHOLD, _normalize

        cursor = await self.db.execute(
            "SELECT id, tagline, feasibility_score, status FROM ideas WHERE category = ? AND status != 'rejected'",
            (IdeaCategory.SELF_IMPROVEMENT.value,),
        )
        rows = await cursor.fetchall()

        # Group by normalized tagline
        groups: dict[str, list[dict]] = {}
        for row in rows:
            key = _normalize(row["tagline"])
            entry = {
                "id": row["id"],
                "tagline": row["tagline"],
                "score": row["feasibility_score"],
                "status": row["status"],
            }
            # Find existing group with similar key (Jaccard >= threshold)
            matched_key = None
            for existing_key in groups:
                existing_tokens = set(existing_key.split())
                new_tokens = set(key.split())
                if not existing_tokens and not new_tokens:
                    matched_key = existing_key
                    break
                if existing_tokens and new_tokens:
                    jaccard = len(existing_tokens & new_tokens) / len(existing_tokens | new_tokens)
                    if jaccard >= SIMILARITY_THRESHOLD:
                        matched_key = existing_key
                        break
            if matched_key is not None:
                groups[matched_key].append(entry)
            else:
                groups[key] = [entry]

        rejected_count = 0
        kept_count = 0

        for _key, members in groups.items():
            if len(members) <= 1:
                kept_count += 1
                continue

            # Sort: approved first, then by score descending
            def sort_key(m: dict) -> tuple:
                status_priority = 0 if m["status"] == "approved" else 1
                return (status_priority, -m["score"])

            members.sort(key=sort_key)
            kept_count += 1

            for dup in members[1:]:
                await self.db.execute(
                    "UPDATE ideas SET status = 'rejected' WHERE id = ?",
                    (dup["id"],),
                )
                rejected_count += 1

        await self.db.commit()
        return {"kept": kept_count, "rejected": rejected_count, "groups": len(groups)}

    async def deduplicate_super_ideas(self) -> dict:
        """Deduplicate super ideas by base name (stripping parenthetical suffixes).

        Groups [SUPER] ideas by base name, keeps the highest-scored per group,
        archives the rest. Returns dict with 'kept', 'archived', 'groups' counts.
        """
        import re

        cursor = await self.db.execute(
            "SELECT id, name, feasibility_score, status FROM ideas "
            "WHERE name LIKE '[SUPER]%' AND status NOT IN ('rejected', 'archived', 'contributed', 'implemented')",
        )
        rows = await cursor.fetchall()

        groups: dict[str, list[dict]] = {}
        for row in rows:
            # Strip "[SUPER] " prefix and any parenthetical suffix
            raw_name = row["name"].replace("[SUPER] ", "")
            base = re.sub(r"\s*\([^)]+\)\s*$", "", raw_name).strip()
            entry = {
                "id": row["id"],
                "name": row["name"],
                "score": row["feasibility_score"],
                "status": row["status"],
            }
            groups.setdefault(base, []).append(entry)

        archived_count = 0
        kept_count = 0

        for _base, members in groups.items():
            if len(members) <= 1:
                kept_count += 1
                continue

            members.sort(key=lambda m: -m["score"])
            kept_count += 1

            for dup in members[1:]:
                await self.db.execute(
                    "UPDATE ideas SET status = 'archived' WHERE id = ?",
                    (dup["id"],),
                )
                archived_count += 1

        await self.db.commit()
        return {"kept": kept_count, "archived": archived_count, "groups": len(groups)}

    async def purge_bad_super_ideas(self) -> set[str]:
        """Archive super ideas whose names contain pre-fix quality defects.

        Targets:
        - Hyphenated concept words (Certificate-Pinning, Data-Cardinality)
        - Names whose base concept is entirely stop words (Multi Control, Well Known,
          Insecure Direct)

        Skips ideas in terminal statuses: approved, scaffolded, implemented, contributed.
        Returns the set of archived idea IDs.
        """
        import re

        from project_forge.engine.dedup import _super_base_name
        from project_forge.engine.super_ideas import _NAME_STOP_WORDS

        cursor = await self.db.execute(
            "SELECT id, name FROM ideas WHERE name LIKE '[SUPER]%' "
            "AND status NOT IN ('approved', 'scaffolded', 'implemented', 'contributed', 'archived', 'rejected')",
        )
        rows = await cursor.fetchall()

        archived: set[str] = set()
        for row in rows:
            name = row["name"]
            core = name.replace("[SUPER] ", "")

            # Detect hyphenated concept words: Certificate-Pinning, Data-Cardinality
            if re.search(r"[A-Za-z]+-[A-Za-z]+", core):
                await self.db.execute("UPDATE ideas SET status = 'archived' WHERE id = ?", (row["id"],))
                archived.add(row["id"])
                continue

            # Require 2 meaningful keywords (5+ chars, not stop words) in base concept.
            # Single-keyword or all-stop-word bases indicate low-quality clusters.
            # e.g. "migration post" has only 1 meaningful word; "mapper multi" has 0.
            base = _super_base_name(name)
            meaningful = [w for w in base.split() if len(w) >= 5 and w not in _NAME_STOP_WORDS]
            if len(meaningful) < 2:
                await self.db.execute("UPDATE ideas SET status = 'archived' WHERE id = ?", (row["id"],))
                archived.add(row["id"])

        await self.db.commit()
        return archived

    async def verify_integrity(self) -> dict[str, list[str]]:
        """Audit cross-table invariants. Returns a violation report.

        Buckets:
        - orphaned_filtered_similar_to: filtered_ideas.similar_to_id points at a
          missing/deleted idea. Pollutes saturation telemetry.
        - duplicate_active_content_hash: two non-archived/non-rejected ideas share
          the same content_hash. Should be impossible due to unique partial index,
          but caught here in case the index was dropped or migration added rows.
        - super_idea_base_collisions: two active [SUPER] ideas normalize to the
          same _super_base_name. Means dedup escaped during generation.

        Empty lists for each bucket = clean DB.
        """
        from project_forge.engine.dedup import _super_base_name

        report: dict[str, list[str]] = {
            "orphaned_filtered_similar_to": [],
            "duplicate_active_content_hash": [],
            "super_idea_base_collisions": [],
        }

        # 1. Orphaned filtered_ideas.similar_to_id
        cursor = await self.db.execute(
            "SELECT f.id, f.similar_to_id FROM filtered_ideas f "
            "WHERE f.similar_to_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ideas i WHERE i.id = f.similar_to_id)",
        )
        for row in await cursor.fetchall():
            report["orphaned_filtered_similar_to"].append(
                f"{row[0]} -> {row[1]}",
            )

        # 2. Duplicate active content_hash
        cursor = await self.db.execute(
            "SELECT content_hash, COUNT(*) AS n, GROUP_CONCAT(id) AS ids "
            "FROM ideas "
            "WHERE content_hash IS NOT NULL "
            "AND status NOT IN ('rejected', 'archived') "
            "GROUP BY content_hash HAVING n > 1",
        )
        for row in await cursor.fetchall():
            report["duplicate_active_content_hash"].append(
                f"hash={row[0]} ids={row[2]}",
            )

        # 3. Super-idea base-name collisions among active supers
        cursor = await self.db.execute(
            "SELECT id, name FROM ideas WHERE name LIKE '[SUPER]%' AND status NOT IN ('rejected', 'archived')",
        )
        rows = await cursor.fetchall()
        seen: dict[str, list[str]] = {}
        for row in rows:
            base = _super_base_name(row[1])
            seen.setdefault(base, []).append(row[0])
        for base, ids in seen.items():
            if len(ids) > 1:
                report["super_idea_base_collisions"].append(
                    f"base='{base}' ids={','.join(ids)}",
                )

        return report

    # === FILTERED IDEAS (audit trail) ===

    async def _log_filtered(self, idea: Idea, reason: str, similar_to_id: str | None = None) -> None:
        """Internal: log a filtered idea to the audit trail."""
        fi = FilteredIdea(
            idea_name=idea.name,
            idea_tagline=idea.tagline,
            idea_category=idea.category,
            filter_reason=reason,
            original_idea_json=idea.model_dump_json(),
            similar_to_id=similar_to_id,
        )
        await self.save_filtered_idea(fi)

    async def save_filtered_idea(self, fi: FilteredIdea) -> FilteredIdea:
        """Persist a filtered idea to the audit trail."""
        await self.db.execute(
            """INSERT INTO filtered_ideas
            (id, idea_name, idea_tagline, idea_category, filter_reason,
             original_idea_json, filtered_at, similar_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fi.id,
                fi.idea_name,
                fi.idea_tagline,
                fi.idea_category.value if isinstance(fi.idea_category, IdeaCategory) else fi.idea_category,
                fi.filter_reason,
                fi.original_idea_json,
                fi.filtered_at.isoformat(),
                fi.similar_to_id,
            ),
        )
        await self.db.commit()
        return fi

    async def get_filtered_ideas(
        self,
        category: IdeaCategory | None = None,
        reason_prefix: str | None = None,
        limit: int = 100,
    ) -> list[FilteredIdea]:
        """Query filtered ideas with optional category/reason filters."""
        query = "SELECT * FROM filtered_ideas WHERE 1=1"
        params: list = []
        if category:
            query += " AND idea_category = ?"
            params.append(category.value)
        if reason_prefix:
            query += " AND filter_reason LIKE ?"
            params.append(f"{reason_prefix}%")
        query += " ORDER BY filtered_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_filtered_idea(row) for row in rows]

    async def get_dedup_stats(self) -> dict:
        """Return dedup/filter stats: total, by_reason, by_category."""
        cursor = await self.db.execute("SELECT COUNT(*) FROM filtered_ideas")
        row = await cursor.fetchone()
        total = row[0] if row else 0

        # Group by reason — normalize "duplicate:tagline_similarity:0.85" to "duplicate:tagline_similarity"
        cursor = await self.db.execute("SELECT filter_reason, COUNT(*) FROM filtered_ideas GROUP BY filter_reason")
        raw_reasons = await cursor.fetchall()
        by_reason: dict[str, int] = {}
        for row in raw_reasons:
            reason = row[0]
            parts = reason.split(":")
            # Keep first two parts as key (e.g. "duplicate:content_hash" or "duplicate:tagline_similarity")
            if len(parts) >= 3 and parts[0] == "duplicate" and parts[1] == "tagline_similarity":
                key = "duplicate:tagline_similarity"
            else:
                key = reason
            by_reason[key] = by_reason.get(key, 0) + row[1]

        cursor = await self.db.execute("SELECT idea_category, COUNT(*) FROM filtered_ideas GROUP BY idea_category")
        cat_rows = await cursor.fetchall()
        by_category = {row[0]: row[1] for row in cat_rows}

        return {"total_filtered": total, "by_reason": by_reason, "by_category": by_category}

    @staticmethod
    def _row_to_filtered_idea(row) -> FilteredIdea:
        return FilteredIdea(
            id=row["id"],
            idea_name=row["idea_name"],
            idea_tagline=row["idea_tagline"],
            idea_category=IdeaCategory(row["idea_category"]),
            filter_reason=row["filter_reason"],
            original_idea_json=row["original_idea_json"],
            filtered_at=datetime.fromisoformat(row["filtered_at"]).replace(tzinfo=UTC)
            if "+" not in row["filtered_at"]
            else datetime.fromisoformat(row["filtered_at"]),
            similar_to_id=row["similar_to_id"],
        )

    # === REPO REGISTRY ===

    async def upsert_repo_entry(self, entry: RepoEntry) -> None:
        """Insert or replace a repo entry in the registry."""
        await self.db.execute(
            """INSERT OR REPLACE INTO repo_registry
            (id, repo_full_name, description, topics, last_synced)
            VALUES (?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.repo_full_name,
                entry.description,
                json.dumps(entry.topics),
                entry.last_synced,
            ),
        )
        await self.db.commit()

    async def list_repo_registry(self) -> list[RepoEntry]:
        """Return all repos in the registry, ordered by repo_full_name."""
        cursor = await self.db.execute("SELECT * FROM repo_registry ORDER BY repo_full_name")
        rows = await cursor.fetchall()
        return [
            RepoEntry(
                id=row["id"],
                repo_full_name=row["repo_full_name"],
                description=row["description"],
                topics=json.loads(row["topics"]),
                last_synced=row["last_synced"],
            )
            for row in rows
        ]

    async def save_route_decision(
        self,
        idea_id: str,
        action: str,
        target_repo: str | None,
        reason: str,
        confidence: float,
    ) -> None:
        """Persist a routing decision for an idea."""
        from uuid import uuid4

        decision_id = uuid4().hex[:12]
        decided_at = datetime.now(UTC).isoformat()
        await self.db.execute(
            """INSERT INTO route_decisions
            (id, idea_id, action, target_repo, reason, confidence, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, idea_id, action, target_repo, reason, confidence, decided_at),
        )
        await self.db.commit()

    async def get_route_decision(self, idea_id: str) -> dict | None:
        """Return the most recent routing decision for an idea, or None."""
        cursor = await self.db.execute(
            "SELECT * FROM route_decisions WHERE idea_id = ? ORDER BY decided_at DESC LIMIT 1",
            (idea_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "idea_id": row["idea_id"],
            "action": row["action"],
            "target_repo": row["target_repo"],
            "reason": row["reason"],
            "confidence": row["confidence"],
            "decided_at": row["decided_at"],
        }

    # === HELPERS ===

    @staticmethod
    def _row_to_idea(row) -> Idea:
        keys = list(row.keys()) if hasattr(row, "keys") else []
        return Idea(
            id=row["id"],
            name=row["name"],
            tagline=row["tagline"],
            description=row["description"],
            category=IdeaCategory(row["category"]),
            market_analysis=row["market_analysis"],
            feasibility_score=row["feasibility_score"],
            mvp_scope=row["mvp_scope"],
            tech_stack=json.loads(row["tech_stack"]),
            generated_at=datetime.fromisoformat(row["generated_at"]).replace(tzinfo=UTC)
            if "+" not in row["generated_at"]
            else datetime.fromisoformat(row["generated_at"]),
            status=row["status"],
            github_issue_url=row["github_issue_url"],
            project_repo_url=row["project_repo_url"],
            source_url=row["source_url"] if "source_url" in keys else None,
            generation_mode=(row["generation_mode"] if "generation_mode" in keys else None),
            fundability_score=(row["fundability_score"] if "fundability_score" in keys else None),
            auto_promoted_at=(
                datetime.fromisoformat(row["auto_promoted_at"]).replace(tzinfo=UTC)
                if "auto_promoted_at" in keys and row["auto_promoted_at"] and "+" not in row["auto_promoted_at"]
                else (
                    datetime.fromisoformat(row["auto_promoted_at"])
                    if "auto_promoted_at" in keys and row["auto_promoted_at"]
                    else None
                )
            ),
            ambition_score=(row["ambition_score"] if "ambition_score" in keys else None),
            artifact_type=(row["artifact_type"] if "artifact_type" in keys else None),
            snipe_score=(row["snipe_score"] if "snipe_score" in keys else None),
            target_incumbent=(row["target_incumbent"] if "target_incumbent" in keys else None),
            mission_id=(row["mission_id"] if "mission_id" in keys else None),
            cashflow_score=(row["cashflow_score"] if "cashflow_score" in keys else None),
        )

    # === RESOURCE CRUD ===

    async def save_resource(self, resource: Resource) -> Resource:
        await self.db.execute(
            """INSERT OR REPLACE INTO resources
            (id, domain, name, description, url, categories, idea_count, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resource.id,
                resource.domain,
                resource.name,
                resource.description,
                resource.url,
                json.dumps(resource.categories),
                resource.idea_count,
                resource.added_at.isoformat(),
            ),
        )
        await self.db.commit()
        return resource

    async def get_resource(self, resource_id: str) -> Resource | None:
        cursor = await self.db.execute("SELECT * FROM resources WHERE id = ?", (resource_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_resource(row)

    async def get_resource_by_domain(self, domain: str) -> Resource | None:
        cursor = await self.db.execute("SELECT * FROM resources WHERE domain = ?", (domain,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_resource(row)

    async def list_resources(self) -> list[Resource]:
        cursor = await self.db.execute("SELECT * FROM resources ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        return [self._row_to_resource(row) for row in rows]

    async def increment_resource_idea_count(self, domain: str) -> None:
        await self.db.execute(
            "UPDATE resources SET idea_count = idea_count + 1 WHERE domain = ?",
            (domain,),
        )
        await self.db.commit()

    @staticmethod
    def _row_to_resource(row) -> Resource:
        return Resource(
            id=row["id"],
            domain=row["domain"],
            name=row["name"],
            description=row["description"],
            url=row["url"],
            categories=json.loads(row["categories"]),
            idea_count=row["idea_count"],
            added_at=datetime.fromisoformat(row["added_at"]).replace(tzinfo=UTC)
            if "+" not in row["added_at"]
            else datetime.fromisoformat(row["added_at"]),
        )

    # === MISSION CRUD (v0.18, #84) ===

    async def save_mission(self, mission: Mission) -> Mission:
        async with self._write_lock:
            await self.db.execute(
                """INSERT OR REPLACE INTO missions
                (id, title, brief, urls, category, status, created_at, last_generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mission.id,
                    mission.title,
                    mission.brief,
                    json.dumps(mission.urls),
                    mission.category.value if mission.category else None,
                    mission.status,
                    mission.created_at.isoformat(),
                    mission.last_generated_at.isoformat() if mission.last_generated_at else None,
                ),
            )
            await self.db.commit()
        return mission

    async def get_mission(self, mission_id: str) -> Mission | None:
        cursor = await self.db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_mission(row)

    async def list_missions(self, status: str | None = None) -> list[Mission]:
        if status:
            cursor = await self.db.execute(
                "SELECT * FROM missions WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await self.db.execute("SELECT * FROM missions ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [self._row_to_mission(row) for row in rows]

    async def update_mission_status(self, mission_id: str, status: str) -> None:
        async with self._write_lock:
            await self.db.execute(
                "UPDATE missions SET status = ? WHERE id = ?",
                (status, mission_id),
            )
            await self.db.commit()

    async def touch_mission_generated(self, mission_id: str) -> None:
        """Advance the mission cadence watermark. Called on every generation
        attempt — saved OR dedup-rejected — so rejection streaks don't let
        the cadence hammer the LLM backend."""
        async with self._write_lock:
            await self.db.execute(
                "UPDATE missions SET last_generated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), mission_id),
            )
            await self.db.commit()

    async def pick_next_mission(self) -> Mission | None:
        """Round-robin picker for the mission cadence: never-generated
        missions first, then the one generated against longest ago."""
        cursor = await self.db.execute(
            "SELECT * FROM missions WHERE status = 'active' "
            "ORDER BY last_generated_at IS NOT NULL, last_generated_at ASC, created_at ASC "
            "LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_mission(row)

    async def count_ideas_by_mission(self) -> dict[str, int]:
        cursor = await self.db.execute(
            "SELECT mission_id, COUNT(*) AS n FROM ideas WHERE mission_id IS NOT NULL GROUP BY mission_id"
        )
        rows = await cursor.fetchall()
        return {row["mission_id"]: row["n"] for row in rows}

    async def list_mission_ideas(self, mission_id: str | None = None, limit: int = 60) -> list[Idea]:
        """Ideas linked to a mission (or all mission-linked ideas when
        mission_id is None), newest first."""
        if mission_id:
            cursor = await self.db.execute(
                "SELECT * FROM ideas WHERE mission_id = ? ORDER BY generated_at DESC LIMIT ?",
                (mission_id, limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM ideas WHERE mission_id IS NOT NULL ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_idea(row) for row in rows]

    @staticmethod
    def _row_to_mission(row) -> Mission:
        def _ts(value: str | None) -> datetime | None:
            if not value:
                return None
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

        return Mission(
            id=row["id"],
            title=row["title"],
            brief=row["brief"],
            urls=json.loads(row["urls"] or "[]"),
            category=IdeaCategory(row["category"]) if row["category"] else None,
            status=row["status"],
            created_at=_ts(row["created_at"]),
            last_generated_at=_ts(row["last_generated_at"]),
        )
