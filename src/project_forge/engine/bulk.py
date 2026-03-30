"""Bulk idea generation for mass think-tank sessions."""

import logging
import random

from pydantic import BaseModel, Field

from project_forge.engine.generator import IdeaGenerator
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

SECURITY_FOCUS_AREAS: list[dict] = [
    {
        "name": "PQC CRL & Revocation",
        "description": "Post-quantum certificate revocation list issuance, management, and distribution challenges",
        "categories": [IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.CRYPTO_INFRASTRUCTURE],
        "seed_topics": [
            "CRL size explosion with ML-DSA/SLH-DSA signatures",
            "Delta CRL optimization for PQC certificate authorities",
            "OCSP responder performance under PQC signature verification load",
            "CRL distribution over constrained networks with large PQC signatures",
            "Hybrid revocation checking (classical + PQC)",
            "CRL partitioning strategies for PQC-era PKI",
        ],
    },
    {
        "name": "PQC Migration & Transition",
        "description": "Tools for migrating from classical to post-quantum cryptography",
        "categories": [IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "Crypto inventory discovery for PQC readiness assessment",
            "Hybrid certificate deployment automation",
            "PQC algorithm agility testing framework",
            "Certificate chain migration planner classical-to-PQC",
            "TLS handshake PQC performance regression tester",
        ],
    },
    {
        "name": "PQC Implementation Testing",
        "description": "Validation, conformance, and interoperability testing for PQC algorithms",
        "categories": [IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.NIST_STANDARDS],
        "seed_topics": [
            "FIPS 204 ML-DSA conformance test suite",
            "FIPS 203 ML-KEM interoperability tester",
            "FIPS 205 SLH-DSA performance benchmark suite",
            "PQC algorithm side-channel resistance tester",
            "Cross-library PQC implementation compatibility checker",
        ],
    },
    {
        "name": "NIST Compliance Automation",
        "description": "Automating compliance with NIST standards and frameworks",
        "categories": [IdeaCategory.NIST_STANDARDS, IdeaCategory.COMPLIANCE],
        "seed_topics": [
            "NIST SP 800-57 key management lifecycle automation",
            "NIST CSF 2.0 to technical controls auto-mapper",
            "CMVP module validation evidence collector",
            "NIST 800-131A crypto transition compliance scanner",
            "FedRAMP continuous monitoring automation",
            "NIST 800-63 identity assurance level calculator",
        ],
    },
    {
        "name": "RFC Implementation & Compliance",
        "description": "Tools for implementing, testing, and validating RFC compliance",
        "categories": [IdeaCategory.RFC_SECURITY, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "RFC compliance test suite generator from RFC text",
            "IETF draft-to-implementation gap analyzer",
            "RFC 5280 X.509 profile validator for PQC",
            "RFC 8446 TLS 1.3 PQC key exchange compliance checker",
            "RFC deprecation impact analyzer for infrastructure",
        ],
    },
    {
        "name": "RFC Monitoring & Intelligence",
        "description": "Tracking, analyzing, and acting on new IETF RFCs and drafts",
        "categories": [IdeaCategory.RFC_SECURITY, IdeaCategory.AUTOMATION],
        "seed_topics": [
            "IETF security draft watcher with impact analysis",
            "RFC dependency graph builder and change propagation tracker",
            "New RFC to security advisory correlator",
            "IETF working group activity dashboard",
            "RFC implementation readiness scorer",
        ],
    },
    {
        "name": "PKI & Certificate Infrastructure",
        "description": "Certificate authority operations, PKI health, and certificate lifecycle",
        "categories": [IdeaCategory.CRYPTO_INFRASTRUCTURE, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "PKI health monitoring dashboard with CRL/OCSP metrics",
            "Certificate chain path-building optimizer",
            "CA ceremony audit trail verifier",
            "Root store program compliance checker",
            "Certificate misuse and abuse detection",
            "Cross-signed certificate migration planner",
        ],
    },
    {
        "name": "Security Vulnerability Tooling",
        "description": "Novel vulnerability discovery, analysis, and remediation tools",
        "categories": [IdeaCategory.VULNERABILITY_RESEARCH, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "Cryptographic protocol downgrade attack detector",
            "Certificate transparency log anomaly detector",
            "TLS configuration drift monitor",
            "API authentication bypass fuzzer",
            "Dependency confusion attack surface mapper",
        ],
    },
    {
        "name": "DevSecOps & Security Automation",
        "description": "Security tooling integrated into development and operations workflows",
        "categories": [IdeaCategory.DEVOPS_TOOLING, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "SBOM-driven vulnerability prioritization engine",
            "Security policy-as-code enforcement for CI/CD",
            "Secrets rotation orchestrator with zero-downtime",
            "Container image crypto material scanner",
            "Infrastructure-as-code security drift detector",
        ],
    },
    {
        "name": "Privacy & Data Protection",
        "description": "Privacy-preserving technologies and data protection automation",
        "categories": [IdeaCategory.PRIVACY, IdeaCategory.COMPLIANCE],
        "seed_topics": [
            "PII discovery in unstructured data streams",
            "Differential privacy budget tracker and enforcer",
            "Cross-border data flow compliance mapper",
            "Privacy impact assessment automation engine",
            "Synthetic data generator with formal privacy guarantees",
        ],
    },
    {
        "name": "Cryptographic Operations",
        "description": "HSM management, key lifecycle, and crypto operations tooling",
        "categories": [IdeaCategory.CRYPTO_INFRASTRUCTURE, IdeaCategory.NIST_STANDARDS],
        "seed_topics": [
            "HSM fleet PQC readiness assessor",
            "PKCS#11 provider compatibility matrix generator",
            "Key ceremony automation and audit tooling",
            "Crypto agility framework for algorithm migration",
            "Hardware security module performance profiler",
        ],
    },
    {
        "name": "Observability & Incident Response",
        "description": "Security monitoring, detection, and incident response tooling",
        "categories": [IdeaCategory.OBSERVABILITY, IdeaCategory.SECURITY_TOOL],
        "seed_topics": [
            "Certificate expiration prediction with ML",
            "TLS handshake anomaly detector",
            "Security event correlation across distributed systems",
            "Incident response runbook automation engine",
            "Cryptographic failure pattern recognizer",
        ],
    },
]


class BulkConfig(BaseModel):
    target_count: int = 100
    batch_size: int = 5
    focus_areas: list[dict] = Field(default_factory=lambda: SECURITY_FOCUS_AREAS)
    include_contrarian: bool = True
    include_combinatoric: bool = True


class BulkGenerator:
    def __init__(self, db: Database, api_key: str, config: BulkConfig | None = None):
        self.db = db
        self.generator = IdeaGenerator(api_key=api_key)
        self.config = config or BulkConfig()

    def plan_distribution(self) -> dict[str, int]:
        """Distribute target count across focus areas."""
        areas = self.config.focus_areas
        base_count = self.config.target_count // len(areas)
        remainder = self.config.target_count % len(areas)

        distribution = {}
        for i, area in enumerate(areas):
            count = base_count + (1 if i < remainder else 0)
            distribution[area["name"]] = count
        return distribution

    async def generate_batch(self, count: int) -> list[Idea]:
        """Generate a batch of ideas, handling errors gracefully."""
        ideas = []
        areas = self.config.focus_areas

        for i in range(count):
            area = areas[i % len(areas)]
            category = random.choice(area["categories"])

            try:
                idea = await self.generator.generate(
                    category=category,
                    recent_ideas=[idea.name for idea in ideas[-5:]],
                    use_contrarian=self.config.include_contrarian and (i % 3 == 1),
                    use_combinatoric=self.config.include_combinatoric and (i % 3 == 2),
                )
                await self.db.save_idea(idea)
                ideas.append(idea)
                logger.info("Generated %d/%d: %s", len(ideas), count, idea.name)
            except Exception as e:
                logger.error("Failed to generate idea %d: %s", i + 1, e)
                continue

        return ideas

    async def run_full_generation(self) -> list[Idea]:
        """Run the full bulk generation across all focus areas."""
        distribution = self.plan_distribution()
        all_ideas = []

        for area_name, count in distribution.items():
            logger.info("Generating %d ideas for: %s", count, area_name)
            ideas = await self.generate_batch(count)
            all_ideas.extend(ideas)

        logger.info("Bulk generation complete: %d/%d ideas generated", len(all_ideas), self.config.target_count)
        return all_ideas
