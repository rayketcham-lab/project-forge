"""TDD: idea-detail Engine Tools render structured HTML, not a raw JSON dump.

User report: clicking "GTM Brief" (and "Build Estimate") on an idea page
dumped the API response as ``JSON.stringify(obj, null, 2)`` into a ``<pre>``
— every field ran together as unformatted machine output.

These tests pin the contract without needing a JS test runner:

  * no raw JSON dump in the renderer;
  * every key the launchpad/recruiter engines emit is referenced by the
    renderer (so a new engine field can't be silently swallowed);
  * unknown keys still get rendered generically;
  * CSP-safety is preserved (DOM APIs only, no innerHTML);
  * every ``et-*`` class the JS applies actually exists in style.css;
  * the result container is a block element, not a ``<pre>``, and the
    cache-buster on the script tag was bumped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "src" / "project_forge" / "web"
IDEA_TOOLS_JS = WEB / "static" / "idea_tools.js"
STYLE_CSS = WEB / "static" / "style.css"
IDEA_DETAIL_HTML = WEB / "templates" / "idea_detail.html"

# Keys returned by engine.recruiter.estimate_build (documented in its docstring).
ESTIMATE_KEYS = frozenset(
    {
        "roles",
        "total_person_weeks",
        "skills",
        "cost_band",
        "complexity",
        "timeline_weeks",
    }
)


@pytest.fixture(scope="module")
def js() -> str:
    return IDEA_TOOLS_JS.read_text()


@pytest.fixture(scope="module")
def css() -> str:
    return STYLE_CSS.read_text()


@pytest.fixture(scope="module")
def html() -> str:
    return IDEA_DETAIL_HTML.read_text()


# --------------------------------------------------------------------------- #
# No raw JSON dumps                                                           #
# --------------------------------------------------------------------------- #


def test_renderer_does_not_dump_raw_json(js: str) -> None:
    """The bug: JSON.stringify was the entire renderer."""
    assert "JSON.stringify" not in js


def test_result_container_is_not_a_pre_block(html: str) -> None:
    """A <pre> forces monospace preformatted text — structured output needs a block."""
    assert not re.search(r"<pre[^>]*id=[\"']engine-tools-result[\"']", html)
    assert re.search(r"<div[^>]*id=[\"']engine-tools-result[\"']", html)


def test_script_cache_buster_bumped(html: str) -> None:
    m = re.search(r"idea_tools\.js\?v=(\d+)", html)
    assert m, "idea_tools.js must be loaded with a ?v= cache-buster"
    assert int(m.group(1)) >= 2, "bump the cache-buster so browsers pick up the new renderer"


# --------------------------------------------------------------------------- #
# Field coverage                                                              #
# --------------------------------------------------------------------------- #


def test_every_gtm_brief_key_is_rendered(js: str) -> None:
    from project_forge.engine.launchpad import _BRIEF_KEYS

    missing = sorted(key for key in _BRIEF_KEYS if key not in js)
    assert not missing, f"GTM brief keys never referenced by the renderer: {missing}"


def test_every_build_estimate_key_is_rendered(js: str) -> None:
    missing = sorted(key for key in ESTIMATE_KEYS if key not in js)
    assert not missing, f"build-estimate keys never referenced by the renderer: {missing}"


def test_role_rows_are_rendered_as_a_table(js: str) -> None:
    assert "createElement" in js
    assert "'table'" in js or '"table"' in js


def test_backend_provenance_is_surfaced(js: str) -> None:
    assert "_backend" in js


def test_unknown_keys_are_not_silently_dropped(js: str) -> None:
    """New engine fields must show up even before the renderer knows them."""
    assert "Object.keys" in js


# --------------------------------------------------------------------------- #
# CSP safety + styling contract                                               #
# --------------------------------------------------------------------------- #


def test_renderer_is_csp_safe(js: str) -> None:
    assert "innerHTML" not in js
    assert "outerHTML" not in js
    assert "document.write" not in js


def test_every_et_class_used_by_js_exists_in_css(js: str, css: str) -> None:
    used = set(re.findall(r"\bet-[a-z0-9-]+", js))
    assert used, "renderer should apply et-* classes"
    missing = sorted(name for name in used if f".{name}" not in css)
    assert not missing, f"et-* classes applied by JS but never styled: {missing}"
