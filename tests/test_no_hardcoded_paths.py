"""Regression guard: test files must not contain hardcoded absolute filesystem paths.

Absolute paths (e.g. /opt/, /home/, /Users/) tie tests to a specific dev box and
cause FileNotFoundError on any other runner. All paths must be constructed relative
to PROJECT_ROOT = Path(__file__).resolve().parent.parent or equivalent.
"""

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Patterns that indicate a hardcoded absolute path in a string literal.
# Match /opt/, /home/, or /Users/ inside a quoted string.
_ABS_PATH_PATTERN = re.compile(r"""["'](/opt/|/home/|/Users/)""")

# Allowlist: lines that contain these substrings are mock/stub values, not real
# filesystem access, and should not be flagged.
_MOCK_CONTEXTS = (
    "return_value=",
    "side_effect=",
    "patch(",
    "monkeypatch",
    "# mock",
    "# fake",
    "# stub",
)


def _collect_violations() -> list[str]:
    violations: list[str] = []
    for test_file in sorted(TESTS_DIR.glob("*.py")):
        text = test_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ABS_PATH_PATTERN.search(line) and not any(ctx in line for ctx in _MOCK_CONTEXTS):
                violations.append(f"{test_file.name}:{lineno}: {line.strip()}")
    return violations


def test_no_hardcoded_absolute_paths_in_tests():
    """No test file may contain a hardcoded absolute filesystem path literal.

    Use PROJECT_ROOT = Path(__file__).resolve().parent.parent and build paths
    relative to that, matching the pattern in test_dashboard_auth.py and
    test_csp_security.py.
    """
    violations = _collect_violations()
    assert not violations, (
        "Hardcoded absolute paths found in test files — use PROJECT_ROOT-relative paths:\n" + "\n".join(violations)
    )
