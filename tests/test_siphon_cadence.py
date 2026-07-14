"""Tests for the weekly siphon cadence (#94).

The idea pool regrew to 3,294 active ideas before a manual trim archived
731 accumulated near-dupes — nothing ran the siphon autonomously. This
cadence makes the pool self-cleaning.
"""

from datetime import timedelta

import pytest


class TestSiphonCadenceRegistration:
    def test_siphon_registered_in_defaults(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        cadences = default_cadences()
        names = [c.name for c in cadences]
        assert "siphon" in names
        siphon_cad = next(c for c in cadences if c.name == "siphon")
        # Weekly, pure clock — siphon_all is idempotent and cheap when clean.
        assert siphon_cad.interval == timedelta(hours=168)
        assert siphon_cad.delay_query is None


class TestFireSiphon:
    @pytest.mark.asyncio
    async def test_fire_siphon_applies_not_dry_run(self, db, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        called: dict = {}

        async def fake_siphon_all(db_, dry_run):
            called["dry_run"] = dry_run
            return {
                "atomic": {"cluster_count": 1, "archived_count": 2, "applied_count": 2},
                "supers": {"cluster_count": 0, "archived_count": 0, "applied_count": 0},
                "verticals": {"cluster_count": 0, "archived_count": 0, "applied_count": 0},
            }

        monkeypatch.setattr("project_forge.engine.siphon.siphon_all", fake_siphon_all)

        await ls._fire_siphon(db)

        assert called["dry_run"] is False
