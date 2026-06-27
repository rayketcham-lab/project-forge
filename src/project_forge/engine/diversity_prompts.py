"""Diversity prompt templates — combinatoric, contrarian, persona seeds.

Extracted from engine/categories.py: this is prompt-engineering material,
distinct from the static category seed dictionary. Keeping them separate
lets prompts.py reach for them without dragging the whole 550-line
CATEGORY_SEEDS dict into its dependency graph.

Imports remain backward-compatible — `from project_forge.engine.categories
import COMBINATORIC_TEMPLATES, CONTRARIAN_PROMPTS, PERSONA_SEEDS` still
works (categories.py re-exports).
"""

from __future__ import annotations

COMBINATORIC_TEMPLATES = [
    "What if {concept_a} was applied to {domain_b}?",
    "What tool would you need if {concept_a} and {concept_b} had to work together?",
    "What's the opposite of {concept_a} -- and would building that be valuable?",
    "If {domain_a} had the same tooling maturity as {domain_b}, what would exist that doesn't today?",
    "What breaks first when {concept_a} scales to 10x its current usage?",
]

CONTRARIAN_PROMPTS = [
    "What security problem does everyone ignore because they think it's already solved?",
    "What developer tool is everyone building wrong because they copied the first implementation?",
    "What compliance requirement is actually an opportunity disguised as a burden?",
    "What monitoring gap only becomes visible during an incident -- but could be caught proactively?",
    "What problem is getting worse faster than the current solutions can keep up with?",
    "What would a startup build if they had zero legacy constraints but deep domain expertise?",
    "What tool do teams build internally over and over because no good open-source version exists?",
    "What's the 'spreadsheet that should be a product' in your domain?",
]

# User persona seeds — each gives Claude a concrete human POV to generate from.
# The goal is to ground ideas in real pain, not abstract concept recombination.
PERSONA_SEEDS: list[dict] = [
    {
        "role": "CISO at a regional bank",
        "pain": "8-person security team, two audits per year, regulators who don't understand cloud. "
        "Any new tool must justify itself in a board slide.",
    },
    {
        "role": "PKI architect at a Fortune 500 migrating off a 15-year-old CA",
        "pain": "Thousands of certificates issued by an EOL CA, no inventory, half the cert owners "
        "have left the company. The board just approved PQC transition budget.",
    },
    {
        "role": "DevSecOps lead at a Series B startup with 18 engineers",
        "pain": "Moving fast, FedRAMP moderate in 6 months, no dedicated security team. "
        "Needs automation that works without a security hire.",
    },
    {
        "role": "Federal contractor under CMMC Level 2",
        "pain": "Contractually required to show continuous compliance evidence. "
        "Manual evidence collection is eating two weeks per quarter.",
    },
    {
        "role": "Red team lead at an MSSP",
        "pain": "Runs 40+ engagements a year across wildly different environments. "
        "Needs tooling that scales across clients without custom setup per engagement.",
    },
    {
        "role": "Embedded systems engineer at an automotive OEM",
        "pain": "ECU firmware must satisfy ISO 21434, V2X requires PQC-ready certs by 2027. "
        "Crypto libraries that work on 64KB RAM devices are almost nonexistent.",
    },
    {
        "role": "Open-source maintainer of a widely-used crypto library",
        "pain": "Constantly asked 'are you PQC ready?' with no good answer. "
        "Test coverage for algorithm edge cases is near zero. "
        "Users are production-deploying untested paths.",
    },
    {
        "role": "Network security engineer at a Tier-1 telecom",
        "pain": "5G core relies on TLS everywhere, certificate expiry causes P1 outages twice a year. "
        "HSM fleet spans 3 data centers and nobody has a full inventory.",
    },
    {
        "role": "Platform engineer at a cloud-native SaaS scaling from 10 to 500 services",
        "pain": "Service mesh cert rotation is manual, secrets are duplicated across 12 Kubernetes "
        "namespaces, and the on-call runbook hasn't been updated in 18 months.",
    },
    {
        "role": "Security researcher at an academic lab studying protocol attacks",
        "pain": "Needs reproducible environments to test TLS downgrade and side-channel attacks. "
        "Setting up a controlled PKI for experiments takes a week each time.",
    },
    {
        "role": "CTO at a healthcare SaaS startup",
        "pain": "HIPAA BAA with every customer, PHI in motion needs encryption audit trail, "
        "and the board just got spooked by a competitor's breach announcement.",
    },
    {
        "role": "IAM engineer at a global financial institution",
        "pain": "Certificate-based authentication for 80,000 employees, half still on smart cards. "
        "Every PQC upgrade requires touching every identity provider in the estate.",
    },
    {
        "role": "Vulnerability researcher doing independent CVE work",
        "pain": "Needs to quickly stand up test harnesses for protocol implementations to confirm "
        "whether a suspected bug is exploitable. Existing tooling assumes you know the target stack.",
    },
    {
        "role": "GRC analyst at a mid-market insurance company",
        "pain": "Maps controls manually across NIST CSF, SOC 2, and state insurance regs. "
        "A single framework change means weeks of spreadsheet rework.",
    },
    {
        "role": "Cloud security architect at a hyperscaler-adjacent ISV",
        "pain": "Customers demand FIPS-validated crypto in multi-tenant environments. "
        "Key isolation between tenants is a compliance requirement, not a nice-to-have.",
    },
]


__all__ = ["COMBINATORIC_TEMPLATES", "CONTRARIAN_PROMPTS", "PERSONA_SEEDS"]
