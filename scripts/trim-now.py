#!/usr/bin/env python3
"""One-shot aggressive trim using the extended siphon.

Runs three passes:
  1. Tightened atomic siphon (tagline 0.45, name 0.55)
  2. Super-idea component-overlap dedup (≥3 shared atoms OR name Jaccard ≥ 0.65)
  3. Vertical-cap collapse (keep top-2 per "X for {vertical}" concept)

Default is dry-run. Pass --apply to mutate the database.

Backup is assumed (data/backups/). To restore the atomic-siphon archives
only, run `python -m project_forge.engine.siphon restore`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from project_forge.config import settings  # noqa: E402
from project_forge.engine.siphon import siphon_all  # noqa: E402
from project_forge.storage.db import Database  # noqa: E402


def _fmt_section(label: str, sub: dict) -> str:
    return (
        f"  {label:<10}  clusters={sub.get('cluster_count', 0):>4}  "
        f"planned_archive={sub.get('archived_count', 0):>4}  "
        f"applied={sub.get('applied_count', 0):>4}"
    )


async def main(apply: bool) -> int:
    db = Database(settings.db_path)
    await db.connect()
    try:
        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE status='new'"
        )
        before_new = (await cur.fetchone())[0]
        cur = await db.db.execute("SELECT COUNT(*) FROM ideas")
        before_total = (await cur.fetchone())[0]

        print(f"Before:  total={before_total}  active 'new'={before_new}")
        print(f"Mode:    {'APPLY' if apply else 'dry-run'}")
        report = await siphon_all(db, dry_run=not apply)

        print()
        print("Per-siphon results:")
        print(_fmt_section("atomic", report["atomic"]))
        print(_fmt_section("supers", report["supers"]))
        print(_fmt_section("verticals", report["verticals"]))
        print()
        print(f"TOTAL planned archive: {report['total_archived']}")

        if apply:
            cur = await db.db.execute(
                "SELECT COUNT(*) FROM ideas WHERE status='new'"
            )
            after_new = (await cur.fetchone())[0]
            print(f"After:   active 'new'={after_new} "
                  f"(delta {after_new - before_new:+d})")
        else:
            print("(re-run with --apply to mutate the database)")
    finally:
        await db.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Mutate the DB (otherwise dry-run).")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
