"""Static introspection — generate self-improvement proposals without an API key.

Analyzes the codebase locally to find untested modules, large files, and other
improvement opportunities, then produces Idea objects from the findings.
"""

from pathlib import Path

from project_forge.models import Idea, IdeaCategory

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def find_untested_modules(project_root: Path) -> list[dict]:
    """Find source modules that have no corresponding test file.

    Returns a list of dicts with 'module' (stem) and 'path' (relative str).
    """
    src_dir = project_root / "src" / "project_forge"
    tests_dir = project_root / "tests"

    if not src_dir.exists() or not tests_dir.exists():
        return []

    # Collect all test file stems (e.g. test_generator -> generator)
    test_stems: set[str] = set()
    for tf in tests_dir.glob("test_*.py"):
        test_stems.add(tf.stem.removeprefix("test_"))

    findings: list[dict] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        module_stem = py_file.stem
        if module_stem not in test_stems:
            rel = py_file.relative_to(project_root)
            findings.append({"module": module_stem, "path": str(rel)})

    return findings


def find_large_files(project_root: Path, threshold: int = 300) -> list[dict]:
    """Find source files exceeding *threshold* lines.

    Returns a list of dicts with 'path' (relative str) and 'lines' (int).
    """
    src_dir = project_root / "src" / "project_forge"
    if not src_dir.exists():
        return []

    findings: list[dict] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        line_count = len(py_file.read_text().splitlines())
        if line_count > threshold:
            rel = py_file.relative_to(project_root)
            findings.append({"path": str(rel), "lines": line_count})

    return findings


def generate_static_proposals(project_root: Path | None = None) -> list[Idea]:
    """Produce Idea objects from static analysis of the codebase.

    This is the API-key-free alternative to LLM-based introspection.
    """
    root = project_root or _PROJECT_ROOT
    proposals: list[Idea] = []
    seen_names: set[str] = set()

    # --- Untested modules ---
    untested = find_untested_modules(root)
    for finding in untested:
        name = f"Add tests for {finding['module']}"
        if name in seen_names:
            continue
        seen_names.add(name)
        proposals.append(
            Idea(
                name=name,
                tagline=f"Missing test coverage for {finding['module']}",
                description=(
                    f"The source module at {finding['path']} has no corresponding "
                    f"test file. Adding tests improves reliability and catches regressions."
                ),
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis="Internal quality improvement — reduces bug risk.",
                feasibility_score=0.9,
                mvp_scope=f"Create tests/test_{finding['module']}.py covering the public API of src/{finding['path']}",
                tech_stack=["python", "pytest"],
            )
        )

    # --- Large files ---
    large = find_large_files(root)
    for finding in large:
        name = f"Decompose {Path(finding['path']).stem}"
        if name in seen_names:
            continue
        seen_names.add(name)
        proposals.append(
            Idea(
                name=name,
                tagline=f"{finding['path']} has {finding['lines']} lines",
                description=(
                    f"The file {finding['path']} is {finding['lines']} lines long. "
                    f"Decomposing it into smaller, focused modules improves readability "
                    f"and maintainability."
                ),
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis="Internal quality improvement — reduces cognitive load.",
                feasibility_score=0.7,
                mvp_scope=f"Split {finding['path']} into smaller modules with clear responsibilities.",
                tech_stack=["python"],
            )
        )

    return proposals
