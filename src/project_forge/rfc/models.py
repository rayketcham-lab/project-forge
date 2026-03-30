"""Data models for RFC and IETF draft tracking."""

from datetime import datetime

from pydantic import BaseModel, Field

from project_forge.rfc.filters import SECURITY_KEYWORDS, SECURITY_WORKING_GROUPS


class RFCEntry(BaseModel):
    number: int
    title: str
    authors: list[str] = Field(default_factory=list)
    status: str = ""
    abstract: str = ""
    working_group: str = ""
    keywords: list[str] = Field(default_factory=list)
    url: str = ""
    published_at: datetime | None = None

    def is_security_relevant(self) -> bool:
        """Check if this RFC is security-relevant."""
        from project_forge.rfc.filters import is_security_relevant

        return is_security_relevant(self)


class IETFDraft(BaseModel):
    name: str
    title: str
    working_group: str = ""
    status: str = ""
    abstract: str = ""
    url: str = ""
    last_updated: datetime | None = None

    def is_security_relevant(self) -> bool:
        """Check if this draft is security-relevant."""
        wg = self.working_group.lower()
        if wg in SECURITY_WORKING_GROUPS:
            return True
        text = f"{self.title} {self.abstract} {self.name}".lower()
        return any(kw in text for kw in SECURITY_KEYWORDS)
