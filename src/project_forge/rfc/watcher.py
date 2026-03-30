"""RFC and IETF draft watcher — tracks new security-relevant publications."""

import logging
import xml.etree.ElementTree as ET

from project_forge.rfc.filters import SECURITY_WORKING_GROUPS
from project_forge.rfc.models import IETFDraft, RFCEntry

logger = logging.getLogger(__name__)


class RFCWatcher:
    def __init__(self):
        self.base_url = "https://www.rfc-editor.org"
        self.datatracker_url = "https://datatracker.ietf.org"
        self.security_groups = SECURITY_WORKING_GROUPS

    def parse_rfc_xml(self, xml_content: str) -> list[RFCEntry]:
        """Parse RFC index XML into RFCEntry objects."""
        entries = []
        root = ET.fromstring(xml_content)

        for rfc_elem in root.findall(".//rfc-entry"):
            doc_id_elem = rfc_elem.find("doc-id")
            if doc_id_elem is None or doc_id_elem.text is None:
                continue

            doc_id = doc_id_elem.text
            if not doc_id.startswith("RFC"):
                continue

            number = int(doc_id.replace("RFC", ""))
            title = ""
            title_elem = rfc_elem.find("title")
            if title_elem is not None and title_elem.text:
                title = title_elem.text

            authors = []
            for author_elem in rfc_elem.findall(".//author/name"):
                if author_elem.text:
                    authors.append(author_elem.text)

            status = ""
            status_elem = rfc_elem.find("current-status")
            if status_elem is not None and status_elem.text:
                status = status_elem.text.lower()

            abstract = ""
            abstract_elem = rfc_elem.find(".//abstract/p")
            if abstract_elem is not None and abstract_elem.text:
                abstract = abstract_elem.text

            keywords = []
            for kw_elem in rfc_elem.findall(".//keywords/kw"):
                if kw_elem.text:
                    keywords.append(kw_elem.text)

            entry = RFCEntry(
                number=number,
                title=title,
                authors=authors,
                status=status,
                abstract=abstract,
                keywords=keywords,
                url=f"{self.base_url}/rfc/rfc{number}",
            )
            entries.append(entry)

        return entries

    def parse_draft_json(self, data: dict) -> list[IETFDraft]:
        """Parse IETF datatracker API response into IETFDraft objects."""
        drafts = []
        for obj in data.get("objects", []):
            name = obj.get("name", "")
            title = obj.get("title", "")
            group = obj.get("group", {})
            wg = group.get("acronym", "") if isinstance(group, dict) else ""

            states = obj.get("states", [])
            status = ""
            if states and isinstance(states[0], dict):
                status = states[0].get("slug", "")

            abstract = obj.get("abstract", "")
            resource_uri = obj.get("resource_uri", "")
            url = f"{self.datatracker_url}{resource_uri}" if resource_uri else ""

            draft = IETFDraft(
                name=name,
                title=title,
                working_group=wg,
                status=status,
                abstract=abstract,
                url=url,
            )
            drafts.append(draft)

        return drafts

    async def fetch_recent_rfcs(self, limit: int = 50) -> list[RFCEntry]:
        """Fetch recent RFCs from the RFC editor. Requires httpx."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/rfc-index.xml")
            resp.raise_for_status()
            entries = self.parse_rfc_xml(resp.text)
            # Return the most recent ones (highest number)
            entries.sort(key=lambda e: e.number, reverse=True)
            return entries[:limit]

    async def fetch_security_drafts(self, groups: list[str] | None = None) -> list[IETFDraft]:
        """Fetch active drafts from security-related IETF working groups."""
        import httpx

        target_groups = groups or list(self.security_groups)
        all_drafts = []

        async with httpx.AsyncClient(timeout=30) as client:
            for wg in target_groups:
                url = f"{self.datatracker_url}/api/v1/doc/document/?group__acronym={wg}&states__slug=active&limit=20"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    drafts = self.parse_draft_json(resp.json())
                    all_drafts.extend(drafts)
                except Exception as e:
                    logger.warning("Failed to fetch drafts for %s: %s", wg, e)

        return all_drafts
