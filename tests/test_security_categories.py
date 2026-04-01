"""Tests for expanded PQC/NIST/RFC security-focused categories."""

from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.prompts import build_generation_prompt
from project_forge.models import IdeaCategory


# New categories must exist
def test_pqc_category_exists():
    assert hasattr(IdeaCategory, "PQC_CRYPTOGRAPHY")
    assert IdeaCategory.PQC_CRYPTOGRAPHY.value == "pqc-cryptography"


def test_nist_category_exists():
    assert hasattr(IdeaCategory, "NIST_STANDARDS")
    assert IdeaCategory.NIST_STANDARDS.value == "nist-standards"


def test_rfc_category_exists():
    assert hasattr(IdeaCategory, "RFC_SECURITY")
    assert IdeaCategory.RFC_SECURITY.value == "rfc-security"


def test_crypto_infra_category_exists():
    assert hasattr(IdeaCategory, "CRYPTO_INFRASTRUCTURE")
    assert IdeaCategory.CRYPTO_INFRASTRUCTURE.value == "crypto-infrastructure"


# Seeds must have deep PQC-specific content
def test_pqc_seeds_have_crl_concepts():
    seeds = CATEGORY_SEEDS[IdeaCategory.PQC_CRYPTOGRAPHY]
    concepts = " ".join(seeds["seed_concepts"]).lower()
    assert "crl" in concepts, "PQC seeds must include CRL-related concepts"
    assert "ml-dsa" in concepts or "dilithium" in concepts, "Must reference ML-DSA or Dilithium"
    assert "hybrid" in concepts, "Must include hybrid certificate concepts"


def test_pqc_seeds_have_enough_concepts():
    seeds = CATEGORY_SEEDS[IdeaCategory.PQC_CRYPTOGRAPHY]
    assert len(seeds["seed_concepts"]) >= 12, "PQC category needs deep seed coverage"
    assert len(seeds["domains_to_cross"]) >= 5


def test_nist_seeds_have_fips_concepts():
    seeds = CATEGORY_SEEDS[IdeaCategory.NIST_STANDARDS]
    concepts = " ".join(seeds["seed_concepts"]).lower()
    assert "fips" in concepts, "NIST seeds must reference FIPS standards"
    assert "sp 800" in concepts or "800-" in concepts, "Must reference SP 800 series"


def test_rfc_seeds_have_ietf_concepts():
    seeds = CATEGORY_SEEDS[IdeaCategory.RFC_SECURITY]
    concepts = " ".join(seeds["seed_concepts"]).lower()
    assert "rfc" in concepts or "ietf" in concepts, "RFC seeds must reference IETF/RFC"
    assert "lamps" in concepts or "tls" in concepts, "Must reference security working groups"


def test_crypto_infra_seeds_have_pki_concepts():
    seeds = CATEGORY_SEEDS[IdeaCategory.CRYPTO_INFRASTRUCTURE]
    concepts = " ".join(seeds["seed_concepts"]).lower()
    assert "pki" in concepts or "certificate" in concepts
    assert "ocsp" in concepts or "revocation" in concepts


def test_new_categories_have_prompts():
    for cat_name in ["PQC_CRYPTOGRAPHY", "NIST_STANDARDS", "RFC_SECURITY", "CRYPTO_INFRASTRUCTURE"]:
        category = IdeaCategory[cat_name]
        prompt = build_generation_prompt(category=category, recent_ideas=[])
        assert category.value in prompt
        assert len(prompt) > 200


def test_total_category_count():
    # 8 original + 4 new + 1 self-improvement = 13
    assert len(IdeaCategory) == 13
