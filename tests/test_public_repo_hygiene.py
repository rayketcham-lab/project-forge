"""This repo is public: shipped code must not carry absolute operator paths.

`/opt/vmdata/project-forge` was hardcoded as the `cwd` for the in-app issue
reporter, so `gh` ran in a directory that exists only on the author's machine.
Everywhere else the subprocess raised FileNotFoundError into a bare `except`
and the feature silently did nothing — while the path itself advertised the
server layout and the account name to everyone reading the source.

Version-badge drift is covered by tests/test_packaging.py and
tests/test_readme_accuracy.py, which pin the badge to major.minor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "project_forge"

# Paths that only exist on the machine this project happens to run on.
OPERATOR_PATHS = re.compile(r"/opt/vmdata/|/opt/project-forge/|/home/claude/")


def _shipped_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in str(p))


@pytest.mark.parametrize("path", _shipped_modules(), ids=lambda p: str(p.name))
def test_no_hardcoded_operator_paths(path: Path) -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if OPERATOR_PATHS.search(line)
    ]
    assert not offenders, (
        "hardcoded operator path in shipped code — derive it instead "
        "(`Path(__file__).resolve().parents[3]`, `shutil.which`, or a setting):\n" + "\n".join(offenders)
    )
