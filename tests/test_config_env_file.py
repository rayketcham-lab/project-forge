"""Tests for Settings loading API keys from .env files.

The self-improve runner fails when run manually because Settings doesn't
load .env files — only systemd's EnvironmentFile provides the vars.
Settings should auto-load .env so manual runs work too.
"""

import os
from pathlib import Path
from unittest.mock import patch


class TestSettingsEnvFile:
    """Settings should load API keys from .env files automatically."""

    def test_loads_api_key_from_env_file(self, tmp_path: Path):
        """Settings reads FORGE_ANTHROPIC_API_KEY from a .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("FORGE_ANTHROPIC_API_KEY=sk-test-from-dotenv\n")

        # Clear any existing env var so only .env is the source
        env = {k: v for k, v in os.environ.items() if "ANTHROPIC" not in k and "FORGE_" not in k}

        with patch.dict(os.environ, env, clear=True):
            from project_forge.config import Settings

            s = Settings(_env_file=str(env_file))
            assert s.anthropic_api_key == "sk-test-from-dotenv"

    def test_env_var_overrides_env_file(self, tmp_path: Path):
        """Explicit env var takes precedence over .env file value."""
        env_file = tmp_path / ".env"
        env_file.write_text("FORGE_ANTHROPIC_API_KEY=from-file\n")

        env = {k: v for k, v in os.environ.items() if "ANTHROPIC" not in k and "FORGE_" not in k}
        env["FORGE_ANTHROPIC_API_KEY"] = "from-env"

        with patch.dict(os.environ, env, clear=True):
            from project_forge.config import Settings

            s = Settings(_env_file=str(env_file))
            assert s.anthropic_api_key == "from-env"

    def test_missing_env_file_is_not_an_error(self):
        """Settings works fine if no .env file exists."""
        env = {k: v for k, v in os.environ.items() if "ANTHROPIC" not in k and "FORGE_" not in k}

        with patch.dict(os.environ, env, clear=True):
            from project_forge.config import Settings

            s = Settings(_env_file="/nonexistent/.env")
            assert s.anthropic_api_key == ""

    def test_default_env_file_path_is_dotenv(self):
        """Settings model_config should include env_file='.env'."""
        from project_forge.config import Settings

        cfg = Settings.model_config
        assert "env_file" in cfg, "Settings.model_config must declare env_file"
        assert cfg["env_file"] == ".env"
