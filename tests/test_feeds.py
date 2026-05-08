"""TDD: External feeds (NVD, arXiv, IETF).

Phase 5 (issue #58). Direction B: pull external signals as fresh seeds
for generation. Each feed has fetch + cache + health check.

Tests stub HTTP and verify:
- Cache write/read/expire behavior
- Health check reports stale/empty cache correctly
- Feed items have a stable shape (dict with id, title, summary, url, ts)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from project_forge.feeds.cache import FeedCache
from project_forge.feeds.health import FeedHealth

# ── FeedCache ────────────────────────────────────────────────────────


class TestFeedCache:
    def test_write_then_read_returns_data(self, tmp_path):
        cache = FeedCache(tmp_path / "feed.json", ttl=timedelta(hours=1))
        items = [{"id": "1", "title": "x", "summary": "y", "url": "z",
                  "ts": "2026-05-08T00:00:00+00:00"}]

        cache.write(items)
        loaded = cache.read()

        assert loaded == items

    def test_read_returns_none_when_missing(self, tmp_path):
        cache = FeedCache(tmp_path / "missing.json", ttl=timedelta(hours=1))
        assert cache.read() is None

    def test_read_returns_none_when_expired(self, tmp_path):
        path = tmp_path / "expired.json"
        # Write directly with old timestamp
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps({"fetched_at": old, "items": [{"id": "1"}]}))

        cache = FeedCache(path, ttl=timedelta(hours=1))
        assert cache.read() is None

    def test_age_returns_timedelta(self, tmp_path):
        cache = FeedCache(tmp_path / "age.json", ttl=timedelta(hours=1))
        cache.write([{"id": "1"}])

        age = cache.age()
        assert age is not None
        assert age < timedelta(seconds=10)

    def test_age_none_when_missing(self, tmp_path):
        cache = FeedCache(tmp_path / "missing.json", ttl=timedelta(hours=1))
        assert cache.age() is None


# ── FeedHealth ────────────────────────────────────────────────────────


class TestFeedHealth:
    def test_healthy_when_cache_recent(self, tmp_path):
        cache = FeedCache(tmp_path / "h.json", ttl=timedelta(hours=1))
        cache.write([{"id": "1"}, {"id": "2"}])

        health = FeedHealth.from_cache(cache)
        assert health.ok
        assert health.count == 2
        assert health.age is not None

    def test_unhealthy_when_cache_missing(self, tmp_path):
        cache = FeedCache(tmp_path / "missing.json", ttl=timedelta(hours=1))
        health = FeedHealth.from_cache(cache)
        assert not health.ok
        assert health.count == 0

    def test_unhealthy_when_cache_expired(self, tmp_path):
        path = tmp_path / "stale.json"
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps({"fetched_at": old, "items": [{"id": "1"}]}))
        cache = FeedCache(path, ttl=timedelta(hours=1))

        health = FeedHealth.from_cache(cache)
        assert not health.ok


# ── NVD feed parsing ──────────────────────────────────────────────────


class TestNvdFeedParsing:
    def test_parse_nvd_response_extracts_items(self):
        from project_forge.feeds.nvd import parse_nvd_response

        # Minimal NVD CVE 2.0 JSON response
        sample = {
            "vulnerabilities": [
                {
                    "cve": {
                        "id": "CVE-2026-12345",
                        "descriptions": [
                            {"lang": "en", "value": "Buffer overflow in xyz"},
                        ],
                        "published": "2026-05-01T00:00:00.000",
                    },
                },
            ],
        }
        items = parse_nvd_response(sample)
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "CVE-2026-12345"
        assert "buffer overflow" in item["summary"].lower()
        assert "url" in item
        assert "ts" in item

    def test_parse_nvd_response_empty_returns_empty_list(self):
        from project_forge.feeds.nvd import parse_nvd_response

        assert parse_nvd_response({}) == []
        assert parse_nvd_response({"vulnerabilities": []}) == []


# ── arXiv feed parsing ────────────────────────────────────────────────


class TestArxivFeedParsing:
    def test_parse_arxiv_atom_extracts_items(self):
        from project_forge.feeds.arxiv import parse_arxiv_atom

        sample = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2605.01234v1</id>
    <title>Post-Quantum Migration Strategies</title>
    <summary>This paper discusses PQC migration in TLS.</summary>
    <published>2026-05-01T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2605.01234v1"/>
  </entry>
</feed>"""
        items = parse_arxiv_atom(sample)
        assert len(items) == 1
        item = items[0]
        assert "post-quantum" in item["title"].lower()
        assert "tls" in item["summary"].lower()
        assert item["url"].startswith("http://arxiv.org/abs/")

    def test_parse_arxiv_empty_returns_empty(self):
        from project_forge.feeds.arxiv import parse_arxiv_atom

        items = parse_arxiv_atom("<feed></feed>")
        assert items == []


# ── format_for_prompt: shared helper ─────────────────────────────────


class TestFormatForPrompt:
    def test_formats_items_as_seed_lines(self):
        from project_forge.feeds import format_for_prompt

        items = [
            {"id": "CVE-2026-1", "title": "X", "summary": "buffer overflow", "url": "u", "ts": "2026-05-08"},
            {"id": "arxiv:1234", "title": "Y", "summary": "pqc tls", "url": "v", "ts": "2026-05-07"},
        ]
        text = format_for_prompt(items, max_items=2)

        assert "CVE-2026-1" in text
        assert "buffer overflow" in text
        assert "arxiv:1234" in text or "Y" in text

    def test_respects_max_items(self):
        from project_forge.feeds import format_for_prompt

        items = [{"id": str(i), "title": "x", "summary": "y", "url": "z", "ts": "0"} for i in range(10)]
        text = format_for_prompt(items, max_items=3)

        # Each item line contains the id; only 3 ids should appear
        appearances = sum(1 for i in range(10) if f" {i} " in f" {text} " or f"\n{i}" in text)
        assert appearances <= 3

    def test_empty_returns_empty_string(self):
        from project_forge.feeds import format_for_prompt

        assert format_for_prompt([]) == ""


# ── pytest fixtures ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Prevent any test from accidentally hitting the network."""
    import urllib.request

    def _block(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("Network access blocked in tests")

    monkeypatch.setattr(urllib.request, "urlopen", _block)
