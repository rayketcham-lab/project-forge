"""Seed data for Project Forge — tracked resources and defaults."""

from project_forge.models import Resource

SEED_RESOURCES = [
    Resource(
        domain="feistyduck.com",
        name="Feisty Duck — TLS/PKI Resources",
        description=(
            "Publisher of Bulletproof TLS and PKI, authoritative source on TLS configuration,"
            " certificate management, and PKI best practices."
        ),
        url="https://www.feistyduck.com",
        categories=["tls", "pki", "security", "cryptography"],
    ),
]


async def seed_resources(db) -> list[Resource]:
    """Insert seed resources if they don't already exist. Returns newly added resources."""
    added = []
    for resource in SEED_RESOURCES:
        existing = await db.get_resource_by_domain(resource.domain)
        if existing is None:
            await db.save_resource(resource)
            added.append(resource)
    return added
