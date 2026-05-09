"""Static introspection — generate self-improvement proposals without an API key.

Analyzes the codebase locally to find untested modules, large files, and other
improvement opportunities, then produces Idea objects from the findings.
"""

import ast
import re
from pathlib import Path

from project_forge.models import Idea, IdeaCategory

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _extract_module_details(py_file: Path) -> dict:
    """Parse a Python file and extract docstring, function names, and class names."""
    try:
        source = py_file.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return {"docstring": None, "functions": [], "classes": []}

    docstring = ast.get_docstring(tree)
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    ]
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]
    return {"docstring": docstring, "functions": functions, "classes": classes}


def find_untested_modules(project_root: Path) -> list[dict]:
    """Find source modules that have no corresponding test file.

    Returns a list of dicts with 'module' (stem), 'path' (relative str),
    'functions' (list of public function names), 'classes' (list of public
    class names), and 'docstring' (module docstring or None).
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
            details = _extract_module_details(py_file)
            findings.append(
                {
                    "module": module_stem,
                    "path": str(rel),
                    "functions": details["functions"],
                    "classes": details["classes"],
                    "docstring": details["docstring"],
                }
            )

    return findings


def _largest_top_level_blocks(py_file: Path, top_n: int = 5) -> list[dict]:
    """Return the largest top-level functions/classes in a file by line span.

    Used by the "Decompose X" proposal to suggest concrete split candidates
    instead of a generic "this file is big" message.
    """
    try:
        source = py_file.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    blocks: list[dict] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        span = end_lineno - node.lineno + 1
        kind = "class" if isinstance(node, ast.ClassDef) else "def"
        blocks.append({"name": node.name, "kind": kind, "lines": span})

    blocks.sort(key=lambda b: -b["lines"])
    return blocks[:top_n]


def find_large_files(project_root: Path, threshold: int = 300) -> list[dict]:
    """Find source files exceeding *threshold* lines.

    Returns a list of dicts with 'path' (relative str), 'lines' (int), and
    'blocks' (list of dicts: top-N largest top-level functions/classes by
    line span — the natural decomposition seams).
    """
    src_dir = project_root / "src" / "project_forge"
    if not src_dir.exists():
        return []

    findings: list[dict] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        line_count = len(py_file.read_text().splitlines())
        if line_count > threshold:
            rel = py_file.relative_to(project_root)
            findings.append({
                "path": str(rel),
                "lines": line_count,
                "blocks": _largest_top_level_blocks(py_file, top_n=5),
            })

    return findings


def _complexity_score(functions: list[str], classes: list[str]) -> float:
    """Compute feasibility score based on module complexity.

    Simpler modules (fewer functions/classes) are easier to test → higher score.
    """
    total = len(functions) + len(classes)
    if total == 0:
        return 0.95
    if total <= 3:
        return 0.9
    if total <= 8:
        return 0.8
    if total <= 15:
        return 0.7
    return 0.6


def _build_test_suggestions(module: str, functions: list[str], classes: list[str]) -> str:
    """Build specific test case suggestions for the module."""
    suggestions = []
    for fn in functions[:6]:
        suggestions.append(f"test_{fn}()")
    for cls in classes[:4]:
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", cls).lower()
        suggestions.append(f"test_{snake}_init()")
    if not suggestions:
        return f"Create tests/test_{module}.py with basic import and smoke tests."
    cases = ", ".join(suggestions)
    return f"Create tests/test_{module}.py with cases: {cases}"


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

        functions = finding["functions"]
        classes = finding["classes"]
        docstring = finding["docstring"]
        score = _complexity_score(functions, classes)

        # Build a rich description
        parts = []
        if docstring:
            first_line = docstring.split("\n")[0].strip().rstrip(".")
            parts.append(f"Module purpose: {first_line}.")
        else:
            parts.append(f"The module {finding['module']} at {finding['path']} has no test coverage.")

        if functions:
            fn_list = ", ".join(f"def {f}()" for f in functions[:8])
            parts.append(f"Public functions: {fn_list}.")
        if classes:
            cls_list = ", ".join(f"class {c}" for c in classes[:5])
            parts.append(f"Classes: {cls_list}.")

        total = len(functions) + len(classes)
        if total > 0:
            parts.append(f"Complexity: {total} public symbols.")

        description = " ".join(parts)
        mvp_scope = _build_test_suggestions(finding["module"], functions, classes)

        proposals.append(
            Idea(
                name=name,
                tagline=f"Missing test coverage for {finding['module']}",
                description=description,
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis="Internal quality improvement — reduces bug risk.",
                feasibility_score=score,
                mvp_scope=mvp_scope,
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

        blocks = finding.get("blocks") or []
        # Build a richer description: name the actual largest blocks (the
        # natural split candidates) instead of a generic "it's big" message.
        desc_parts = [
            f"{finding['path']} is {finding['lines']} lines — past the 300-line "
            f"split threshold."
        ]
        if blocks:
            top_lines = ", ".join(
                f"{b['kind']} {b['name']} ({b['lines']} lines)"
                for b in blocks[:3]
            )
            desc_parts.append(
                f"Largest top-level blocks (natural split candidates): {top_lines}.",
            )
            biggest_share = blocks[0]["lines"] / finding["lines"]
            if biggest_share >= 0.40:
                desc_parts.append(
                    f"{blocks[0]['kind']} {blocks[0]['name']} alone is "
                    f"{biggest_share:.0%} of the file — likely its own module.",
                )
        desc_parts.append(
            "Splitting reduces cognitive load and makes future edits less risky.",
        )
        description = " ".join(desc_parts)

        # Concrete mvp_scope: name the suggested new files.
        if blocks:
            split_targets = ", ".join(
                f"{Path(finding['path']).parent}/{b['name'].lower()}.py"
                for b in blocks[:3]
            )
            mvp_scope = (
                f"Extract the top blocks ({', '.join(b['name'] for b in blocks[:3])}) "
                f"into separate modules — e.g. {split_targets}. Update imports "
                f"in callers; keep public API unchanged."
            )
        else:
            mvp_scope = (
                f"Identify logical sections in {finding['path']} and split into "
                f"smaller modules with clear responsibilities."
            )

        proposals.append(
            Idea(
                name=name,
                tagline=f"{finding['path']} has {finding['lines']} lines",
                description=description,
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis="Internal quality improvement — reduces cognitive load.",
                feasibility_score=0.7,
                mvp_scope=mvp_scope,
                tech_stack=["python"],
            )
        )

    return proposals
