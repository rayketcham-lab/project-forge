"""NVD CVE feed parser (CVE 2.0 JSON API).

Fetches recent CVEs from https://services.nvd.nist.gov/rest/json/cves/2.0
and exposes them as feed items. Pure parser kept separate for testability.
"""

from __future__ import annotations

from typing import Any


def parse_nvd_response(payload: dict[str, Any]) -> list[dict]:
    """Convert an NVD CVE 2.0 response into feed items.

    Each item: {id, title, summary, url, ts}
    """
    items: list[dict] = []
    for vuln in payload.get("vulnerabilities") or []:
        cve = vuln.get("cve", {})
        cve_id = cve.get("id")
        if not cve_id:
            continue

        # Pick the English description if available
        descriptions = cve.get("descriptions") or []
        summary = ""
        for d in descriptions:
            if d.get("lang") == "en":
                summary = d.get("value", "")
                break
        if not summary and descriptions:
            summary = descriptions[0].get("value", "")

        items.append({
            "id": cve_id,
            "title": cve_id,
            "summary": summary,
            "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            "ts": cve.get("published", ""),
        })
    return items
