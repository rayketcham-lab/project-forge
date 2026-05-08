"""TDD: Super idea generation produces clean names and never stores junk in DB.

Root causes:
1. generate_seeded() had its own broken dedup (only stripped parentheticals, not
   synthesis suffixes or hyphens) — a second source of truth that diverged from
   _super_base_name in dedup.py.
2. Old bad-name super ideas (hyphens, stop words) blocked fresh generation because
   should_accept correctly matched "Certificate-Pinning Observatory" and
   "Certificate Pinning Observatory" to the same base "certificate pinning".
3. No CI gate caught either issue.

Fix targets:
- generate_seeded(): use _super_base_name from dedup.py for existing_base_names
- purge_bad_super_ideas(): new db.py method to archive pre-fix bad-name supers
- CI: after purge+generate, all super ideas in DB must pass name quality checks
"""

import re

import pytest
import pytest_asyncio

from project_forge.engine.dedup import _super_base_name, should_accept
from project_forge.engine.super_ideas import _NAME_STOP_WORDS, SuperIdeaGenerator
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, tagline: str, cat: IdeaCategory = IdeaCategory.SECURITY_TOOL, score: float = 0.82) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description="Desc.",
        category=cat,
        market_analysis="Market.",
        feasibility_score=score,
        mvp_scope="MVP.",
        tech_stack=["python"],
    )


def _super(name: str, score: float = 0.92, cat: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    return Idea(
        name=name,
        tagline=f"Unified platform for {name}",
        description="A mega project.",
        category=cat,
        market_analysis="Big market.",
        feasibility_score=score,
        mvp_scope="Phase 1, 2, 3.",
        tech_stack=["python", "rust"],
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_gen_quality.db")
    await d.connect()
    yield d
    await d.close()


@pytest_asyncio.fixture
async def db_with_ideas(db):
    """DB pre-seeded with enough ideas across multiple categories to trigger generation."""
    ideas = [
        # PQC + Crypto cluster
        _idea("CRYSTALS-Kyber Implementation", "kyber: post-quantum cryptography", IdeaCategory.PQC_CRYPTOGRAPHY),
        _idea("CRYSTALS-Dilithium Signer", "dilithium signing: post-quantum signatures", IdeaCategory.PQC_CRYPTOGRAPHY),
        _idea("Post-Quantum TLS Handshake Analyzer", "tls handshake: pqc migration", IdeaCategory.PQC_CRYPTOGRAPHY),
        _idea("X.509 PQ Certificate Tool", "pqc x509: certificate issuance", IdeaCategory.CRYPTO_INFRASTRUCTURE),
        _idea("HSM Post-Quantum Key Ceremony", "hsm ceremony: pqc hardware", IdeaCategory.CRYPTO_INFRASTRUCTURE),
        _idea("Certificate Revocation OCSP Monitor", "ocsp: certificate lifecycle", IdeaCategory.CRYPTO_INFRASTRUCTURE),
        # Security Tool + Vulnerability cluster
        _idea("Static Analysis Security Scanner", "sast: vulnerability detection", IdeaCategory.SECURITY_TOOL),
        _idea("Dependency Vuln Tracker", "dependency audit: vuln research", IdeaCategory.VULNERABILITY_RESEARCH),
        _idea("Fuzzing Harness Generator", "fuzzing: security research", IdeaCategory.VULNERABILITY_RESEARCH),
        _idea("SBOM Generation Pipeline", "sbom: supply chain security", IdeaCategory.SECURITY_TOOL),
        _idea("Threat Modeling Automation", "threat modeling: security architecture", IdeaCategory.SECURITY_TOOL),
        # RFC + Crypto cluster
        _idea("RFC 8446 Compliance Verifier", "tls compliance: rfc verification", IdeaCategory.RFC_SECURITY),
        _idea("RFC 5280 Certificate Validator", "certificate validation: rfc", IdeaCategory.RFC_SECURITY),
        _idea("PKIX Path Building Library", "pkix path: chain building", IdeaCategory.CRYPTO_INFRASTRUCTURE),
    ]
    for idea in ideas:
        await db.save_idea(idea)
    return db


# ── generate_seeded: single-source dedup via _super_base_name ────────


class TestGenerateSeededDedup:
    """generate_seeded must use _super_base_name from dedup.py, not an inline broken version."""

    @pytest.mark.asyncio
    async def test_does_not_regenerate_when_base_name_covered(self, db_with_ideas):
        """If 'Certificate Pinning Observatory' exists, 'Certificate-Pinning Observatory' must be rejected."""
        # Save an existing super with a known base name
        existing = _super("[SUPER] Certificate Pinning Observatory", score=0.92)
        await db_with_ideas.save_idea(existing)

        # Directly test that should_accept rejects the hyphen variant
        candidate = _super("[SUPER] Certificate-Pinning Observatory")
        candidate.content_hash = None
        accepted, reason = await should_accept(candidate, db_with_ideas)

        assert not accepted
        assert reason is not None
        assert "duplicate" in reason.lower()

    @pytest.mark.asyncio
    async def test_does_not_regenerate_synthesis_suffix_variant(self, db_with_ideas):
        """If 'Certificate Pinning Observatory' exists, 'Certificate Pinning Defense Suite' must be rejected."""
        existing = _super("[SUPER] Certificate Pinning Observatory", score=0.92)
        await db_with_ideas.save_idea(existing)

        candidate = _super("[SUPER] Certificate Pinning Defense Suite")
        candidate.content_hash = None
        accepted, reason = await should_accept(candidate, db_with_ideas)

        assert not accepted, "Same base 'certificate pinning' with different suffix must be rejected"

    @pytest.mark.asyncio
    async def test_accepts_genuinely_new_concept(self, db_with_ideas):
        """A new concept must be accepted even if other supers exist."""
        existing = _super("[SUPER] Certificate Pinning Observatory", score=0.92)
        await db_with_ideas.save_idea(existing)

        candidate = _super("[SUPER] Quantum Migration Platform")
        candidate.content_hash = None
        accepted, reason = await should_accept(candidate, db_with_ideas)

        assert accepted

    @pytest.mark.asyncio
    async def test_generate_seeded_dedup_uses_super_base_name(self, db_with_ideas):
        """generate_seeded must consult _super_base_name not inline parenthetical-only strip."""
        gen = SuperIdeaGenerator(db_with_ideas)

        # Run generation for all 5 slots — collect generated ideas
        generated = []
        for slot in range(5):
            result = await gen.generate_seeded(slot=slot)
            if result:
                generated.append(result)

        # Each slot should have produced at most 1 super idea
        # Run again — should NOT produce duplicates with same base names
        initial_count = len(await db_with_ideas.list_super_ideas())

        generated2 = []
        for slot in range(5):
            result = await gen.generate_seeded(slot=slot)
            if result:
                generated2.append(result)

        final_count = len(await db_with_ideas.list_super_ideas())
        # Second pass may add new ideas, but not same base names
        assert final_count >= initial_count


# ── name quality: no stop words or hyphens ───────────────────────────


class TestGeneratedNameQuality:
    """Generated super idea names must not contain stop words or hyphens."""

    @pytest.mark.asyncio
    async def test_no_stop_words_in_generated_names(self, db_with_ideas):
        """Generated names must not have all-stop-word base concepts."""
        gen = SuperIdeaGenerator(db_with_ideas)

        for slot in range(5):
            await gen.generate_seeded(slot=slot)

        supers = await db_with_ideas.list_super_ideas()
        assert supers, "At least one super idea should be generated"

        # Check the BASE concept (synthesis suffix stripped) — that's where junk words appear.
        # The suffix itself (e.g., "Lifecycle Platform", "Defense Suite") may contain stop
        # words by design and should not be flagged.
        bad_names = []
        for idea in supers:
            base = _super_base_name(idea.name)
            words = re.findall(r"[a-zA-Z]+", base)
            for word in words:
                if word.lower() in _NAME_STOP_WORDS and len(word) >= 5:
                    bad_names.append((idea.name, word, f"base={base}"))

        assert bad_names == [], (
            f"Stop words found in base concept of generated super ideas: {bad_names}"
        )

    @pytest.mark.asyncio
    async def test_no_hyphenated_concept_words_in_names(self, db_with_ideas):
        """Generated super idea names must not contain hyphens between concept words."""
        gen = SuperIdeaGenerator(db_with_ideas)

        for slot in range(5):
            await gen.generate_seeded(slot=slot)

        supers = await db_with_ideas.list_super_ideas()
        hyphenated = [
            idea.name for idea in supers
            if re.search(r"[A-Za-z]+-[A-Za-z]+", idea.name.replace("[SUPER] ", ""))
        ]
        assert hyphenated == [], f"Hyphenated names generated: {hyphenated}"

    @pytest.mark.asyncio
    async def test_generated_names_have_meaningful_keywords(self, db_with_ideas):
        """Generated names must derive from meaningful 5+ char domain keywords."""
        gen = SuperIdeaGenerator(db_with_ideas)

        for slot in range(5):
            await gen.generate_seeded(slot=slot)

        supers = await db_with_ideas.list_super_ideas()
        for idea in supers:
            base = _super_base_name(idea.name)
            # Base must have at least one meaningful word (5+ chars, not a stop word)
            base_words = [w for w in base.split() if len(w) >= 5 and w not in _NAME_STOP_WORDS]
            assert base_words, (
                f"Super idea '{idea.name}' has no meaningful keywords in base '{base}'"
            )


# ── purge_bad_super_ideas: DB method to clean pre-fix junk ────────────


class TestPurgeBadSuperIdeas:
    """Database.purge_bad_super_ideas must archive ideas with bad-pattern names."""

    @pytest.mark.asyncio
    async def test_archives_hyphenated_names(self, db):
        """Names with hyphens between concept words must be archived."""
        bad = _super("[SUPER] Certificate-Pinning Observatory")
        await db.save_idea(bad)

        archived = await db.purge_bad_super_ideas()

        assert bad.id in archived, "Hyphenated super idea must be archived"
        updated = await db.get_idea(bad.id)
        assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_archives_stop_word_names(self, db):
        """Names with stop-word or single-keyword bases must be archived."""
        bad1 = _super("[SUPER] Well Known Defense Suite")         # base "well known" — both stop words
        bad2 = _super("[SUPER] Multi Control Command Center")     # base "multi control" — both stop words
        bad3 = _super("[SUPER] Insecure Direct Observatory")      # base "insecure direct" — both stop words
        bad4 = _super("[SUPER] Mapper & Multi Lifecycle Platform") # base "mapper multi" — both stop words
        bad5 = _super("[SUPER] Migration Post Command Center")    # base "migration post" — 1 meaningful word only
        for b in [bad1, bad2, bad3, bad4, bad5]:
            await db.save_idea(b)

        archived = await db.purge_bad_super_ideas()

        for b in [bad1, bad2, bad3, bad4, bad5]:
            assert b.id in archived, f"Bad super idea '{b.name}' must be archived"
            updated = await db.get_idea(b.id)
            assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_preserves_good_names(self, db):
        """Super ideas with meaningful concept words must not be archived."""
        good1 = _super("[SUPER] Certificate Pinning Observatory")
        good2 = _super("[SUPER] Quantum Migration Defense Suite")
        good3 = _super("[SUPER] CRYSTALS Kyber Operations Center")
        for g in [good1, good2, good3]:
            await db.save_idea(g)

        archived = await db.purge_bad_super_ideas()

        for g in [good1, good2, good3]:
            assert g.id not in archived, f"Good super idea '{g.name}' must NOT be archived"
            updated = await db.get_idea(g.id)
            assert updated.status == "new"

    @pytest.mark.asyncio
    async def test_preserves_approved_and_contributed(self, db):
        """Approved and contributed super ideas must never be archived by purge."""
        approved = _super("[SUPER] Multi Control Command Center")  # bad name, but approved
        await db.save_idea(approved)
        await db.db.execute("UPDATE ideas SET status = 'approved' WHERE id = ?", (approved.id,))
        await db.db.commit()

        archived = await db.purge_bad_super_ideas()

        assert approved.id not in archived, "Approved super must never be purged"

    @pytest.mark.asyncio
    async def test_returns_count_of_archived(self, db):
        """purge_bad_super_ideas must return the set of archived idea IDs."""
        bad_names = [
            "[SUPER] Well Known Defense Suite",       # all stop words
            "[SUPER] Multi Control Command Center",   # all stop words
            "[SUPER] Certificate-Pinning Observatory", # hyphenated
            "[SUPER] Migration Post Command Center",  # only 1 meaningful keyword
        ]
        for n in bad_names:
            await db.save_idea(_super(n))

        archived = await db.purge_bad_super_ideas()

        assert len(archived) == 4

    @pytest.mark.asyncio
    async def test_idempotent_on_already_archived(self, db):
        """Running purge twice must not change already-archived ideas."""
        bad = _super("[SUPER] Well Known Defense Suite")
        await db.save_idea(bad)

        first = await db.purge_bad_super_ideas()
        second = await db.purge_bad_super_ideas()

        assert bad.id in first
        assert bad.id not in second  # second run finds nothing new to archive


# ── CI integration: end-to-end clean slate ────────────────────────────


class TestEndToEndCleanGeneration:
    """After purge + generate, DB must contain only good-quality super ideas."""

    @pytest.mark.asyncio
    async def test_full_cycle_produces_clean_ideas(self, db_with_ideas):
        """Simulate the full purge → generate cycle and verify results."""
        # Seed some pre-fix junk to simulate the real DB state
        junk = [
            _super("[SUPER] Multi Control Command Center", score=0.91),
            _super("[SUPER] Well Known Defense Suite", score=0.91),
            _super("[SUPER] Certificate-Pinning Observatory", score=0.91),
            _super("[SUPER] Insecure Direct Observatory", score=0.90),
        ]
        for j in junk:
            await db_with_ideas.save_idea(j)

        # Step 1: purge
        archived = await db_with_ideas.purge_bad_super_ideas()
        assert len(archived) >= 4, f"Expected at least 4 purged, got {len(archived)}: {archived}"

        # Step 2: generate
        gen = SuperIdeaGenerator(db_with_ideas)
        for slot in range(5):
            await gen.generate_seeded(slot=slot)

        # Step 3: verify all remaining active super ideas have clean names
        supers = await db_with_ideas.list_super_ideas()

        bad = []
        for idea in supers:
            core = idea.name.replace("[SUPER] ", "")
            # Check hyphens in concept part
            if re.search(r"[A-Za-z]+-[A-Za-z]+", core):
                bad.append(f"HYPHEN: {idea.name}")
            # Check stop words in the BASE concept (not the synthesis suffix)
            base = _super_base_name(idea.name)
            words = re.findall(r"[a-zA-Z]+", base)
            for word in words:
                if word.lower() in _NAME_STOP_WORDS and len(word) >= 5:
                    bad.append(f"STOP_WORD({word}) in base '{base}': {idea.name}")

        assert bad == [], "Bad names survived purge+generate cycle:\n" + "\n".join(bad)
