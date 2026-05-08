"""FeedHealth dataclass + factory for cache status reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache


@dataclass
class FeedHealth:
    ok: bool
    age: timedelta | None
    count: int

    @classmethod
    def from_cache(cls, cache: FeedCache) -> FeedHealth:
        items = cache.read()
        if items is None:
            return cls(ok=False, age=cache.age(), count=0)
        return cls(ok=True, age=cache.age(), count=len(items))
