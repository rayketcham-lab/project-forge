"""Application configuration via pydantic BaseSettings."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "FORGE_", "env_file": ".env", "env_file_encoding": "utf-8"}

    db_path: Path = Path("data/forge.db")
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 55443
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    auto_scaffold_threshold: float = 0.7
    github_owner: str = "rayketcham-lab"
    github_org: str = "rayketcham-lab"
    github_personal: str = "rayketcham"
    github_repo: str = "project-forge"
    log_level: str = "INFO"
    expand_ideas_per_run: int = 2
    expand_cross_weight: float = 0.7
    api_token: str = ""
    # Host header allowlist. "*" accepts any Host, which is what makes DNS
    # rebinding work: a page on attacker.com re-points its own hostname at
    # this LAN address, and the browser then treats it as same-origin and can
    # read the dashboard token out of the HTML. Narrowing this to the names
    # you actually serve on breaks that. It defaults open because the right
    # value is deployment-specific and a wrong guess locks the operator out.
    # Comma-separated: FORGE_ALLOWED_HOSTS="localhost,127.0.0.1,forge.lan"
    allowed_hosts: str = "*"

    @property
    def allowed_hosts_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        return hosts or ["*"]

    # v0.24 Money Bots — where the operator can actually deploy capital.
    # Empty means "unspecified", and generation stays venue-agnostic. Set it
    # (FORGE_OPERATOR_JURISDICTION="United States") and both the generation
    # seed and the red team's legality lens judge against the real
    # jurisdiction instead of assuming. Without it, the panel correctly but
    # expensively kills strategies on eligibility grounds after the fact.
    operator_jurisdiction: str = ""

    # Fix #73 — out-of-range floats / ports were silently accepted, causing
    # subtle misbehavior (nothing ever scaffolded, cross-pollination ran
    # backwards, ports outside [1, 65535]). Enforce at construction.
    @field_validator("auto_scaffold_threshold", "expand_cross_weight")
    @classmethod
    def _validate_unit_weight(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"must be in [0.0, 1.0], got {v}")
        return v

    @field_validator("port")
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be in [1, 65535], got {v}")
        return v


settings = Settings()
