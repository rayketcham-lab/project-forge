"""Seed the database with example ideas for demo purposes."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from project_forge.models import Idea, IdeaCategory  # noqa: E402
from project_forge.storage.db import Database  # noqa: E402

DEMO_IDEAS = [
    Idea(
        name="Ghost Keys",
        tagline="Detect orphaned API keys across your entire infrastructure",
        description=(
            "Ghost Keys scans your infrastructure for API keys that are still active but no longer "
            "used by any service. It integrates with cloud providers, secret managers, and application "
            "logs to build a dependency graph of key usage. Keys with no recent activity get flagged "
            "for rotation or revocation.\n\n"
            "Most organizations have hundreds of API keys that nobody remembers creating. "
            "Each one is a potential attack vector waiting to be exploited."
        ),
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis=(
            "API key sprawl is a growing problem as organizations adopt more SaaS tools and "
            "microservices. Existing secret scanners find exposed keys but don't track usage. "
            "This fills the gap between secret detection and secret lifecycle management."
        ),
        feasibility_score=0.85,
        mvp_scope=(
            "CLI tool that scans AWS IAM, GitHub tokens, and common secret managers. "
            "Reports unused keys older than 30 days. No rotation in MVP, just detection."
        ),
        tech_stack=["python", "boto3", "click", "sqlite"],
    ),
    Idea(
        name="Drift Sentinel",
        tagline="Real-time infrastructure drift detection with auto-fix suggestions",
        description=(
            "Drift Sentinel continuously monitors your infrastructure state against your IaC "
            "definitions and alerts when drift occurs. Unlike terraform plan which is point-in-time, "
            "this runs continuously and catches manual changes, failed deployments, and config drift "
            "the moment they happen.\n\n"
            "It generates fix suggestions as PR-ready IaC patches, not just alerts."
        ),
        category=IdeaCategory.DEVOPS_TOOLING,
        market_analysis=(
            "Infrastructure drift costs teams hours of debugging when production diverges from code. "
            "Existing tools detect drift but don't help fix it. The auto-fix-as-PR approach is novel."
        ),
        feasibility_score=0.78,
        mvp_scope=(
            "Agent that polls Terraform state vs actual AWS resources every 5 minutes. "
            "Generates diff reports and creates PRs with suggested .tf fixes."
        ),
        tech_stack=["python", "terraform", "boto3", "fastapi"],
    ),
    Idea(
        name="Consent Mesh",
        tagline="Distributed consent management for microservice architectures",
        description=(
            "Consent Mesh provides a sidecar-style consent enforcement layer for microservices. "
            "When user consent is granted or revoked, it propagates across all services in real-time "
            "via an event mesh. Each service checks consent before processing PII.\n\n"
            "GDPR and CCPA require consent propagation but most architectures have no mechanism for it."
        ),
        category=IdeaCategory.PRIVACY,
        market_analysis=(
            "Privacy regulations are getting stricter globally. Most consent management is centralized "
            "and can't keep up with distributed architectures. A mesh approach is the right pattern."
        ),
        feasibility_score=0.72,
        mvp_scope=(
            "Go sidecar that intercepts HTTP requests, checks consent status from a central store, "
            "and blocks or allows based on consent scope. REST API for consent CRUD."
        ),
        tech_stack=["go", "redis", "grpc", "protobuf"],
    ),
    Idea(
        name="Pipeline Profiler",
        tagline="Find and fix the slowest steps in your CI/CD pipeline",
        description=(
            "Pipeline Profiler analyzes your GitHub Actions (or GitLab/Jenkins) workflow runs to "
            "identify bottlenecks, cache misses, redundant steps, and parallelization opportunities. "
            "It generates actionable recommendations with estimated time savings.\n\n"
            "Most teams accept slow CI as inevitable. This tool shows exactly where time is wasted."
        ),
        category=IdeaCategory.AUTOMATION,
        market_analysis=(
            "CI/CD costs are rising fast. A 10-minute pipeline running 50 times a day wastes "
            "8+ hours of developer wait time daily. Tools that cut this are immediately valuable."
        ),
        feasibility_score=0.88,
        mvp_scope=(
            "GitHub App that analyzes workflow run logs, generates a flamegraph-style visualization "
            "of step durations, and suggests optimizations via PR comments."
        ),
        tech_stack=["python", "fastapi", "github-api", "d3.js"],
    ),
    Idea(
        name="SBOM Watcher",
        tagline="Continuous SBOM generation with real-time vulnerability tracking",
        description=(
            "SBOM Watcher generates Software Bill of Materials on every commit and continuously "
            "monitors all dependencies for new CVEs. When a vulnerability is disclosed, it instantly "
            "identifies which of your projects are affected and creates prioritized fix PRs.\n\n"
            "Current SBOM tools are point-in-time snapshots. This is a living, breathing SBOM."
        ),
        category=IdeaCategory.COMPLIANCE,
        market_analysis=(
            "SBOM requirements are becoming law (US Executive Order, EU CRA). Most tools generate "
            "static SBOMs. A continuous, reactive SBOM with auto-remediation is what the market needs."
        ),
        feasibility_score=0.82,
        mvp_scope=(
            "GitHub Action that generates CycloneDX SBOM on push, stores versions, "
            "and polls NVD/OSV for new CVEs affecting listed components. Slack alerts + PR creation."
        ),
        tech_stack=["python", "cyclonedx", "github-actions", "sqlite"],
    ),
]


async def main():
    db = Database(Path("/opt/project-forge/data/forge.db"))
    await db.connect()
    for idea in DEMO_IDEAS:
        await db.save_idea(idea)
        print(f"Seeded: {idea.name} ({idea.category.value}, score: {idea.feasibility_score})")
    await db.close()
    print(f"\nSeeded {len(DEMO_IDEAS)} demo ideas. Visit http://localhost:55443")


if __name__ == "__main__":
    asyncio.run(main())
