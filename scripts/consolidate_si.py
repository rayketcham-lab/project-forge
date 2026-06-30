"""One-shot: consolidate the Think Tank self-improvement list.

Archives floaty [SUPER] self-improvement ideas and dedupes near-identical
base proposals. Safe to re-run (idempotent). Run: python -m scripts.consolidate_si
(or python scripts/consolidate_si.py from the repo root).
"""

import asyncio

from project_forge.config import settings
from project_forge.engine.si_consolidation import consolidate_self_improvement
from project_forge.storage.db import Database


async def main() -> None:
    db = Database(settings.db_path)
    await db.connect()
    try:
        report = await consolidate_self_improvement(db)
        print(
            f"Consolidated self-improvement ideas: archived "
            f"{report['archived_super']} [SUPER] junk + "
            f"{report['archived_garbled']} garbled-crossover + "
            f"{report['archived_dupes']} duplicates; {report['kept']} kept active."
        )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
