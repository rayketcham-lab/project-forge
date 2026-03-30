"""Security relevance filters for RFC and draft content."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project_forge.rfc.models import RFCEntry

SECURITY_WORKING_GROUPS = {
    "lamps",  # Limited Additional Mechanisms for PKIX and SMIME
    "tls",  # Transport Layer Security
    "cfrg",  # Crypto Forum Research Group
    "saag",  # Security Area Advisory Group
    "rats",  # Remote ATtestation procedureS
    "scitt",  # Supply Chain Integrity, Transparency, and Trust
    "oauth",  # Web Authorization Protocol
    "ace",  # Authentication and Authorization for Constrained Environments
    "emu",  # EAP Method Update
    "ipsecme",  # IP Security Maintenance and Extensions
    "curdle",  # CURves, Deprecating and a Little more Encryption
    "openpgp",  # Open Pretty Good Privacy
    "pquip",  # Post-Quantum Use In Protocols
    "secdispatch",  # Security Dispatch
}

SECURITY_KEYWORDS = {
    "cryptography",
    "cryptographic",
    "certificate",
    "tls",
    "pki",
    "authentication",
    "authorization",
    "encryption",
    "signature",
    "post-quantum",
    "pqc",
    "quantum",
    "ml-dsa",
    "ml-kem",
    "slh-dsa",
    "dilithium",
    "kyber",
    "sphincs",
    "falcon",
    "revocation",
    "crl",
    "ocsp",
    "x.509",
    "pkix",
    "vulnerability",
    "security",
    "secure",
    "key exchange",
    "key encapsulation",
    "kem",
    "digital signature",
    "zero trust",
    "mutual authentication",
    "mTLS",
    "fips",
    "nist",
    "compliance",
}


def is_security_relevant(entry: RFCEntry) -> bool:
    """Determine if an RFC entry is security-relevant."""
    # Check working group
    wg = entry.working_group.lower()
    if wg in SECURITY_WORKING_GROUPS:
        return True

    # Check keywords
    entry_keywords = {kw.lower() for kw in entry.keywords}
    if entry_keywords & SECURITY_KEYWORDS:
        return True

    # Check title and abstract
    text = f"{entry.title} {entry.abstract}".lower()
    return any(kw in text for kw in SECURITY_KEYWORDS)
