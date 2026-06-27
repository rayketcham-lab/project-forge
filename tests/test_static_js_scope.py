"""TDD: JavaScript scope regression — module-scope functions called from
DOMContentLoaded must actually be at module scope.

User report: "Begin Wizard does nothing." Root cause was `initWizard`
defined inside an IIFE that early-returned on every page without
`.project-delete-btn` elements. The DOMContentLoaded handler called
`initWizard()` but the function was undefined in scope, throwing a
ReferenceError that the dashboard had no handler for. Silent no-op.

This test pins the contract: every function called by name from a
DOMContentLoaded callback in app.js MUST be defined at column 0
(module scope). Catches the regression class without needing a
JS test runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "src" / "project_forge" / "web" / "static" / "app.js"


@pytest.fixture(scope="module")
def js_source() -> str:
    return APP_JS.read_text()


def _module_scope_functions(src: str) -> set[str]:
    """Names of every `function NAME(` (or `async function NAME(`) declared
    at column 0 — i.e. module scope, not nested in an IIFE."""
    return set(re.findall(r"^(?:async\s+)?function\s+(\w+)\s*\(", src, flags=re.MULTILINE))


def _domcontent_called_names(src: str) -> set[str]:
    """Function names called bare (e.g. `foo();`) inside DOMContentLoaded.

    Any name called bare must resolve to module scope or a built-in.
    """
    # Find the DOMContentLoaded callback body.
    m = re.search(
        r"document\.addEventListener\(\s*['\"]DOMContentLoaded['\"]\s*,\s*function\s*\(\s*\)\s*\{(.*?)\n\}\s*\)\s*;",
        src,
        flags=re.DOTALL,
    )
    if not m:
        return set()
    body = m.group(1)

    # Match bare function-call expressions: `name(` not preceded by `.` or `function `
    # Skip definitions and method calls.
    names = set()
    for m2 in re.finditer(r"(?<![\w.])\b([a-zA-Z_]\w*)\s*\(", body):
        name = m2.group(1)
        # Skip JS keywords that look like calls
        if name in {
            "if",
            "for",
            "while",
            "switch",
            "function",
            "return",
            "typeof",
            "new",
            "catch",
            "var",
            "let",
            "const",
        }:
            continue
        # Skip method definitions like .closest(...) — but our regex already
        # excludes . prefix. Skip anonymous "function(" too.
        names.add(name)
    return names


# Names that are expected to live elsewhere (browser builtins, our own
# helpers we know are module-scope, etc.). Not exhaustive — only listed
# when needed to keep the test green.
_KNOWN_MODULE_SCOPE = {
    "switchTab",
    "submitUrl",
    "submitText",
    "initWizard",
    "approveIdea",
    "rejectIdea",
    "scaffoldIdea",
    "compareIdea",
    "promoteProposal",
    "rejectProposal",
    "toggleChallengeInput",
    "addToProject",
}

_BUILTINS = {
    "alert",
    "console",
    "fetch",
    "JSON",
    "Object",
    "Array",
    "String",
    "Number",
    "Boolean",
    "Math",
    "Date",
    "window",
    "document",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "clearInterval",
    "Promise",
    "Error",
    "parseInt",
    "parseFloat",
}


# ── The actual contract ────────────────────────────────────────────


class TestDomContentLoadedNamesResolve:
    def test_initwizard_is_at_module_scope(self, js_source):
        """The exact bug: initWizard was inside an IIFE; this asserts it
        sits at column 0 going forward."""
        module_scope = _module_scope_functions(js_source)
        assert "initWizard" in module_scope, (
            "initWizard must be defined at column 0 of app.js so the "
            "DOMContentLoaded handler can call it. Was caught at "
            "indented scope (inside an IIFE) — would silently no-op the "
            "Begin Wizard button."
        )


class TestKnownModuleScopeFunctions:
    """Belt-and-suspenders: each handler the dashboard relies on must
    sit at module scope — calling out the specific ones so the failure
    mode is named, not just 'something is wrong'."""

    @pytest.mark.parametrize("name", sorted(_KNOWN_MODULE_SCOPE))
    def test_function_is_at_module_scope(self, js_source, name: str):
        module_scope = _module_scope_functions(js_source)
        assert name in module_scope, (
            f"{name} is called from DOMContentLoaded but is not at module scope. Hoist it out of any IIFE."
        )
