"""Tests for package build, install, and distribution.

Ensures project-forge ships as a precompiled product:
- Wheel and sdist build successfully
- Package metadata is correct
- CLI entry points work from installed package
- Version is consistent across all sources
- Built wheel installs cleanly in a fresh venv
"""

import re
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

        # The wheel filename must carry the real package version — proves the
        # dynamic version resolution in pyproject.toml works end-to-end.
        from project_forge import __version__

        expected = f"project_forge-{__version__}-py3-none-any.whl"
        assert wheels[0].name == expected, f"Wheel {wheels[0].name} != expected {expected}"

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

    def test_version_single_source_of_truth(self):
        """pyproject.toml must derive its version from project_forge.__version__.

        No hardcoded version literal is allowed in pyproject.toml — the one
        canonical declaration lives in src/project_forge/__init__.py.
        """
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        assert "version" not in config["project"], (
            "pyproject.toml hardcodes a version — it must use dynamic = ['version'] "
            "resolved from project_forge.__version__"
        )
        assert "version" in config["project"].get("dynamic", []), "version missing from [project] dynamic"
        attr = config["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        assert attr == "project_forge.__version__", f"dynamic version reads {attr!r}"

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


class TestVersionConsistency:
    """Every surface that states a version must agree with __version__.

    Guards issue #82: the package sat at 0.1.0 while the product shipped
    v0.17 because nothing compared the declared versions to each other.
    """

    def test_version_is_semver(self):
        from project_forge import __version__

        assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), f"__version__ {__version__!r} is not X.Y.Z"

    def test_readme_badge_matches_version(self):
        """The README shields.io badge must show __version__'s major.minor."""
        from project_forge import __version__

        readme = (PROJECT_ROOT / "README.md").read_text()
        m = re.search(r"img\.shields\.io/badge/version-(\d+(?:\.\d+)*)-", readme)
        assert m, "README.md has no version badge"
        major_minor = ".".join(__version__.split(".")[:2])
        assert m.group(1) == major_minor, (
            f"README badge says version-{m.group(1)} but package is {major_minor} — bump the badge"
        )

    def test_fastapi_app_version_matches(self):
        """The dashboard's FastAPI metadata must derive from __version__."""
        from project_forge import __version__
        from project_forge.web.app import app

        assert app.version == __version__, f"FastAPI app.version {app.version!r} != __version__ {__version__!r}"


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
