"""The grounding layer under the money board's cadence.

A strategy the engine invents from nothing cites nothing. This probe
sweeps the places where venue mechanics are actually announced — SDK
release notes and open issues on the clients traders really use — and
returns candidate PROGRAMS carrying a citable URL: a fee schedule change,
a new reward budget, a funding mechanic, an incentive campaign.

Same contract as the PKI probe: every network call degrades to an empty
list rather than raising, and finding nothing is a normal outcome that the
cadence records as a quiet hour rather than an error.
"""

from __future__ import annotations

import json

import pytest

from project_forge.feeds import venue_probe
from project_forge.models import MONEY_CATEGORIES, BotVenueFamily, IdeaCategory

_RELEASES = json.dumps(
    [
        {
            "html_url": "https://github.com/ccxt/ccxt/releases/tag/v4.5.73",
            "name": "v4.5.73",
            "body": "Updated maker rebate tiers and funding rate endpoint for several exchanges.",
            "published_at": "2026-08-01T00:00:00Z",
        },
        {
            "html_url": "https://github.com/ccxt/ccxt/releases/tag/v4.5.72",
            "name": "v4.5.72",
            "body": "Typo fixes in the README and a docstring cleanup.",
            "published_at": "2026-07-30T00:00:00Z",
        },
    ]
).encode()

_ISSUES = json.dumps(
    {
        "items": [
            {
                "html_url": "https://github.com/Polymarket/py-clob-client/issues/42",
                "title": "Liquidity rewards: qualifying spread not documented",
                "body": "The reward budget per market and the max spread band are unclear.",
                "updated_at": "2026-08-10T00:00:00Z",
            }
        ]
    }
).encode()


def _fake_http(payloads: dict[str, bytes]):
    """http_get double that serves canned bytes by URL substring."""

    def _get(url: str, timeout: float = 15.0) -> bytes:
        for needle, payload in payloads.items():
            if needle in url:
                return payload
        raise OSError(f"no canned payload for {url}")

    return _get


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_registry_is_populated(self):
        assert len(venue_probe.VENUE_REGISTRY) >= 8

    def test_every_venue_has_a_docs_url(self):
        for venue in venue_probe.VENUE_REGISTRY:
            assert venue.docs_url.startswith("https://"), venue.name

    def test_all_four_families_are_represented(self):
        families = {v.family for v in venue_probe.VENUE_REGISTRY}
        for fam in (
            BotVenueFamily.PREDICTION_MARKETS,
            BotVenueFamily.CRYPTO_DEFI,
            BotVenueFamily.SPORTSBOOK,
            BotVenueFamily.BROKERAGE,
        ):
            assert fam in families

    def test_venue_names_are_unique(self):
        names = [v.name for v in venue_probe.VENUE_REGISTRY]
        assert len(names) == len(set(names))

    def test_probe_repos_belong_to_registered_venues(self):
        """A source with no venue behind it produces uncitable candidates."""
        registered = {v.name for v in venue_probe.VENUE_REGISTRY}
        for _repo, venue_name in venue_probe.PROBE_REPOS:
            assert venue_name in registered


# --------------------------------------------------------------------------- #
# Fetch + degrade                                                             #
# --------------------------------------------------------------------------- #


class TestFetch:
    def test_returns_relevant_candidates(self):
        got = venue_probe.fetch_venue_programs(http_get=_fake_http({"/releases": _RELEASES, "search/issues": _ISSUES}))
        assert got
        urls = {c["url"] for c in got}
        assert "https://github.com/Polymarket/py-clob-client/issues/42" in urls

    def test_irrelevant_items_are_dropped(self):
        got = venue_probe.fetch_venue_programs(http_get=_fake_http({"/releases": _RELEASES, "search/issues": _ISSUES}))
        titles = " ".join(c["title"] for c in got)
        assert "Typo fixes" not in titles

    def test_candidates_carry_venue_family_and_category(self):
        got = venue_probe.fetch_venue_programs(http_get=_fake_http({"/releases": _RELEASES, "search/issues": _ISSUES}))
        for c in got:
            assert c["venue"]
            assert c["family"] in {f.value for f in BotVenueFamily}
            assert c["category"] in {c2.value for c2 in MONEY_CATEGORIES}
            assert c["url"].startswith("https://")

    def test_total_network_failure_degrades_to_empty(self):
        def _boom(url: str, timeout: float = 15.0) -> bytes:
            raise OSError("network down")

        assert venue_probe.fetch_venue_programs(http_get=_boom) == []

    def test_partial_failure_still_returns_what_worked(self):
        got = venue_probe.fetch_venue_programs(http_get=_fake_http({"search/issues": _ISSUES}))
        assert got

    def test_malformed_payload_does_not_raise(self):
        got = venue_probe.fetch_venue_programs(http_get=_fake_http({"/releases": b"not json", "search/issues": b"{{{"}))
        assert got == []

    def test_max_items_caps_output(self):
        got = venue_probe.fetch_venue_programs(
            http_get=_fake_http({"/releases": _RELEASES, "search/issues": _ISSUES}),
            max_items=1,
        )
        assert len(got) <= 1


# --------------------------------------------------------------------------- #
# Scoring + routing                                                           #
# --------------------------------------------------------------------------- #


class TestScoringAndRouting:
    def test_program_vocabulary_scores_above_zero(self):
        assert venue_probe.score_program({"title": "New maker rebate tier", "summary": ""}) > 0

    def test_unrelated_text_scores_zero(self):
        assert venue_probe.score_program({"title": "Fix README typo", "summary": ""}) == 0

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("liquidity rewards budget per market", IdeaCategory.INCENTIVE_CAPTURE),
            ("funding rate settlement changes", IdeaCategory.BASIS_CARRY),
            ("maker order post-only quoting", IdeaCategory.MARKET_MAKING),
            ("price discrepancy between venues", IdeaCategory.CROSS_VENUE_ARBITRAGE),
        ],
    )
    def test_routes_to_the_right_category(self, text, expected):
        assert venue_probe.route_category({"title": text, "summary": ""}) == expected.value

    def test_unroutable_text_still_returns_a_bot_category(self):
        routed = venue_probe.route_category({"title": "misc api change", "summary": ""})
        assert routed in {c.value for c in MONEY_CATEGORIES}


# --------------------------------------------------------------------------- #
# Pick + seed                                                                 #
# --------------------------------------------------------------------------- #


class TestPickAndSeed:
    def test_picks_the_highest_scoring_unseen(self):
        candidates = [
            {"url": "https://a", "program_score": 5},
            {"url": "https://b", "program_score": 3},
        ]
        assert venue_probe.pick_top_program(candidates)["url"] == "https://a"

    def test_skips_already_probed(self):
        candidates = [
            {"url": "https://a", "program_score": 5},
            {"url": "https://b", "program_score": 3},
        ]
        got = venue_probe.pick_top_program(candidates, seen_urls={"https://a"})
        assert got["url"] == "https://b"

    def test_a_fully_seen_pool_yields_a_revisit_not_nothing(self):
        """Superseded: returning None here starved the cadence permanently.
        See TestNeverStarves for the reasoning."""
        candidates = [{"url": "https://a", "program_score": 5}]
        got = venue_probe.pick_top_program(candidates, seen_urls={"https://a"})
        assert got is not None
        assert got["revisit"] is True

    def test_returns_none_on_empty(self):
        assert venue_probe.pick_top_program([]) is None


class TestSeed:
    def _program(self) -> dict:
        return {
            "venue": "Polymarket",
            "family": BotVenueFamily.PREDICTION_MARKETS.value,
            "category": IdeaCategory.INCENTIVE_CAPTURE.value,
            "title": "Liquidity rewards: qualifying spread not documented",
            "url": "https://github.com/Polymarket/py-clob-client/issues/42",
            "summary": "The reward budget per market and the max spread band are unclear.",
            "source": "github-issue",
            "program_score": 6,
        }

    def test_seed_carries_the_grounding(self):
        from project_forge.engine.strategy_library import STRATEGY_LIBRARY

        seed = venue_probe.program_to_seed(self._program(), primitive=STRATEGY_LIBRARY[0])
        assert "Polymarket" in seed
        assert "https://github.com/Polymarket/py-clob-client/issues/42" in seed
        assert STRATEGY_LIBRARY[0].name in seed

    def test_seed_demands_every_spec_field(self):
        from project_forge.engine.strategy_library import STRATEGY_LIBRARY

        seed = venue_probe.program_to_seed(self._program(), primitive=STRATEGY_LIBRARY[0]).lower()
        for demand in ("venue", "api", "mechanism", "capital", "decay", "kill"):
            assert demand in seed

    def test_seed_forbids_the_product_shape(self):
        from project_forge.engine.strategy_library import STRATEGY_LIBRARY

        seed = venue_probe.program_to_seed(self._program(), primitive=STRATEGY_LIBRARY[0]).lower()
        assert "saas" in seed or "not a product" in seed

    def test_seed_demands_legality(self):
        from project_forge.engine.strategy_library import STRATEGY_LIBRARY

        seed = venue_probe.program_to_seed(self._program(), primitive=STRATEGY_LIBRARY[0]).lower()
        assert "legal" in seed or "manipul" in seed

    def test_seed_works_without_a_primitive(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "Polymarket" in seed


class TestJurisdiction:
    """Where the operator can legally trade must reach the seed.

    Observed in production: five consecutive probes were killed, two of
    them because the proposed venue bars the operator's jurisdiction. The
    panel was right, but nothing had told generation the constraint, so the
    cycle burned a generation plus a four-lens panel to discover it.
    """

    def _program(self) -> dict:
        return {
            "venue": "Polymarket",
            "family": BotVenueFamily.PREDICTION_MARKETS.value,
            "category": IdeaCategory.INCENTIVE_CAPTURE.value,
            "title": "rewards",
            "url": "https://example.com/x",
            "summary": "reward budget",
            "source": "github-issue",
            "program_score": 5,
        }

    def test_seed_carries_the_jurisdiction_when_set(self, monkeypatch):
        from project_forge.config import settings

        monkeypatch.setattr(settings, "operator_jurisdiction", "United States")
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "United States" in seed
        assert "eligib" in seed.lower() or "permitted" in seed.lower()

    def test_seed_stays_venue_agnostic_when_unset(self, monkeypatch):
        from project_forge.config import settings

        monkeypatch.setattr(settings, "operator_jurisdiction", "")
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "operator is based in" not in seed


class TestFeeArithmetic:
    """Every live kill so far was fee arithmetic, so the seed must demand it.

    Observed: drafts quoted "0.035% round-trip" when that figure was one
    fill on each venue — entry only. The true round trip is double. The
    generator has to do that sum itself, because the panel doing it later
    costs a whole cycle.
    """

    def _program(self) -> dict:
        return {
            "venue": "Hyperliquid",
            "family": BotVenueFamily.CRYPTO_DEFI.value,
            "category": IdeaCategory.BASIS_CARRY.value,
            "title": "funding endpoint",
            "url": "https://example.com/x",
            "summary": "funding history",
            "source": "github-release",
            "program_score": 5,
        }

    def test_seed_demands_round_trip_costs(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None).lower()
        assert "round trip" in seed or "round-trip" in seed
        assert "entry and exit" in seed or "both legs" in seed

    def test_seed_demands_a_net_return(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None).lower()
        assert "net of" in seed

    def test_seed_tells_it_to_walk_away(self):
        """A strategy that cannot clear its costs should be abandoned in the
        prompt, not defended and then killed by the panel."""
        seed = venue_probe.program_to_seed(self._program(), primitive=None).lower()
        assert "does not clear" in seed or "cannot clear" in seed


class TestAvoidLessons:
    """Rejections are the cheapest training signal this board has.

    The panel kept killing the same two errors — a one-way fee quoted as a
    round trip, and a capacity claim the reward pool cannot pay. Nothing
    carried that back into the next generation, so it made them again.
    """

    def _program(self) -> dict:
        return {
            "venue": "Polymarket",
            "family": BotVenueFamily.PREDICTION_MARKETS.value,
            "category": IdeaCategory.INCENTIVE_CAPTURE.value,
            "title": "rewards",
            "url": "https://example.com/x",
            "summary": "reward budget",
            "source": "github-issue",
            "program_score": 5,
        }

    def test_lessons_reach_the_seed(self):
        seed = venue_probe.program_to_seed(
            self._program(),
            primitive=None,
            avoid_lessons=[
                "Reward Minute Maker: quoted a one-way fee as a round trip",
                "Carry Bot: capacity claim exceeded what the reward pool pays",
            ],
        )
        assert "one-way fee as a round trip" in seed
        assert "capacity claim exceeded" in seed

    def test_seed_tells_it_not_to_repeat_them(self):
        seed = venue_probe.program_to_seed(
            self._program(), primitive=None, avoid_lessons=["X: fees were wrong"]
        ).lower()
        assert "already rejected" in seed or "do not repeat" in seed

    def test_no_lessons_is_fine(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None, avoid_lessons=[])
        assert "Polymarket" in seed


class TestNeverStarves:
    """The probe must never permanently run out of work.

    Live failure: after the board went US-only the candidate pool shrank to
    a single item, all 16 previously probed URLs were marked seen forever,
    and every click of "Generate one now" answered "no new venue program
    surfaced from any source". The button looked broken; the probe had
    simply declared the world exhausted.

    A previously worked program is not used up. The strategy built from it
    depends on the category rotation, the mechanism drawn from the library,
    and the accumulated rejection lessons — all of which have moved.
    """

    def _candidates(self) -> list[dict]:
        return [
            {"url": "https://a", "program_score": 5, "title": "a"},
            {"url": "https://b", "program_score": 3, "title": "b"},
        ]

    def test_unseen_is_still_preferred(self):
        got = venue_probe.pick_top_program(self._candidates(), seen_urls={"https://a"})
        assert got["url"] == "https://b"

    def test_falls_back_to_a_revisit_when_everything_is_seen(self):
        got = venue_probe.pick_top_program(self._candidates(), seen_urls={"https://a", "https://b"})
        assert got is not None, "an exhausted pool must still yield work"
        assert got["url"] == "https://a"
        assert got.get("revisit") is True

    def test_a_revisit_is_marked_so_the_log_is_honest(self):
        got = venue_probe.pick_top_program([{"url": "https://a", "program_score": 5}], seen_urls={"https://a"})
        assert got["revisit"] is True

    def test_an_actually_empty_pool_still_returns_none(self):
        assert venue_probe.pick_top_program([], seen_urls={"https://a"}) is None

    def test_fresh_candidates_are_not_marked_revisit(self):
        got = venue_probe.pick_top_program(self._candidates(), seen_urls=set())
        assert not got.get("revisit")


class TestSourceBreadth:
    def test_enough_sources_to_keep_the_pool_alive(self):
        """One repo's issue tracker is not a pipeline."""
        assert len(venue_probe.PROBE_REPOS) >= 10

    def test_sources_span_several_venues(self):
        venues = {v for _repo, v in venue_probe.PROBE_REPOS}
        assert len(venues) >= 5


class TestGitHubAuth:
    """Unauthenticated GitHub allows 60 requests/hour. A single probe makes
    26, so a few cycles exhaust it and every source starts returning 403 —
    which the probe correctly degrades to "no candidates", leaving the
    operator with a board that silently stops producing.

    A token raises that to 5,000/hour. It is optional: no token still works,
    just slower to exhaust.
    """

    def test_token_is_used_when_available(self, monkeypatch):
        from project_forge.feeds import _http

        monkeypatch.setenv("GH_TOKEN", "ghp_example")
        seen = {}

        class _Resp:
            # urllib responses expose .headers; http_get_bytes checks
            # Content-Length against its size cap before reading.
            headers: dict = {}

            def read(self, n=None):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=15.0):
            seen["auth"] = req.get_header("Authorization")
            return _Resp()

        monkeypatch.setattr(_http.urllib.request, "urlopen", _fake_urlopen)
        _http.http_get_bytes("https://api.github.com/repos/x/y")
        assert seen["auth"] == "Bearer ghp_example"

    def test_no_token_is_fine(self, monkeypatch):
        from project_forge.feeds import _http

        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        seen = {}

        class _Resp:
            # urllib responses expose .headers; http_get_bytes checks
            # Content-Length against its size cap before reading.
            headers: dict = {}

            def read(self, n=None):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=15.0):
            seen["auth"] = req.get_header("Authorization")
            return _Resp()

        monkeypatch.setattr(_http.urllib.request, "urlopen", _fake_urlopen)
        _http.http_get_bytes("https://api.github.com/repos/x/y")
        assert seen["auth"] is None

    def test_token_is_only_sent_to_github(self, monkeypatch):
        """Never leak a credential to an unrelated host."""
        from project_forge.feeds import _http

        monkeypatch.setenv("GH_TOKEN", "ghp_example")
        seen = {}

        class _Resp:
            # urllib responses expose .headers; http_get_bytes checks
            # Content-Length against its size cap before reading.
            headers: dict = {}

            def read(self, n=None):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=15.0):
            seen["auth"] = req.get_header("Authorization")
            return _Resp()

        monkeypatch.setattr(_http.urllib.request, "urlopen", _fake_urlopen)
        _http.http_get_bytes("https://datatracker.ietf.org/feed/wg/lamps/")
        assert seen["auth"] is None


class TestOperatorChosenVenue:
    """The operator can point the engine at a venue instead of taking
    whatever the probe happened to surface.

    Two cases have to work. When the probe HAS candidates for that venue,
    prefer them — a live signal beats a synthetic one. When it does not —
    Tradier and Polymarket US have no SDK repositories at all — fall back to
    the registry entry so the choice still produces work rather than "no
    program surfaced", which would read as the picker being broken.
    """

    def _candidates(self) -> list[dict]:
        return [
            {"url": "https://a", "program_score": 9, "venue": "Coinbase", "title": "coinbase thing"},
            {"url": "https://b", "program_score": 4, "venue": "Kalshi", "title": "kalshi thing"},
        ]

    def test_a_chosen_venue_wins_over_a_higher_scoring_other(self):
        got = venue_probe.pick_top_program(self._candidates(), prefer_venue="Kalshi")
        assert got["venue"] == "Kalshi"

    def test_without_a_choice_the_best_score_still_wins(self):
        got = venue_probe.pick_top_program(self._candidates())
        assert got["venue"] == "Coinbase"

    def test_a_seen_candidate_for_the_chosen_venue_is_revisited(self):
        got = venue_probe.pick_top_program(self._candidates(), seen_urls={"https://b"}, prefer_venue="Kalshi")
        assert got["venue"] == "Kalshi"
        assert got["revisit"] is True

    def test_a_venue_with_no_candidates_falls_back_to_the_registry(self):
        got = venue_probe.pick_top_program(self._candidates(), prefer_venue="Tradier")
        assert got is not None, "choosing a venue must always produce work"
        assert got["venue"] == "Tradier"
        assert got["url"].startswith("https://")
        assert got.get("from_registry") is True

    def test_the_registry_fallback_carries_the_family_and_a_category(self):
        got = venue_probe.program_for_venue("Polymarket US")
        assert got["family"] == BotVenueFamily.PREDICTION_MARKETS.value
        assert got["category"] in {c.value for c in MONEY_CATEGORIES}
        assert "Polymarket US" in got["venue"]

    def test_an_unknown_venue_yields_nothing_rather_than_a_guess(self):
        assert venue_probe.program_for_venue("Definitely Not A Venue") is None

    def test_a_restricted_venue_cannot_be_chosen(self):
        """The gate would refuse it anyway; refusing here saves a cycle."""
        assert venue_probe.program_for_venue("Hyperliquid") is None
        assert venue_probe.program_for_venue("Polymarket") is None
