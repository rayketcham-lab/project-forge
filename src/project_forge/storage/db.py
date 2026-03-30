"""SQLite storage for ideas, projects, and generation runs."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from project_forge.models import GenerationRun, Idea, IdeaCategory, IdeaStatus

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
    project_repo_url TEXT
);

CREATE TABLE IF NOT EXISTS generation_runs (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    idea_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT
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
        await self._db.executescript(SCHEMA)
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

    async def save_idea(self, idea: Idea) -> Idea:
        await self.db.execute(
            """INSERT OR REPLACE INTO ideas
            (id, name, tagline, description, category, market_analysis,
             feasibility_score, mvp_scope, tech_stack, generated_at, status,
             github_issue_url, project_repo_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    async def count_ideas(self, status: IdeaStatus | None = None) -> int:
        if status:
            cursor = await self.db.execute("SELECT COUNT(*) FROM ideas WHERE status = ?", (status,))
        else:
            cursor = await self.db.execute("SELECT COUNT(*) FROM ideas")
        row = await cursor.fetchone()
        return row[0]

    async def get_recent_categories(self, limit: int = 3) -> list[str]:
        cursor = await self.db.execute("SELECT category FROM ideas ORDER BY generated_at DESC LIMIT ?", (limit,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

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

    async def get_stats(self) -> dict:
        ideas_by_status = {}
        cursor = await self.db.execute("SELECT status, COUNT(*) FROM ideas GROUP BY status")
        for row in await cursor.fetchall():
            ideas_by_status[row[0]] = row[1]

        ideas_by_category = {}
        cursor = await self.db.execute("SELECT category, COUNT(*) FROM ideas GROUP BY category")
        for row in await cursor.fetchall():
            ideas_by_category[row[0]] = row[1]

        total_runs = 0
        cursor = await self.db.execute("SELECT COUNT(*) FROM generation_runs")
        row = await cursor.fetchone()
        if row:
            total_runs = row[0]

        avg_score = 0.0
        cursor = await self.db.execute("SELECT AVG(feasibility_score) FROM ideas")
        row = await cursor.fetchone()
        if row and row[0]:
            avg_score = round(row[0], 2)

        return {
            "total_ideas": sum(ideas_by_status.values()),
            "ideas_by_status": ideas_by_status,
            "ideas_by_category": ideas_by_category,
            "total_runs": total_runs,
            "avg_feasibility_score": avg_score,
        }

    @staticmethod
    def _row_to_idea(row) -> Idea:
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
        )
