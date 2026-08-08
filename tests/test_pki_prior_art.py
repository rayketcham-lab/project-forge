"""Tests for the PKI prior-art gate.

"This already exists" is the biggest killer of certificate-tooling ideas —
there are dozens of expiry monitors, ACME clients and chain checkers. This
gate asks GitHub whether a maintained tool already does the job, and is
deliberately biased toward letting things through: a network blip must
never look like "already exists".

Every test injects a fake `http_get`. Nothing here touches the network.
"""

from __future__ import annotations

import json

import pytest

from project_forge.engine.pki_prior_art import (
    MATCH_THRESHOLD,
    MAX_SEARCH_REQUESTS,
    MAX_SEARCH_TERMS,
    STAR_ESTABLISHED,
    PriorArtVerdict,
    check_prior_art,
    clear_prior_art_cache,
    extract_search_terms,
    score_match,
)
from project_forge.models import Idea, IdeaCategory


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_prior_art_cache()
    yield
    clear_prior_art_cache()


def make_idea(name: str, tagline: str, **kw) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description=kw.pop("description", ""),
        category=kw.pop("category", IdeaCategory.CERT_LIFECYCLE),
        market_analysis=kw.pop("market_analysis", ""),
        feasibility_score=kw.pop("feasibility_score", 0.7),
        mvp_scope=kw.pop("mvp_scope", ""),
        **kw,
    )


def repo(name: str, description: str, stars: int, url: str | None = None) -> dict:
    return {
        "name": name,
        "url": url or f"https://github.com/example/{name}",
        "stars": stars,
        "description": description,
    }


def fake_http(payload_by_call: list, counter: list | None = None):
    """An http_get that returns canned payloads, recording every URL."""
    calls = counter if counter is not None else []

    def _get(url: str, *, timeout: float = 15.0) -> bytes:
        calls.append(url)
        payload = payload_by_call[min(len(calls) - 1, len(payload_by_call) - 1)]
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, bytes):
            return payload
        return json.dumps(payload).encode()

    _get.calls = calls  # type: ignore[attr-defined]
    return _get


def gh_payload(*repos: dict) -> dict:
    return {
        "total_count": len(repos),
        "items": [
            {
                "full_name": f"example/{r['name']}",
                "name": r["name"],
                "html_url": r["url"],
                "stargazers_count": r["stars"],
                "description": r["description"],
                "archived": False,
            }
            for r in repos
        ],
    }


# --------------------------------------------------------------------------- #
# Search-term extraction                                                       #
# --------------------------------------------------------------------------- #


def test_extract_terms_strips_generic_filler():
    idea = make_idea(
        "Certificate Revocation Platform",
        "A management system and tool suite for operators",
    )
    joined = " ".join(extract_search_terms(idea))
    for filler in ("platform", "system", "tool", "suite", "management"):
        assert filler not in joined.split()


def test_extract_terms_keeps_domain_tokens():
    idea = make_idea("CRL Delta Distributor", "Pushes OCSP deltas to ACME issuers")
    joined = " ".join(extract_search_terms(idea))
    for token in ("crl", "ocsp", "acme"):
        assert token in joined


def test_extract_terms_prefers_specific_domain_tokens_over_broad_ones():
    idea = make_idea(
        "Workload Trust Bootstrapper",
        "Bootstraps X.509 certificate trust for SPIFFE workloads from an HSM-backed TLS root",
    )
    joined = " ".join(extract_search_terms(idea))
    assert "spiffe" in joined
    assert "hsm" in joined


def test_extract_terms_is_capped_and_nonempty():
    idea = make_idea(
        "CRLite Delta Distributor",
        "Ships incremental CRLite filter deltas to embedded devices over CoAP",
    )
    terms = extract_search_terms(idea)
    assert 1 <= len(terms) <= MAX_SEARCH_TERMS
    assert all(t.strip() for t in terms)


def test_extract_terms_on_pure_filler_name_returns_nothing_useless():
    idea = make_idea("The Platform", "A system for the tool")
    assert extract_search_terms(idea) == []


# --------------------------------------------------------------------------- #
# Match scoring                                                                #
# --------------------------------------------------------------------------- #


def test_score_match_flags_same_job():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    hit = repo(
        "cert-expiry-monitor",
        "Monitors TLS certificate expiry across your fleet and alerts before renewal",
        stars=4200,
    )
    assert score_match(idea, hit) >= MATCH_THRESHOLD


def test_score_match_ignores_merely_topical_overlap():
    idea = make_idea(
        "CRLite Delta Distributor",
        "Ships incremental CRLite filter deltas to embedded devices over CoAP",
    )
    topical = repo(
        "awesome-certificates",
        "A curated list of certificate, TLS and PKI resources",
        stars=9000,
    )
    assert score_match(idea, topical) < MATCH_THRESHOLD


def test_score_match_star_weighting_spares_unmaintained_repos():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    body = "Monitors TLS certificate expiry across your fleet and alerts before renewal"
    popular = score_match(idea, repo("cert-expiry-monitor", body, stars=STAR_ESTABLISHED * 10))
    abandoned = score_match(idea, repo("cert-expiry-monitor", body, stars=3))
    assert abandoned < popular
    assert abandoned < MATCH_THRESHOLD


def test_shared_domain_vocabulary_alone_is_never_prior_art():
    """The failure mode that quietly empties the board: this board is
    ENTIRELY about certificates, so `chain`, `hsm`, `ocsp` and `ceremony` are
    the words two items share by both being PKI. Scored as purpose, two of
    them plus a popular repo cleared the kill threshold on their own."""
    sizer = make_idea("PQ Chain Sizer", "Model ML-DSA handshake bloat across TLS certificate chains")
    fuzzer = repo("tlsfuzzer", "TLS protocol test suite covering handshake and chain edge cases", stars=700)
    assert score_match(sizer, fuzzer) < MATCH_THRESHOLD

    ceremony = make_idea("Ceremony Rehearsal Kit", "Rehearse root key ceremony scripts against an HSM")
    softhsm = repo("softhsm", "A software implementation of an HSM with a PKCS#11 interface", stars=1200)
    assert score_match(ceremony, softhsm) < MATCH_THRESHOLD


def test_shared_job_words_still_score_over_the_same_vocabulary():
    """The other half of the bargain: fold domain nouns into the topical
    class and a genuine duplicate must still land."""
    idea = make_idea(
        "OCSP Stapling Drift Detector",
        "Detect and alert when stapled OCSP responses drift stale across a fleet",
    )
    duplicate = repo(
        "staple-drift",
        "Detects stale stapled OCSP responses across your fleet and alerts on drift",
        stars=1500,
    )
    assert score_match(idea, duplicate) >= MATCH_THRESHOLD


def test_score_match_is_bounded():
    idea = make_idea("Certificate Expiry Monitor", "Monitors certificate expiry and alerts on renewal")
    hit = repo("cert-expiry-monitor", "monitor certificate expiry alerts renewal fleet", stars=99999)
    assert 0.0 <= score_match(idea, hit) <= 1.0
    assert score_match(idea, repo("unrelated", "a static site generator", stars=500)) == 0.0


# --------------------------------------------------------------------------- #
# check_prior_art — the verdict                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_obvious_duplicate_is_flagged():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http(
        [
            gh_payload(
                repo(
                    "cert-expiry-monitor",
                    "Monitors TLS certificate expiry across your fleet and alerts before renewal",
                    stars=4200,
                )
            )
        ]
    )
    verdict = await check_prior_art(idea, http_get=http)
    assert isinstance(verdict, PriorArtVerdict)
    assert verdict.exists is True
    assert verdict.confidence >= MATCH_THRESHOLD
    assert verdict.matches[0]["name"] == "cert-expiry-monitor"
    assert verdict.matches[0]["stars"] == 4200
    assert "cert-expiry-monitor" in verdict.reason


@pytest.mark.asyncio
async def test_novel_idea_against_topical_repo_is_not_flagged():
    idea = make_idea(
        "CRLite Delta Distributor",
        "Ships incremental CRLite filter deltas to embedded devices over CoAP",
    )
    topical = repo("awesome-certificates", "A curated list of certificate and PKI resources", 9000)
    http = fake_http([gh_payload(topical)])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert verdict.confidence < MATCH_THRESHOLD


@pytest.mark.asyncio
async def test_empty_search_results_are_not_prior_art():
    idea = make_idea("CRLite Delta Distributor", "Ships incremental CRLite filter deltas over CoAP")
    http = fake_http([{"total_count": 0, "items": []}])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert verdict.matches == []


@pytest.mark.asyncio
async def test_low_star_match_does_not_kill_the_idea():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http(
        [
            gh_payload(
                repo(
                    "cert-expiry-monitor",
                    "Monitors TLS certificate expiry across your fleet and alerts before renewal",
                    stars=2,
                )
            )
        ]
    )
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False


@pytest.mark.asyncio
async def test_request_count_is_capped():
    idea = make_idea(
        "CRL Delta Distributor",
        "Pushes OCSP and ACME revocation deltas to HSM-backed SPIFFE workloads over CoAP",
    )
    http = fake_http([{"total_count": 0, "items": []}])
    await check_prior_art(idea, http_get=http)
    assert 1 <= len(http.calls) <= MAX_SEARCH_REQUESTS


# --------------------------------------------------------------------------- #
# Fail open                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_network_failure_fails_open():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http([OSError("connection refused")])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert verdict.confidence == 0.0
    assert verdict.matches == []
    assert "could not" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_malformed_json_fails_open():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http([b"<html>502 Bad Gateway</html>"])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert "could not" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_rate_limit_body_fails_open():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http([{"message": "API rate limit exceeded", "documentation_url": "https://docs.github.com"}])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert "could not" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_partial_failure_still_uses_the_working_search():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    payloads = [
        OSError("boom"),
        gh_payload(
            repo(
                "cert-expiry-monitor",
                "Monitors TLS certificate expiry across your fleet and alerts before renewal",
                stars=4200,
            )
        ),
    ]
    verdict = await check_prior_art(idea, http_get=fake_http(payloads))
    assert verdict.exists is True


@pytest.mark.asyncio
async def test_unsearchable_idea_fails_open():
    idea = make_idea("The Platform", "A system for the tool")
    http = fake_http([{"total_count": 0, "items": []}])
    verdict = await check_prior_art(idea, http_get=http)
    assert verdict.exists is False
    assert http.calls == []


# --------------------------------------------------------------------------- #
# Caching                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repeat_check_is_served_from_cache():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    http = fake_http(
        [
            gh_payload(
                repo(
                    "cert-expiry-monitor",
                    "Monitors TLS certificate expiry across your fleet and alerts before renewal",
                    stars=4200,
                )
            )
        ]
    )
    first = await check_prior_art(idea, http_get=http)
    fetched = len(http.calls)
    assert fetched >= 1
    second = await check_prior_art(idea, http_get=http)
    assert len(http.calls) == fetched  # no re-fetch
    assert second.exists == first.exists
    assert second.confidence == pytest.approx(first.confidence)


@pytest.mark.asyncio
async def test_clear_cache_forces_refetch():
    idea = make_idea("CRLite Delta Distributor", "Ships incremental CRLite filter deltas over CoAP")
    http = fake_http([{"total_count": 0, "items": []}])
    await check_prior_art(idea, http_get=http)
    fetched = len(http.calls)
    clear_prior_art_cache()
    await check_prior_art(idea, http_get=http)
    assert len(http.calls) > fetched


@pytest.mark.asyncio
async def test_cache_is_bounded():
    """uvicorn processes live for weeks. An unbounded per-term dict also pins
    a stale empty result for a term forever."""
    from project_forge.engine import pki_prior_art

    http = fake_http([{"total_count": 0, "items": []}])
    for n in range(pki_prior_art.MAX_CACHE_ENTRIES + 20):
        await pki_prior_art._search(f"crl shard term{n}", http)
    assert len(pki_prior_art._SEARCH_CACHE) <= pki_prior_art.MAX_CACHE_ENTRIES


@pytest.mark.asyncio
async def test_failed_search_is_not_cached():
    idea = make_idea(
        "Certificate Expiry Monitor",
        "Monitors certificate expiry across a fleet and alerts before renewal",
    )
    calls: list[str] = []
    payloads = [
        OSError("boom"),
        gh_payload(
            repo(
                "cert-expiry-monitor",
                "Monitors TLS certificate expiry across your fleet and alerts before renewal",
                stars=4200,
            )
        ),
    ]
    seq = {"i": 0}

    def _get(url: str, *, timeout: float = 15.0) -> bytes:
        calls.append(url)
        payload = payloads[0] if seq["i"] == 0 else payloads[1]
        seq["i"] += 1
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()

    first = await check_prior_art(idea, http_get=_get)
    assert first.exists is False or first.exists is True  # verdict irrelevant here
    before = len(calls)
    await check_prior_art(idea, http_get=_get)
    assert len(calls) > before  # the failed term was retried, not cached
