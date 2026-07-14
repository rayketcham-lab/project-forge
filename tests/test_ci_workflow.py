"""Tests for CI workflow hygiene.

Least-privilege guard (supersedes PR #50): every workflow must declare an
explicit permissions block so GITHUB_TOKEN never gets the broad write
default on the self-hosted runner. The gh CLI steps in ci.yml use the
runner host's ambient auth (no GH_TOKEN env) and tolerate failure, so a
read-only token cannot break them.
"""

from pathlib import Path

import yaml

_WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"


def _workflows() -> list[Path]:
    return sorted(_WORKFLOWS_DIR.glob("*.yml")) + sorted(_WORKFLOWS_DIR.glob("*.yaml"))


class TestWorkflowPermissions:
    def test_workflow_files_exist(self):
        assert _workflows(), "no workflow files found under .github/workflows/"

    def test_every_workflow_declares_permissions(self):
        for wf in _workflows():
            config = yaml.safe_load(wf.read_text())
            assert "permissions" in config, f"{wf.name} missing a top-level permissions block"

    def test_ci_workflow_is_least_privilege(self):
        config = yaml.safe_load((_WORKFLOWS_DIR / "ci.yml").read_text())
        assert config["permissions"] == {"contents": "read"}

    def test_workflows_parse_as_valid_yaml(self):
        for wf in _workflows():
            config = yaml.safe_load(wf.read_text())
            assert isinstance(config, dict), f"{wf.name} did not parse to a mapping"
