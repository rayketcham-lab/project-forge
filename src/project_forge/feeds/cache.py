"""File-backed JSON cache with TTL for external feeds."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class FeedCache:
    """Persist a list of feed items with a fetched_at timestamp."""

    def __init__(self, path: Path, ttl: timedelta) -> None:
        self.path = path
        self.ttl = ttl

    def write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "items": items,
        }
        self.path.write_text(json.dumps(payload))

    def read(self) -> list[dict] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read feed cache %s: %s", self.path, exc)
            return None

        fetched = self._parse_ts(payload.get("fetched_at"))
        if fetched is None:
            return None
        if datetime.now(UTC) - fetched > self.ttl:
            return None
        return payload.get("items") or []

    def age(self) -> timedelta | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        fetched = self._parse_ts(payload.get("fetched_at"))
        if fetched is None:
            return None
        return datetime.now(UTC) - fetched

    @staticmethod
    def _parse_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
