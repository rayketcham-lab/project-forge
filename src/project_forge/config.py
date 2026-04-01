"""Application configuration via pydantic BaseSettings."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "FORGE_"}

    db_path: Path = Path("data/forge.db")
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 55443
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    auto_scaffold_threshold: float = 0.7
    github_owner: str = "rayketcham-lab"
    github_org: str = "rayketcham-lab"
    github_personal: str = "rayketcham"
    github_repo: str = "project-forge"
    log_level: str = "INFO"
    expand_ideas_per_run: int = 2
    expand_cross_weight: float = 0.7


settings = Settings()
