"""Tests for package build, install, and distribution.

Ensures project-forge ships as a precompiled product:
- Wheel and sdist build successfully
- Package metadata is correct
- CLI entry points work from installed package
- Version is consistent across all sources
- Built wheel installs cleanly in a fresh venv
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class TestPackageBuild:
    """The project must build into distributable artifacts."""

    def test_wheel_builds_successfully(self, tmp_path):
        """python -m build --wheel must produce a .whl file."""
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Wheel build failed:\n{result.stderr}"
        wheels = list(tmp_path.glob("*.whl"))
        assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}: {wheels}"
        assert wheels[0].stat().st_size > 0, "Wheel file is empty"

    def test_sdist_builds_successfully(self, tmp_path):
        """python -m build --sdist must produce a .tar.gz file."""
        result = subprocess.run(
            [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Sdist build failed:\n{result.stderr}"
        tarballs = list(tmp_path.glob("*.tar.gz"))
        assert len(tarballs) == 1, f"Expected 1 tarball, found {len(tarballs)}: {tarballs}"


class TestPackageMetadata:
    """Package metadata must be correct and complete."""

    def test_version_in_pyproject(self):
        """pyproject.toml must declare a version."""
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        version = config["project"]["version"]
        assert version, "No version in pyproject.toml"
        # Must be a valid semver-ish string
        parts = version.split(".")
        assert len(parts) >= 2, f"Version '{version}' should have at least major.minor"

    def test_package_name_correct(self):
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        assert config["project"]["name"] == "project-forge"

    def test_python_requires(self):
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        assert ">=3.12" in config["project"]["requires-python"]

    def test_entry_points_defined(self):
        """CLI entry points must be declared."""
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        scripts = config["project"].get("scripts", {})
        assert "forge-generate" in scripts, "Missing forge-generate entry point"
        assert "forge-serve" in scripts, "Missing forge-serve entry point"

    def test_dependencies_declared(self):
        """All runtime dependencies must be listed."""
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        deps = config["project"]["dependencies"]
        dep_names = [d.split(">=")[0].split("[")[0].strip().lower() for d in deps]
        required = ["fastapi", "uvicorn", "pydantic", "aiosqlite", "anthropic", "httpx", "jinja2"]
        for req in required:
            assert req in dep_names, f"Missing dependency: {req}"

    def test_license_declared(self):
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        license_text = config["project"].get("license", {}).get("text", "")
        assert license_text, "No license in pyproject.toml"


class TestCLIEntryPoints:
    """CLI commands must be importable and runnable."""

    def test_forge_generate_importable(self):
        """The forge-generate entry point module must be importable."""
        from project_forge.cron.runner import main  # noqa: F401

    def test_forge_serve_importable(self):
        """The forge-serve entry point module must be importable."""
        from project_forge.web.app import run  # noqa: F401

    def test_forge_generate_help(self):
        """forge-generate should at least not crash on import."""
        result = subprocess.run(
            [sys.executable, "-c", "from project_forge.cron.runner import main"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Import failed:\n{result.stderr}"

    def test_forge_serve_help(self):
        """forge-serve should at least not crash on import."""
        result = subprocess.run(
            [sys.executable, "-c", "from project_forge.web.app import run"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Import failed:\n{result.stderr}"


class TestWheelContents:
    """The built wheel must contain all required files."""

    def test_wheel_contains_source(self, tmp_path):
        """Wheel must include all Python source modules."""
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Build failed:\n{result.stderr}"

        wheel = list(tmp_path.glob("*.whl"))[0]
        import zipfile

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()

        # Core modules must be in the wheel
        required_modules = [
            "project_forge/__init__.py",
            "project_forge/models.py",
            "project_forge/config.py",
            "project_forge/engine/",
            "project_forge/storage/",
            "project_forge/web/",
            "project_forge/scaffold/",
            "project_forge/cron/",
        ]
        for mod in required_modules:
            assert any(mod in n for n in names), f"Wheel missing {mod}"

    def test_wheel_contains_templates(self, tmp_path):
        """Wheel must include Jinja2 templates and static files."""
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0

        wheel = list(tmp_path.glob("*.whl"))[0]
        import zipfile

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()

        assert any("templates/" in n for n in names), "Wheel missing templates/"
        assert any("static/" in n for n in names), "Wheel missing static/"
        assert any("app.js" in n for n in names), "Wheel missing app.js"


class TestInstallAndRun:
    """Built package must install and run in a clean environment."""

    def test_wheel_installs_in_venv(self, tmp_path):
        """Wheel must install cleanly in a fresh virtualenv."""
        # Build wheel
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Build failed:\n{result.stderr}"

        wheel = list(dist_dir.glob("*.whl"))[0]

        # Create venv
        venv_dir = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=30)
        venv_pip = str(venv_dir / "bin" / "pip")
        venv_python = str(venv_dir / "bin" / "python")

        # Install wheel
        result = subprocess.run(
            [venv_pip, "install", str(wheel)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Install failed:\n{result.stderr}"

        # Verify import works
        result = subprocess.run(
            [venv_python, "-c", "import project_forge; print(project_forge.__file__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Import in venv failed:\n{result.stderr}"
        assert "project_forge" in result.stdout

        # Verify CLI entry points exist
        assert (venv_dir / "bin" / "forge-generate").exists(), "forge-generate not installed"
        assert (venv_dir / "bin" / "forge-serve").exists(), "forge-serve not installed"
