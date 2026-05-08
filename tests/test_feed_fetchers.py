"""TDD: HTTP fetchers for NVD, arXiv, IETF feeds.

Phase 5 shipped parsers + cache + health. This wires the fetchers that
actually pull from the network — with mocked HTTP so tests stay offline.

Each feed exports:
- fetch(...) → list[dict]: network call + cache write, returns parsed items
- load(...) → list[dict] | None: cache read, returns None when stale/missing
- health(...) → FeedHealth: status snapshot

Plus a top-level helper get_external_seeds(...) that aggregates all feeds.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from project_forge.feeds.cache import FeedCache


@pytest.fixture
def tmp_cache(tmp_path):
    return tmp_path / "feed.json"


# ── NVD fetcher ───────────────────────────────────────────────────────


class TestNvdFetcher:
    def test_fetch_writes_cache_and_returns_items(self, monkeypatch, tmp_cache):
        from project_forge.feeds import nvd

        captured = {}
        sample = {
            "vulnerabilities": [
                {"cve": {
                    "id": "CVE-2026-9000",
                    "descriptions": [{"lang": "en", "value": "RCE in libfoo"}],
                    "published": "2026-05-08T00:00:00",
                }},
            ],
        }

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            captured["url"] = url
            captured["timeout"] = timeout
            return json.dumps(sample).encode("utf-8")

        monkeypatch.setattr(nvd, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=6))

        items = nvd.fetch(cache=cache, days=7)

        assert len(items) == 1
        assert items[0]["id"] == "CVE-2026-9000"
        # Must hit the NVD 2.0 API
        assert "services.nvd.nist.gov" in captured["url"]
        # Must have written cache
        assert cache.read() == items

    def test_fetch_returns_empty_on_http_failure(self, monkeypatch, tmp_cache):
        from project_forge.feeds import nvd

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            raise OSError("connection refused")

        monkeypatch.setattr(nvd, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=6))

        items = nvd.fetch(cache=cache, days=7)
        assert items == []
        # Cache is NOT written on failure
        assert cache.read() is None

    def test_load_returns_cached_when_fresh(self, monkeypatch, tmp_cache):
        from project_forge.feeds import nvd

        cache = FeedCache(tmp_cache, ttl=timedelta(hours=6))
        cache.write([{"id": "CVE-2026-1", "title": "x", "summary": "y", "url": "u", "ts": "t"}])

        # If load is called, it must NOT hit network
        def boom(*args, **kwargs):  # noqa: ARG001
            raise AssertionError("network call should not happen")

        monkeypatch.setattr(nvd, "_http_get_bytes", boom)

        items = nvd.load(cache)
        assert len(items) == 1


# ── arXiv fetcher ─────────────────────────────────────────────────────


class TestArxivFetcher:
    def test_fetch_writes_cache_and_returns_items(self, monkeypatch, tmp_cache):
        from project_forge.feeds import arxiv

        sample_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry>"
            "<id>http://arxiv.org/abs/2605.99999v1</id>"
            "<title>Detecting Side Channels</title>"
            "<summary>Constant-time ops in TLS.</summary>"
            "<published>2026-05-01T00:00:00Z</published>"
            '<link href="http://arxiv.org/abs/2605.99999v1"/>'
            "</entry>"
            "</feed>"
        )

        captured = {}

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            captured["url"] = url
            return sample_xml.encode("utf-8")

        monkeypatch.setattr(arxiv, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=24))

        items = arxiv.fetch(cache=cache, category="cs.CR", max_results=10)

        assert len(items) == 1
        assert "side channels" in items[0]["title"].lower()
        # Must hit arXiv API
        assert "export.arxiv.org" in captured["url"]

    def test_fetch_returns_empty_on_http_failure(self, monkeypatch, tmp_cache):
        from project_forge.feeds import arxiv

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            raise OSError("dns failure")

        monkeypatch.setattr(arxiv, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=24))

        items = arxiv.fetch(cache=cache, category="cs.CR")
        assert items == []


# ── IETF I-D fetcher (new) ───────────────────────────────────────────


class TestIetfFetcher:
    def test_fetch_extracts_drafts(self, monkeypatch, tmp_cache):
        from project_forge.feeds import ietf

        sample_xml = (
            '<?xml version="1.0"?>'
            "<rss><channel>"
            "<item>"
            "<title>draft-foo-bar-quic-extension-00</title>"
            "<description>A new QUIC extension for X.</description>"
            "<link>https://datatracker.ietf.org/doc/draft-foo-bar-quic-extension/</link>"
            "<pubDate>Fri, 02 May 2026 12:00:00 GMT</pubDate>"
            "</item>"
            "</channel></rss>"
        )

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            return sample_xml.encode("utf-8")

        monkeypatch.setattr(ietf, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=6))

        items = ietf.fetch(cache=cache)

        assert len(items) == 1
        assert "quic" in items[0]["title"].lower()
        assert items[0]["url"].startswith("https://datatracker.ietf.org/")

    def test_fetch_empty_rss_returns_empty_list(self, monkeypatch, tmp_cache):
        from project_forge.feeds import ietf

        def stub_http_get(url, *, timeout):  # noqa: ARG001
            return b"<rss><channel></channel></rss>"

        monkeypatch.setattr(ietf, "_http_get_bytes", stub_http_get)
        cache = FeedCache(tmp_cache, ttl=timedelta(hours=6))

        assert ietf.fetch(cache=cache) == []


# ── get_external_seeds aggregator ────────────────────────────────────


class TestExternalSeedAggregator:
    def test_aggregates_from_all_healthy_feeds(self, tmp_path):
        from project_forge.feeds import get_external_seeds

        # Pre-populate caches as if fetches already ran
        nvd_cache = FeedCache(tmp_path / "nvd.json", ttl=timedelta(hours=6))
        arxiv_cache = FeedCache(tmp_path / "arxiv.json", ttl=timedelta(hours=24))
        ietf_cache = FeedCache(tmp_path / "ietf.json", ttl=timedelta(hours=6))

        nvd_cache.write([{"id": "CVE-1", "title": "X", "summary": "y", "url": "u", "ts": "t"}])
        arxiv_cache.write([{"id": "arxiv:1", "title": "Y", "summary": "z", "url": "u", "ts": "t"}])
        ietf_cache.write([{"id": "draft-1", "title": "Z", "summary": "w", "url": "u", "ts": "t"}])

        items = get_external_seeds(
            nvd_cache=nvd_cache,
            arxiv_cache=arxiv_cache,
            ietf_cache=ietf_cache,
            max_per_feed=5,
        )

        # Aggregated, max_per_feed honored
        assert len(items) == 3
        ids = {i["id"] for i in items}
        assert {"CVE-1", "arxiv:1", "draft-1"}.issubset(ids)

    def test_skips_unhealthy_feeds(self, tmp_path):
        from project_forge.feeds import get_external_seeds

        # nvd has no cache → unhealthy; arxiv has fresh data → healthy
        nvd_cache = FeedCache(tmp_path / "nvd.json", ttl=timedelta(hours=6))
        arxiv_cache = FeedCache(tmp_path / "arxiv.json", ttl=timedelta(hours=24))
        arxiv_cache.write([{"id": "arxiv:1", "title": "Y", "summary": "z", "url": "u", "ts": "t"}])

        items = get_external_seeds(
            nvd_cache=nvd_cache,
            arxiv_cache=arxiv_cache,
            ietf_cache=None,
        )

        ids = {i["id"] for i in items}
        assert "arxiv:1" in ids
        assert not any(i["id"].startswith("CVE-") for i in items)
