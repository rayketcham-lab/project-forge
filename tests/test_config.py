"""Tests for `project_forge.config` — fixes #48 and #73.

The pre-existing tests covered the env-file loading path. This file
covers the public surface of the Settings class itself and the new
field validators (#73): out-of-range values raise at construction
instead of silently misconfiguring downstream behaviour.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from project_forge.config import Settings


class TestUnitWeightValidators:
    """auto_scaffold_threshold and expand_cross_weight must live in [0,1]."""

    def test_default_values_are_valid(self):
        s = Settings()
        assert 0.0 <= s.auto_scaffold_threshold <= 1.0
        assert 0.0 <= s.expand_cross_weight <= 1.0

    @pytest.mark.parametrize("v", [0.0, 0.25, 0.5, 0.99, 1.0])
    def test_in_range_floats_accepted(self, v: float):
        Settings(auto_scaffold_threshold=v, expand_cross_weight=v)

    @pytest.mark.parametrize("v", [-0.1, -1.0, 1.01, 1.5, 2.0])
    def test_out_of_range_auto_scaffold_rejected(self, v: float):
        with pytest.raises(ValidationError):
            Settings(auto_scaffold_threshold=v)

    @pytest.mark.parametrize("v", [-0.3, 1.5, 100.0])
    def test_out_of_range_expand_cross_rejected(self, v: float):
        with pytest.raises(ValidationError):
            Settings(expand_cross_weight=v)


class TestPortValidator:
    @pytest.mark.parametrize("p", [1, 80, 8080, 55443, 65535])
    def test_in_range_ports_accepted(self, p: int):
        s = Settings(port=p)
        assert s.port == p

    @pytest.mark.parametrize("p", [0, -1, 65536, 99999])
    def test_out_of_range_ports_rejected(self, p: int):
        with pytest.raises(ValidationError):
            Settings(port=p)


class TestDefaults:
    """Lock down the public surface against accidental changes."""

    def test_db_path_default(self):
        from pathlib import Path
        s = Settings()
        assert isinstance(s.db_path, Path)

    def test_port_default(self):
        assert Settings().port == 55443

    def test_log_level_default(self):
        assert Settings().log_level == "INFO"

    def test_anthropic_model_default(self):
        # Sonnet 4 — the project-default reasoning model. Changing the
        # short alias here would silently change generation behaviour.
        assert Settings().anthropic_model == "claude-sonnet-4-6"

    def test_env_prefix_pattern(self):
        # The settings rely on FORGE_ prefix on every env var. Lock down
        # the config so a careless rename doesn't break every deployment.
        assert Settings.model_config["env_prefix"] == "FORGE_"


class TestEnvOverride:
    def test_env_can_override_in_range_floats(self, monkeypatch):
        monkeypatch.setenv("FORGE_AUTO_SCAFFOLD_THRESHOLD", "0.42")
        monkeypatch.setenv("FORGE_EXPAND_CROSS_WEIGHT", "0.13")
        s = Settings()
        assert s.auto_scaffold_threshold == 0.42
        assert s.expand_cross_weight == 0.13

    def test_env_out_of_range_float_raises(self, monkeypatch):
        monkeypatch.setenv("FORGE_AUTO_SCAFFOLD_THRESHOLD", "1.5")
        with pytest.raises(ValidationError):
            Settings()
