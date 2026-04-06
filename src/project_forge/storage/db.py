"""SQLite storage for ideas, projects, and generation runs.

Hardened with WAL mode, busy_timeout, content fingerprinting,
and input-tuple tracking for deduplication at scale.
"""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from project_forge.models import (
    Challenge,
    FilteredIdea,
    GenerationRun,
    Idea,
    IdeaCategory,
    IdeaDenial,
    IdeaStatus,
    Resource,
    SelectionRound,
)

logger = logging.getLogger(__name__)

# Query profiling threshold in seconds
_SLOW_QUERY_THRESHOLD = 0.1  # 100ms

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
    created_at TEXT NOT NULL
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
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self.query_times: list[float] = []  # recent query durations in seconds

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # Hardening: WAL mode + busy_timeout for concurrent safety
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.executescript(SCHEMA)
        # Migration: add content_hash column if missing (safe for existing DBs)
        try:
            await self._db.execute("ALTER TABLE ideas ADD COLUMN content_hash TEXT")
        except Exception:  # noqa: S110
            pass  # Column already exists on migrated DBs
        # Migration: add source_url column if missing
        try:
            await self._db.execute("ALTER TABLE ideas ADD COLUMN source_url TEXT")
        except Exception:  # noqa: S110
            pass  # Column already exists on migrated DBs
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
        await self.db.execute(
            """INSERT OR REPLACE INTO ideas
            (id, name, tagline, description, category, market_analysis,
             feasibility_score, mvp_scope, tech_stack, generated_at, status,
             github_issue_url, project_repo_url, content_hash, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        await self.db.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))
        await self.db.commit()
        return await self.get_idea(idea_id)

    async def update_idea_urls(
        self, idea_id: str, github_issue_url: str | None = None, project_repo_url: str | None = None
    ) -> Idea | None:
        if github_issue_url is not None:
            await self.db.execute("UPDATE ideas SET github_issue_url = ? WHERE id = ?", (github_issue_url, idea_id))
        if project_repo_url is not None:
            await self.db.execute("UPDATE ideas SET project_repo_url = ? WHERE id = ?", (project_repo_url, idea_id))
        await self.db.commit()
        return await self.get_idea(idea_id)

    # === COUNTING & SEARCH (SQL-optimized, no Python-side filtering) ===

    async def count_ideas(self, status: IdeaStatus | None = None) -> int:
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

    async def list_super_ideas(self, limit: int = 6) -> list[Idea]:
        """List super ideas (name starts with [SUPER]) by score descending, deduped by name.

        Uses a subquery to deterministically select the highest-scoring row per
        unique name, avoiding SQLite's undefined behaviour when GROUP BY is used
        without an aggregate on non-grouped columns.
        """
        cursor = await self.db.execute(
            """
            SELECT i.*
            FROM ideas i
            INNER JOIN (
                SELECT name, MAX(feasibility_score) AS max_score
                FROM ideas
                WHERE name LIKE '[SUPER]%'
                GROUP BY name
            ) best ON i.name = best.name AND i.feasibility_score = best.max_score
            ORDER BY i.feasibility_score DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_idea(row) for row in rows]

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

        ideas_by_category = await self.count_ideas_by_category()

        cursor = await self.db.execute("SELECT COUNT(*) FROM generation_runs")
        row = await cursor.fetchone()
        total_runs = row[0] if row else 0

        cursor = await self.db.execute("SELECT AVG(feasibility_score) FROM ideas")
        row = await cursor.fetchone()
        avg_score = round(row[0], 2) if row and row[0] else 0.0

        cursor = await self.db.execute("SELECT COUNT(*) FROM ideas WHERE name LIKE '[SUPER]%'")
        row = await cursor.fetchone()
        super_count = row[0] if row else 0

        cursor = await self.db.execute("SELECT COUNT(*) FROM challenges")
        row = await cursor.fetchone()
        challenge_count = row[0] if row else 0

        cursor = await self.db.execute("SELECT COUNT(*) FROM selection_rounds")
        row = await cursor.fetchone()
        total_rounds = row[0] if row else 0

        cursor = await self.db.execute("SELECT COUNT(*) FROM idea_denials")
        row = await cursor.fetchone()
        total_denials = row[0] if row else 0

        return {
            "total_ideas": sum(ideas_by_status.values()),
            "ideas_by_status": ideas_by_status,
            "ideas_by_category": ideas_by_category,
            "total_runs": total_runs,
            "avg_feasibility_score": avg_score,
            "super_ideas": super_count,
            "total_challenges": challenge_count,
            "total_rounds": total_rounds,
            "total_denials": total_denials,
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

    async def list_challenges(self, idea_id: str) -> list[Challenge]:
        cursor = await self.db.execute(
            "SELECT * FROM challenges WHERE idea_id = ? ORDER BY created_at ASC",
            (idea_id,),
        )
        rows = await cursor.fetchall()
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
                created_at=datetime.fromisoformat(row["created_at"]).replace(tzinfo=UTC)
                if "+" not in row["created_at"]
                else datetime.fromisoformat(row["created_at"]),
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
            "WHERE name LIKE '[SUPER]%' AND status NOT IN ('rejected', 'archived')",
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
