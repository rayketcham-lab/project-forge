"""Core data models for Project Forge."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class IdeaCategory(StrEnum):
    SECURITY_TOOL = "security-tool"
    MARKET_GAP = "market-gap"
    VULNERABILITY_RESEARCH = "vulnerability-research"
    AUTOMATION = "automation"
    DEVOPS_TOOLING = "devops-tooling"
    PRIVACY = "privacy"
    COMPLIANCE = "compliance"
    OBSERVABILITY = "observability"
    PQC_CRYPTOGRAPHY = "pqc-cryptography"
    NIST_STANDARDS = "nist-standards"
    RFC_SECURITY = "rfc-security"
    CRYPTO_INFRASTRUCTURE = "crypto-infrastructure"


IdeaStatus = Literal["new", "approved", "scaffolded", "rejected", "archived"]


class Idea(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    tagline: str
    description: str
    category: IdeaCategory
    market_analysis: str
    feasibility_score: float = Field(ge=0.0, le=1.0)
    mvp_scope: str
    tech_stack: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: IdeaStatus = "new"
    github_issue_url: str | None = None
    project_repo_url: str | None = None


class ScaffoldSpec(BaseModel):
    idea_id: str
    repo_name: str
    language: Literal["python", "node", "rust", "go"]
    framework: str | None = None
    features: list[str] = Field(default_factory=lambda: ["ci", "tests", "readme"])
    initial_issues: list[dict] = Field(default_factory=list)


class GenerationRun(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    category: IdeaCategory
    idea_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    success: bool = False
    error: str | None = None
