"""Tests for the PKI multi-pass depth engine (`engine/pki_depth.py`).

The PKI board's gate is selective, but selectivity alone does not make a
finding a CA engineer would act on — a one-shot draft can be well-anchored
and still be wrong, or already solved by a tool the model never heard of.
The depth engine attacks each admitted draft from three independent
adversarial angles, kills it if the panel lands a fatal hit, and otherwise
rewrites it to answer what survived.

Covers:
  - keyless: zero calls, idea untouched, passes=0 (the non-negotiable)
  - the three lenses each get a genuinely distinct prompt
  - kill conditions: a high-severity 'solved' hit, or two high-severity
    hits across lenses
  - the revision preserves idea identity (id / category / content_hash)
  - unparseable or garbage revision output falls back to the original
  - objections come back strongest-first
  - a clean draft survives with the revision applied

No test may reach a real LLM: the backend is always a MagicMock.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from project_forge.engine import pki_depth
from project_forge.engine.pki_depth import (
    FATAL_SEVERITY,
    HIGH_SEVERITY,
    LENSES,
    OBJECTION_DISPLAY_FLOOR,
    UNCITED_SEVERITY_CAP,
    Objection,
    deepen,
)
from project_forge.models import Idea, IdeaCategory


def _draft(**over) -> Idea:
    base = dict(
        name="Delta-CRL Shard Planner",
        tagline="partition issuing distribution points before CRLs blow the size budget",
        description=(
            "ML-DSA signatures push CRL size past what clients will fetch, and "
            "operators size shards by hand today. Anchored to RFC 5280."
        ),
        category=IdeaCategory.PKI_REVOCATION,
        market_analysis="Every CA hits this at the ML-DSA transition.",
        feasibility_score=0.8,
        mvp_scope="Phase 1: growth model. Phase 2: shard plan emitter.",
        tech_stack=["go", "postgres"],
        content_hash="deadbeefcafe",
    )
    base.update(over)
    return Idea(**base)


def _obj(text: str, severity: float, citation: str | None = None) -> str:
    payload = {"objection": text, "severity": severity}
    if citation is not None:
        payload["citation"] = citation
    return json.dumps(payload)


# The `wrong` lens must cite what it is correcting, so anything that is meant
# to land as a real hit carries one.
def _cited(text: str, severity: float) -> str:
    return _obj(text, severity, citation="RFC 5280 s5.2.5")


_CLEAN_REVISION = json.dumps(
    {
        "name": "Delta-CRL Shard Planner",
        "tagline": "size issuing distribution points against a hard client fetch budget",
        "description": (
            "Models per-shard revocation growth under ML-DSA signature sizes and emits "
            "a partitioning plan operators can diff. Strongest counterargument: large CAs "
            "already shard by hand, so the value is the growth model, not the partitioning."
        ),
        "mvp_scope": "Phase 1: growth model against real CRL corpora. Phase 2: plan emitter.",
        "market_analysis": "Public CAs and large enterprise PKIs at the ML-DSA transition.",
        "added_specifics": ["per-shard growth model against a real CRL corpus"],
        "dropped_claims": ["that operators have no partitioning story at all"],
    }
)


def _backend(*responses: str | None) -> MagicMock:
    backend = MagicMock()
    backend.call = MagicMock(side_effect=list(responses))
    return backend


def _use(monkeypatch, backend) -> None:
    monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: backend)


# --------------------------------------------------------------------------- #
# keyless                                                                     #
# --------------------------------------------------------------------------- #


class TestKeyless:
    @pytest.mark.asyncio
    async def test_no_backend_returns_idea_untouched(self, monkeypatch):
        _use(monkeypatch, None)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea
        assert result.objections == []
        assert result.strongest is None
        assert result.survived is True
        assert result.passes == 0

    @pytest.mark.asyncio
    async def test_no_backend_makes_no_calls(self, monkeypatch):
        # A backend that detonates if touched — proves the keyless path never
        # reaches for one rather than merely tolerating a failure.
        exploding = MagicMock()
        exploding.call = MagicMock(side_effect=AssertionError("no LLM call allowed when keyless"))
        monkeypatch.setattr(pki_depth, "resolve_cheap_backend", lambda: None)
        result = await deepen(_draft())

        assert result.passes == 0
        assert exploding.call.call_count == 0


# --------------------------------------------------------------------------- #
# the panel                                                                   #
# --------------------------------------------------------------------------- #


class TestPanel:
    @pytest.mark.asyncio
    async def test_three_lenses_get_distinct_prompts(self, monkeypatch):
        backend = _backend(
            _obj("premise is fine", 0.1),
            _obj("nothing does this", 0.1),
            _obj("sizes check out", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft())

        prompts = [c.args[0] for c in backend.call.call_args_list[: len(LENSES)]]
        assert len(prompts) == 3
        assert len(set(prompts)) == 3, "each lens must have its own prompt"

    @pytest.mark.asyncio
    async def test_four_calls_for_a_surviving_draft(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert backend.call.call_count == 4
        assert result.passes == 4

    @pytest.mark.asyncio
    async def test_objections_sorted_strongest_first(self, monkeypatch):
        backend = _backend(
            _obj("weak premise nit", 0.2),
            _obj("partially covered by certbot", 0.55),
            _obj("wrong signature size cited", 0.4),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        sevs = [o.severity for o in result.objections]
        assert sevs == sorted(sevs, reverse=True)
        assert result.strongest == "partially covered by certbot"

    @pytest.mark.asyncio
    async def test_empty_objection_is_dropped(self, monkeypatch):
        backend = _backend(
            json.dumps({"objection": "none", "severity": 0.0}),
            _obj("", 0.9),
            _obj("wrong layer", 0.3),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert [o.text for o in result.objections] == ["wrong layer"]

    @pytest.mark.asyncio
    async def test_unparseable_lens_output_is_skipped_not_fatal(self, monkeypatch):
        backend = _backend(
            "I cannot comply with that",
            None,
            _obj("wrong threat model", 0.3),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is True
        assert len(result.objections) == 1
        assert result.objections[0].lens == "wrong"


# --------------------------------------------------------------------------- #
# kill conditions                                                             #
# --------------------------------------------------------------------------- #


class TestKill:
    @pytest.mark.asyncio
    async def test_fatal_solved_kills_outright(self, monkeypatch):
        backend = _backend(
            _obj("premise holds", 0.1),
            _obj("openssl crl -shard already does exactly this", FATAL_SEVERITY + 0.05),
            _obj("no protocol error", 0.1),
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is False
        assert result.passes == len(LENSES), "a killed draft is never revised"
        assert "openssl" in (result.strongest or "")

    @pytest.mark.asyncio
    async def test_two_high_severity_across_lenses_kills(self, monkeypatch):
        backend = _backend(
            _obj("the mechanism is not what the draft claims", HIGH_SEVERITY + 0.1),
            _obj("nothing solves it", 0.2),
            _cited("cites the wrong signature sizes entirely", HIGH_SEVERITY + 0.2),
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is False
        assert backend.call.call_count == len(LENSES)

    @pytest.mark.asyncio
    async def test_one_non_solved_high_severity_survives(self, monkeypatch):
        backend = _backend(
            _obj("mechanism is mischaracterized", HIGH_SEVERITY + 0.1),
            _obj("nothing solves it", 0.2),
            _obj("sizes fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is True
        assert result.passes == 4

    @pytest.mark.asyncio
    async def test_solved_below_threshold_does_not_kill(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("certbot partially overlaps", HIGH_SEVERITY - 0.05),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is True

    @pytest.mark.asyncio
    async def test_partial_coverage_solved_hit_is_answerable_not_fatal(self, monkeypatch):
        """The prompt's own rubric calls 0.7-0.9 "a problem the author must
        answer", and the `solved` brief invites exactly the partial answer
        that lands there. Every PKI idea has partial prior coverage; reading
        it as unsalvageable empties the board."""
        backend = _backend(
            _obj("fine", 0.1),
            _obj("lego renews ACME certs, so renewal is covered", 0.8),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is True
        assert result.passes == 4, "a survivable objection must still be answered by a rewrite"

    @pytest.mark.asyncio
    async def test_uncited_wrong_hit_cannot_vote_toward_a_kill(self, monkeypatch):
        """An uncited factual correction is an assertion. It informs the
        rewrite; it does not get a vote."""
        backend = _backend(
            _obj("the mechanism is not what the draft claims", 0.85),
            _obj("nothing solves it", 0.2),
            _obj("ML-DSA signatures are 2420 bytes, not 3309", 0.95),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.survived is True
        wrong = next(o for o in result.objections if o.lens == "wrong")
        assert wrong.severity == pytest.approx(UNCITED_SEVERITY_CAP)

    @pytest.mark.asyncio
    async def test_cited_wrong_hit_keeps_its_severity_and_its_citation(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _obj("ML-DSA-65 signatures are 3309 bytes", 0.8, citation="FIPS 204 Table 2"),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        wrong = next(o for o in result.objections if o.lens == "wrong")
        assert wrong.severity == pytest.approx(0.8)
        assert "FIPS 204 Table 2" in wrong.text, "the citation must survive to the operator"

    @pytest.mark.asyncio
    async def test_killed_draft_keeps_the_original_idea(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("already shipped in step-ca", 0.95),
            _obj("fine", 0.1),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea


# --------------------------------------------------------------------------- #
# revision                                                                    #
# --------------------------------------------------------------------------- #


class TestRevision:
    @pytest.mark.asyncio
    async def test_revision_preserves_identity(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea.id == idea.id
        assert result.idea.category == idea.category
        assert result.idea.content_hash == idea.content_hash
        assert result.idea.pki_urgency_score == idea.pki_urgency_score

    @pytest.mark.asyncio
    async def test_revision_rewrites_the_prose(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea.description != idea.description
        assert "counterargument" in result.idea.description.lower()

    @pytest.mark.asyncio
    async def test_revise_prompt_carries_the_surviving_objections(self, monkeypatch):
        backend = _backend(
            _obj("premise nit about OCSP stapling", 0.3),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft())

        revise_prompt = backend.call.call_args_list[-1].args[0]
        assert "premise nit about OCSP stapling" in revise_prompt

    @pytest.mark.asyncio
    async def test_unparseable_revision_falls_back_to_original(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            "Sure! Here is your revised idea: <not json at all>",
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea
        assert result.survived is True
        assert result.passes == 4

    @pytest.mark.asyncio
    async def test_revision_missing_fields_falls_back(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            json.dumps({"name": "Only A Name"}),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea

    @pytest.mark.asyncio
    async def test_revision_with_blank_field_falls_back(self, monkeypatch):
        payload = json.loads(_CLEAN_REVISION)
        payload["description"] = "   "
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            json.dumps(payload),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea

    @pytest.mark.asyncio
    async def test_fenced_revision_is_accepted(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            f"```json\n{_CLEAN_REVISION}\n```",
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea.tagline != idea.tagline

    @pytest.mark.asyncio
    async def test_unfaulted_draft_is_not_rewritten(self, monkeypatch):
        """A rewrite with no criticism to answer has nothing to work from and
        can only dilute a draft the panel could not fault. Three calls, not
        four, and the draft ships as written."""
        backend = _backend(
            json.dumps({"objection": "none", "severity": 0.0}),
            json.dumps({"objection": "none", "severity": 0.0}),
            json.dumps({"objection": "none", "severity": 0.0}),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.survived is True
        assert result.objections == []
        assert result.strongest is None
        assert result.idea is idea
        assert result.passes == 3
        assert backend.call.call_count == 3

    @pytest.mark.asyncio
    async def test_revision_declaring_no_delta_falls_back_to_the_draft(self, monkeypatch):
        """The only checkable forcing function available: the rewrite has to
        name what it added or what it dropped. Nothing downstream can tell
        "sharpened the mechanism" from "changed three adjectives"."""
        payload = json.loads(_CLEAN_REVISION)
        payload["added_specifics"] = []
        payload["dropped_claims"] = []
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            json.dumps(payload),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea
        assert result.revised is False

    @pytest.mark.asyncio
    async def test_revision_omitting_the_delta_keys_falls_back(self, monkeypatch):
        payload = json.loads(_CLEAN_REVISION)
        payload.pop("added_specifics")
        payload.pop("dropped_claims")
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            json.dumps(payload),
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea

    @pytest.mark.asyncio
    async def test_list_valued_mvp_scope_is_accepted(self, monkeypatch):
        """Models routinely return phased scope as a JSON list. Discarding the
        whole revision over the container type wastes the call."""
        payload = json.loads(_CLEAN_REVISION)
        payload["mvp_scope"] = ["Phase 1: growth model.", "Phase 2: plan emitter."]
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            json.dumps(payload),
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert "Phase 1: growth model." in result.idea.mvp_scope
        assert "Phase 2: plan emitter." in result.idea.mvp_scope

    @pytest.mark.asyncio
    async def test_objection_is_withheld_when_the_rewrite_never_landed(self, monkeypatch):
        """The card presents the objection as one the proposal answers. When
        the rewrite failed, the text below it answers nothing."""
        backend = _backend(
            _obj("partitioning only helps clients that honour the IDP extension", 0.6),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            "not json at all",
        )
        _use(monkeypatch, backend)
        idea = _draft()
        result = await deepen(idea)

        assert result.idea is idea
        assert result.revised is False
        assert result.strongest is None


# --------------------------------------------------------------------------- #
# misc surface                                                                #
# --------------------------------------------------------------------------- #


class TestGrounding:
    """The `solved` lens holds the only unilateral veto, and its question is
    the one with external evidence available. Handing it that evidence turns
    recall — the prompt shape that invents a plausible tool and kills real
    work with it — into adjudication."""

    _REPOS = [
        {"name": "crlite", "stars": 900, "description": "CRL filter cascade distribution for Firefox"},
    ]

    @pytest.mark.asyncio
    async def test_prior_art_reaches_the_solved_lens(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft(), prior_art=self._REPOS)

        solved = next(p for p in (c.args[0] for c in backend.call.call_args_list) if "ATTACK THE NOVELTY" in p)
        assert "crlite" in solved
        assert "900 stars" in solved

    @pytest.mark.asyncio
    async def test_prior_art_does_not_leak_into_the_other_lenses(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft(), prior_art=self._REPOS)

        for prompt in (c.args[0] for c in backend.call.call_args_list):
            if "ATTACK THE PREMISE" in prompt or "ATTACK THE CORRECTNESS" in prompt:
                assert "crlite" not in prompt

    @pytest.mark.asyncio
    async def test_absent_prior_art_is_not_reported_as_proof_of_novelty(self, monkeypatch):
        backend = _backend(
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft())

        solved = next(p for p in (c.args[0] for c in backend.call.call_args_list) if "ATTACK THE NOVELTY" in p)
        assert "Do not treat this as proof of novelty" in solved

    @pytest.mark.asyncio
    async def test_revision_must_differentiate_against_named_prior_art(self, monkeypatch):
        backend = _backend(
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _obj("minor", 0.2),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        await deepen(_draft(), prior_art=self._REPOS)

        revise = backend.call.call_args_list[-1].args[0]
        assert "crlite" in revise


class TestSurface:
    @pytest.mark.asyncio
    async def test_a_nit_is_never_surfaced_as_the_strongest_counterargument(self, monkeypatch):
        backend = _backend(
            _obj("the tagline could be tighter", 0.05),
            _obj("none", 0.0),
            _obj("none", 0.0),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert result.objections, "the nit is still fed to the rewrite"
        assert result.strongest is None, "a 0.05 quibble is not a counterargument"

    def test_display_floor_sits_above_the_nit_line(self):
        assert UNCITED_SEVERITY_CAP < OBJECTION_DISPLAY_FLOOR < HIGH_SEVERITY < FATAL_SEVERITY <= 1.0

    def test_lenses_are_the_three_documented_angles(self):
        assert LENSES == ("real", "solved", "wrong")

    def test_high_severity_is_a_tunable_in_range(self):
        assert 0.0 < HIGH_SEVERITY < 1.0

    def test_objection_is_a_plain_record(self):
        o = Objection(lens="real", severity=0.5, text="x")
        assert (o.lens, o.severity, o.text) == ("real", 0.5, "x")

    @pytest.mark.asyncio
    async def test_out_of_range_severity_is_clamped(self, monkeypatch):
        backend = _backend(
            _obj("way over", 4.2),
            _obj("way under", -3.0),
            _obj("fine", 0.1),
            _CLEAN_REVISION,
        )
        _use(monkeypatch, backend)
        result = await deepen(_draft())

        assert all(0.0 <= o.severity <= 1.0 for o in result.objections)
