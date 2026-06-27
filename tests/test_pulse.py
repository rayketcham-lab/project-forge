"""Tests for the Pulse feed — event-driven world-signal watcher.

Covers:
  - parse_hn_front: sorts by score desc, normalises shape, falls back URL
  - parse_github_trending: sorts by score desc, normalises shape
  - fetch_pulse_signals: merges both sources, sorted desc, degrades to [] on OSError
  - pick_hot_signal: returns max-score signal, None for empty list
  - signal_to_seed: non-empty string referencing source, title, url, score

No network calls — http_get is always a monkeypatched stub or raises OSError.
"""

from __future__ import annotations

import json

# ── representative sample payloads ──────────────────────────────────────────

_HN_PAYLOAD: dict = {
    "hits": [
        {
            "objectID": "42000000",
            "title": "Show HN: I built a zero-config TLS terminator",
            "url": "https://example.com/tls",
            "points": 412,
            "num_comments": 91,
            "created_at": "2026-06-27T10:00:00Z",
        },
        {
            "objectID": "42000001",
            "title": "Ask HN: What are you building this week?",
            "url": None,  # deliberately absent — fallback URL must be used
            "points": 83,
            "num_comments": 204,
            "created_at": "2026-06-27T09:00:00Z",
        },
        {
            "objectID": "42000002",
            "title": "Why Postgres is the only database you need",
            "url": "https://example.com/pg",
            "points": 231,
            "num_comments": 55,
            "created_at": "2026-06-27T08:00:00Z",
        },
    ]
}

_GH_PAYLOAD: dict = {
    "items": [
        {
            "full_name": "anthropics/open-interpreter",
            "html_url": "https://github.com/anthropics/open-interpreter",
            "stargazers_count": 4820,
            "description": "A natural language interface for computers",
            "created_at": "2026-06-20T12:00:00Z",
        },
        {
            "full_name": "coolorg/fastrouter",
            "html_url": "https://github.com/coolorg/fastrouter",
            "stargazers_count": 318,
            "description": "Ultra-fast HTTP router",
            "created_at": "2026-06-21T08:00:00Z",
        },
    ]
}


# ── parse_hn_front ────────────────────────────────────────────────────────────


class TestParseHnFront:
    def test_sorts_by_score_desc(self):
        from project_forge.feeds.pulse import parse_hn_front

        signals = parse_hn_front(_HN_PAYLOAD)
        scores = [s["score"] for s in signals]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 412

    def test_signal_has_required_keys(self):
        from project_forge.feeds.pulse import parse_hn_front

        signals = parse_hn_front(_HN_PAYLOAD)
        for s in signals:
            for key in ("source", "title", "url", "score", "ts"):
                assert key in s, f"missing key {key!r} in signal {s!r}"

    def test_source_is_hn(self):
        from project_forge.feeds.pulse import parse_hn_front

        signals = parse_hn_front(_HN_PAYLOAD)
        assert all(s["source"] == "hn" for s in signals)

    def test_fallback_url_when_none(self):
        from project_forge.feeds.pulse import parse_hn_front

        signals = parse_hn_front(_HN_PAYLOAD)
        ask_hn = next(s for s in signals if "Ask HN" in s["title"])
        assert "news.ycombinator.com" in ask_hn["url"]

    def test_empty_payload_returns_empty(self):
        from project_forge.feeds.pulse import parse_hn_front

        assert parse_hn_front({}) == []
        assert parse_hn_front({"hits": []}) == []

    def test_max_items_respected(self):
        from project_forge.feeds.pulse import parse_hn_front

        signals = parse_hn_front(_HN_PAYLOAD, max_items=2)
        assert len(signals) <= 2

    def test_title_stripped(self):
        from project_forge.feeds.pulse import parse_hn_front

        payload = {"hits": [{"objectID": "1", "title": "  Trimmed  ", "points": 50, "created_at": ""}]}
        signals = parse_hn_front(payload)
        assert signals[0]["title"] == "Trimmed"

    def test_hit_with_no_title_skipped(self):
        from project_forge.feeds.pulse import parse_hn_front

        payload = {
            "hits": [
                {"objectID": "1", "title": "", "points": 999, "created_at": ""},
                {"objectID": "2", "title": "Real Title", "points": 5, "created_at": ""},
            ]
        }
        signals = parse_hn_front(payload)
        assert len(signals) == 1
        assert signals[0]["title"] == "Real Title"


# ── parse_github_trending ─────────────────────────────────────────────────────


class TestParseGithubTrending:
    def test_sorts_by_score_desc(self):
        from project_forge.feeds.pulse import parse_github_trending

        signals = parse_github_trending(_GH_PAYLOAD)
        scores = [s["score"] for s in signals]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 4820

    def test_signal_has_required_keys(self):
        from project_forge.feeds.pulse import parse_github_trending

        signals = parse_github_trending(_GH_PAYLOAD)
        for s in signals:
            for key in ("source", "title", "url", "score", "ts"):
                assert key in s, f"missing key {key!r} in signal {s!r}"

    def test_source_is_github(self):
        from project_forge.feeds.pulse import parse_github_trending

        signals = parse_github_trending(_GH_PAYLOAD)
        assert all(s["source"] == "github" for s in signals)

    def test_empty_payload_returns_empty(self):
        from project_forge.feeds.pulse import parse_github_trending

        assert parse_github_trending({}) == []
        assert parse_github_trending({"items": []}) == []

    def test_max_items_respected(self):
        from project_forge.feeds.pulse import parse_github_trending

        signals = parse_github_trending(_GH_PAYLOAD, max_items=1)
        assert len(signals) <= 1

    def test_repo_with_no_full_name_skipped(self):
        from project_forge.feeds.pulse import parse_github_trending

        payload = {
            "items": [
                {"full_name": "", "html_url": "https://github.com/x/y", "stargazers_count": 9000, "created_at": ""},
                {
                    "full_name": "good/repo",
                    "html_url": "https://github.com/good/repo",
                    "stargazers_count": 5,
                    "created_at": "",
                },
            ]
        }
        signals = parse_github_trending(payload)
        assert len(signals) == 1
        assert signals[0]["title"] == "good/repo"

    def test_fallback_url_when_html_url_absent(self):
        from project_forge.feeds.pulse import parse_github_trending

        payload = {
            "items": [
                {"full_name": "myorg/myrepo", "stargazers_count": 100, "created_at": ""},
            ]
        }
        signals = parse_github_trending(payload)
        assert "github.com/myorg/myrepo" in signals[0]["url"]


# ── fetch_pulse_signals ───────────────────────────────────────────────────────


class TestFetchPulseSignals:
    def _http_stub(self):
        def _get(url: str, *, timeout: float = 12.0) -> bytes:
            if "hn.algolia.com" in url:
                return json.dumps(_HN_PAYLOAD).encode()
            if "api.github.com" in url:
                return json.dumps(_GH_PAYLOAD).encode()
            raise AssertionError(f"unexpected url: {url}")

        return _get

    def test_merges_both_sources(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        signals = fetch_pulse_signals(http_get=self._http_stub())
        sources = {s["source"] for s in signals}
        assert "hn" in sources
        assert "github" in sources

    def test_sorted_by_score_desc(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        signals = fetch_pulse_signals(http_get=self._http_stub())
        scores = [s["score"] for s in signals]
        assert scores == sorted(scores, reverse=True)

    def test_degrades_to_empty_on_total_network_failure(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        def _boom(url: str, *, timeout: float = 12.0) -> bytes:
            raise OSError("network down")

        signals = fetch_pulse_signals(http_get=_boom)
        assert signals == []

    def test_degrades_gracefully_when_hn_fails(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        def _partial(url: str, *, timeout: float = 12.0) -> bytes:
            if "hn.algolia.com" in url:
                raise OSError("hn down")
            return json.dumps(_GH_PAYLOAD).encode()

        signals = fetch_pulse_signals(http_get=_partial)
        assert len(signals) > 0
        assert all(s["source"] == "github" for s in signals)

    def test_degrades_gracefully_when_github_fails(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        def _partial(url: str, *, timeout: float = 12.0) -> bytes:
            if "api.github.com" in url:
                raise OSError("github down")
            return json.dumps(_HN_PAYLOAD).encode()

        signals = fetch_pulse_signals(http_get=_partial)
        assert len(signals) > 0
        assert all(s["source"] == "hn" for s in signals)

    def test_each_signal_has_required_keys(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        signals = fetch_pulse_signals(http_get=self._http_stub())
        for s in signals:
            for key in ("source", "title", "url", "score", "ts"):
                assert key in s

    def test_degrades_on_json_decode_error(self):
        from project_forge.feeds.pulse import fetch_pulse_signals

        def _bad_json(url: str, *, timeout: float = 12.0) -> bytes:
            return b"not valid json {"

        signals = fetch_pulse_signals(http_get=_bad_json)
        assert signals == []


# ── pick_hot_signal ───────────────────────────────────────────────────────────


class TestPickHotSignal:
    def test_returns_max_score(self):
        from project_forge.feeds.pulse import pick_hot_signal

        signals = [
            {"source": "hn", "title": "A", "url": "", "score": 100, "ts": ""},
            {"source": "github", "title": "B", "url": "", "score": 4820, "ts": ""},
            {"source": "hn", "title": "C", "url": "", "score": 412, "ts": ""},
        ]
        hot = pick_hot_signal(signals)
        assert hot is not None
        assert hot["score"] == 4820
        assert hot["title"] == "B"

    def test_none_for_empty_list(self):
        from project_forge.feeds.pulse import pick_hot_signal

        assert pick_hot_signal([]) is None

    def test_single_signal_is_returned(self):
        from project_forge.feeds.pulse import pick_hot_signal

        sig = {"source": "hn", "title": "Solo", "url": "https://x.com", "score": 55, "ts": ""}
        assert pick_hot_signal([sig]) is sig

    def test_ties_resolved_deterministically(self):
        from project_forge.feeds.pulse import pick_hot_signal

        signals = [
            {"source": "hn", "title": "First", "url": "", "score": 300, "ts": ""},
            {"source": "github", "title": "Second", "url": "", "score": 300, "ts": ""},
        ]
        hot = pick_hot_signal(signals)
        assert hot is not None
        assert hot["score"] == 300


# ── signal_to_seed ────────────────────────────────────────────────────────────


class TestSignalToSeed:
    def _hn_signal(self, **overrides) -> dict:
        base = {
            "source": "hn",
            "title": "zero-config TLS terminator",
            "url": "https://example.com/tls",
            "score": 412,
            "ts": "",
        }
        base.update(overrides)
        return base

    def _gh_signal(self, **overrides) -> dict:
        base = {
            "source": "github",
            "title": "coolorg/fastrouter",
            "url": "https://github.com/coolorg/fastrouter",
            "score": 318,
            "ts": "",
        }
        base.update(overrides)
        return base

    def test_seed_contains_title(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._hn_signal())
        assert "zero-config TLS terminator" in seed

    def test_seed_contains_url(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._hn_signal())
        assert "https://example.com/tls" in seed

    def test_hn_seed_mentions_hacker_news(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._hn_signal())
        assert "Hacker News" in seed

    def test_github_seed_mentions_github_trending(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._gh_signal())
        assert "GitHub" in seed

    def test_seed_contains_score(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._hn_signal(score=9999))
        assert "9999" in seed

    def test_seed_is_non_empty_string(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._hn_signal())
        assert isinstance(seed, str)
        assert len(seed) > 20

    def test_seed_survives_minimal_signal(self):
        from project_forge.feeds.pulse import signal_to_seed

        # All optional fields absent — must not raise.
        seed = signal_to_seed({})
        assert isinstance(seed, str)
        assert len(seed) > 0

    def test_seed_github_uses_github_label(self):
        from project_forge.feeds.pulse import signal_to_seed

        seed = signal_to_seed(self._gh_signal())
        assert "GitHub trending" in seed
