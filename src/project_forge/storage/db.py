"""SQLite storage for ideas, projects, and generation runs.

Hardened with WAL mode, busy_timeout, content fingerprinting,
and input-tuple tracking for deduplication at scale.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from project_forge.models import GenerationRun, Idea, IdeaCategory, IdeaStatus, Resource

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
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

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

    # === IDEA CRUD ===

    async def save_idea(self, idea: Idea) -> Idea:
        content_hash = getattr(idea, "content_hash", None)
        if content_hash:
            # Check if content_hash already exists -- skip if duplicate
            cursor = await self.db.execute("SELECT id FROM ideas WHERE content_hash = ?", (content_hash,))
            existing = await cursor.fetchone()
            if existing:
                return idea  # Silently skip duplicate content

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
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
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

    async def get_least_explored_pairs(self, limit: int = 66) -> list[tuple[str, str, int]]:
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
        """List super ideas (name starts with [SUPER]) by score descending, deduped by name."""
        cursor = await self.db.execute(
            "SELECT * FROM ideas WHERE name LIKE '[SUPER]%' "
            "GROUP BY name ORDER BY feasibility_score DESC LIMIT ?",
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

        return {
            "total_ideas": sum(ideas_by_status.values()),
            "ideas_by_status": ideas_by_status,
            "ideas_by_category": ideas_by_category,
            "total_runs": total_runs,
            "avg_feasibility_score": avg_score,
            "super_ideas": super_count,
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
