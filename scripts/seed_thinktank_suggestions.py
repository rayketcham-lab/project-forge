"""Seed the Think Tank with the vetted, file-grounded self-improvement suggestions
produced by the multi-agent audit (2026-06). These replace the floaty era with
concrete, high-leverage code-change proposals. Idempotent via content-hash dedup.

Run: python scripts/seed_thinktank_suggestions.py
"""

import asyncio

from project_forge.config import settings
from project_forge.engine.dedup import filter_and_save
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

_EFFORT_SCORE = {"S": 0.85, "M": 0.7, "L": 0.55}

# (name, tagline, what, why, files, effort)
SUGGESTIONS = [
    (
        "Fix Phase-schema self-contradiction",
        "the engine docks + rejects ideas for following its own mvp_scope schema",
        "The generation schemas tell the LLM to write mvp_scope as 'Phase 1, Phase 2, Phase 3', but "
        "scorer._OVERAMBITION_SIGNALS docks scope_realism for 'phase 1'..'phase 4' and "
        "quality_review._NEW_PROJECT_SIGNALS hard-rejects SELF_IMPROVEMENT ideas containing them. "
        "Reword the schema to 'what the MVP includes / excludes' and drop the bare phase tokens from both signal lists.",
        "Every schema-compliant idea is silently down-scored and a chunk of self-improvement ideas are auto-rejected "
        "for obeying their instructions — a direct internal contradiction depressing feasibility + throughput.",
        ["src/project_forge/engine/llm_generator.py", "src/project_forge/engine/scorer.py", "src/project_forge/engine/quality_review.py"],
        "S",
    ),
    (
        "Fix the dead Pulse cadence watermark",
        "Pulse react-loop almost never fires because its gate is keyed on a sibling cadence",
        "The pulse Cadence uses delay_query=seconds_until_next_expand (MAX(generated_at) over ALL ideas), which the "
        "hourly expand cadence keeps fresh, so pulse returns nearly every tick. Tag pulse ideas mode='pulse' in "
        "_fire_pulse and add seconds_until_next_pulse querying MAX(generated_at) WHERE generation_mode='pulse'.",
        "A whole shipped /labs avenue (Pulse) is effectively dead in production while the dashboard shows it as live.",
        ["src/project_forge/web/lifespan_scheduler.py", "src/project_forge/engine/llm_generator.py"],
        "S",
    ),
    (
        "Wire grounded generation-mode introspection",
        "the Think Tank's telemetry signals never reach the LLM, so it free-associates",
        "introspect_runner always calls build_introspection_prompt without mode, so the telemetry-grounded path "
        "(mode='generation' + gather_generation_signals + validate_generation_patch) is dead in prod. Alternate the "
        "runner into generation mode, feeding filter-rate/saturation/novelty/coverage + build_calibration recommendations.",
        "The single biggest cause of floaty Think Tank output: all the rich signals telemetry computes are never used. "
        "The strict prompt + validator already exist and are tested — this is wiring that forces every proposal to name a file + a metric.",
        ["src/project_forge/cron/introspect_runner.py", "src/project_forge/engine/introspect.py", "src/project_forge/engine/scoreboard.py"],
        "M",
    ),
    (
        "Harden the autonomous self-improve loop",
        "untrusted GitHub issue -> LLM -> code executed on host with live secrets before review",
        "run_self_improve_cycle feeds an untrusted issue body to the LLM, writes files into tests/+engine/, then runs "
        "pytest (which imports/executes them) with NO scrubbed env — inheriting ANTHROPIC_API_KEY + gh token. Add a scrubbed "
        "env= to _run_cmd, a FORGE_SELF_IMPROVE_ENABLED flag (default off), and an author/authorAssociation allowlist on ci-queue issues.",
        "Highest-risk path in the engine: untrusted text -> LLM -> code executed on the production host with live credentials, autonomously, pre-review.",
        ["src/project_forge/cron/self_improve_runner.py", "src/project_forge/config.py", "src/project_forge/web/lifespan_scheduler.py"],
        "M",
    ),
    (
        "Capture owner approve/reject as outcome signals",
        "2 of 3 Scoreboard axes can never learn — no fundability/ambition signal is ever captured",
        "capture_outcome_signals only records axis='snipe', so learned_nudge('fundability'/'ambition') is structurally 0 forever. "
        "Turn the owner's promote/reject decisions into realized signals (approved->1.0, rejected/archived->0.0) for the fundability + ambition axes.",
        "The Scoreboard is the LEARN loop but two of its three axes can't learn. Owner decisions are ground truth already in the DB.",
        ["src/project_forge/engine/scoreboard.py", "src/project_forge/web/lifespan_scheduler.py"],
        "M",
    ),
    (
        "Apply the independent scorer on the LLM path",
        "feasibility means self-graded for LLM ideas, independently graded for template ideas",
        "scheduler.py overrides feasibility with the independent composite (score_idea), but horizontal.generate_cross_idea keeps the "
        "model's self-reported feasibility and saves directly. Call score_idea + set the composite in generate_cross_idea and the snipe path.",
        "Cross-board ranking + the auto-promote 0.7 threshold compare apples to oranges; LLM ideas get inflated self-scores.",
        ["src/project_forge/cron/horizontal.py", "src/project_forge/engine/scorer.py"],
        "S",
    ),
    (
        "De-bias the specificity scorer for non-security categories",
        "specificity tech-patterns only match security/infra tokens, penalizing money-bot ideas",
        "scorer._TECH_PATTERNS (0.35-weight specificity) only matches X.509/ACME/OCSP/HSM/PQC/k8s/docker. Money-bot ideas naming "
        "stripe/react/postgres/redis/webhook/oauth/s3/cron match nothing and land near-floor. Add a second pattern group for those.",
        "After the 27-category expansion the specificity axis structurally favors security ideas — tilting feasibility against the money-bot categories the engine is growing.",
        ["src/project_forge/engine/scorer.py"],
        "S",
    ),
    (
        "Close the url_ingest SSRF DNS-rebinding TOCTOU",
        "validate resolves the host, then httpx resolves it again — rebinding bypasses the guard",
        "fetch_url_content calls validate_url (resolves + rejects private IPs) then a separate httpx get that resolves DNS AGAIN. "
        "A domain can return a public IP to validation and a private one (metadata/127.0.0.1) to the fetch. Resolve once, pin the validated IP, and stream with a ~5MB cap.",
        "The engine fetches arbitrary user/LLM-supplied URLs; the two-lookup design lets the SSRF guard be bypassed to hit cloud metadata/internal services, and an unbounded body can OOM the process.",
        ["src/project_forge/engine/url_ingest.py"],
        "M",
    ),
    (
        "Implement the missing DB _write_serialized helper",
        "the documented write serializer doesn't exist; ~20 commit sites are unlocked",
        "db.py creates _write_lock and its docstring says writes go through a _write_serialized helper — but that helper doesn't exist; only 4 "
        "methods take the lock. On the shared connection, an unlocked commit can flush a lock-holder's in-flight transaction. Add the helper and route every write through it.",
        "The single documented defense against interleaved transactions + 'database is locked' storms is mostly unimplemented; partial transactions can commit prematurely.",
        ["src/project_forge/storage/db.py"],
        "M",
    ),
    (
        "Offload blocking subprocess calls off the event loop",
        "a slow gh/Claude CLI call freezes the whole single-loop server for up to 60s",
        "Sync subprocess.run (backend.call, _run_gh, fetch_issue_state) runs inside async code on uvicorn's single loop. Wrap them with "
        "await asyncio.to_thread(...) at the async boundaries.",
        "A manual promote click can stall every concurrent request for up to 60s; every Pulse/Snipe/expand generation blocks the loop for CLI latency.",
        ["src/project_forge/engine/llm_generator.py", "src/project_forge/scaffold/github.py", "src/project_forge/cron/issue_sync_runner.py", "src/project_forge/web/routes.py"],
        "M",
    ),
    (
        "Remove the os._exit teardown hack",
        "fix the leaked aiosqlite thread instead of papering over it with os._exit",
        "The pytest_sessionfinish os._exit wrapper masks a real leak: the module-level Database singleton + the deprecated session-scoped "
        "event_loop override leave a non-daemon aiosqlite worker alive at teardown. Close every Database via fixtures, drop the event_loop override, and delete the hack.",
        "os._exit bypasses atexit + output flushing and is fragile with pytest-cov finalization; it masks a genuine resource leak.",
        ["tests/conftest.py", "src/project_forge/web/app.py", "src/project_forge/storage/db.py"],
        "M",
    ),
    (
        "Default autonomously-created repos to private",
        "Foundry one-click publishes a machine-generated scaffold to the public internet with no review window",
        "create_repo() defaults public=True and the Foundry path hardcodes public=True. Default create_repo(public=False) and make Foundry private-by-default with an explicit opt-in to publish.",
        "For an engine that autonomously creates + pushes repos, public-by-default is the wrong least-privilege posture.",
        ["src/project_forge/scaffold/github.py", "src/project_forge/web/routes.py"],
        "S",
    ),
    (
        "Add a least-privilege permissions block to CI",
        "the workflow declares no permissions, so GITHUB_TOKEN gets the broad default on a self-hosted runner",
        "ci.yml has no permissions key, so jobs (incl. fork PRs on a self-hosted runner) get the default token scope. Add 'permissions: contents: read' at the top and grant 'issues: read' only to the jobs that call gh issue list.",
        "An over-permissioned default token + a self-hosted runner executing PR code is a real escalation path. One-block hardening, no downside.",
        [".github/workflows/ci.yml"],
        "S",
    ),
    (
        "Centralize Anthropic-key neutralization in the test fixture",
        "deterministic-fallback tests silently take the live-API branch on any host with a real key",
        "Extend conftest._isolate_test_env to also blank settings.anthropic_api_key and remove ANTHROPIC_API_KEY/FORGE_ANTHROPIC_API_KEY from os.environ for every test, with an opt-in escape hatch.",
        "Many modules branch on the key; on a host with a real key, no-backend tests take the live branch — the same env-bleed class that caused 44 spurious 401s. Several tests already hand-roll this, proving the gap.",
        ["tests/conftest.py"],
        "S",
    ),
]


async def main() -> None:
    db = Database(settings.db_path)
    await db.connect()
    seeded = 0
    try:
        for name, tagline, what, why, files, effort in SUGGESTIONS:
            idea = Idea(
                name=name,
                tagline=tagline[:200],
                description=f"{what}\n\n**Why it matters:** {why}",
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis=why,
                feasibility_score=_EFFORT_SCORE.get(effort, 0.7),
                mvp_scope="Files to change: " + ", ".join(files),
                tech_stack=["python"],
            )
            _saved, ok, reason = await filter_and_save(idea, db)
            if ok:
                seeded += 1
            else:
                print(f"  skipped '{name}': {reason}")
        print(f"Seeded {seeded}/{len(SUGGESTIONS)} vetted Think Tank suggestions.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
