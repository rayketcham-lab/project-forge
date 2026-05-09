# Security Policy

## Reporting a vulnerability

If you find a security issue, please **do not open a public GitHub issue.** Use GitHub's private security advisory flow instead:

1. Go to https://github.com/rayketcham-lab/project-forge/security/advisories/new
2. Describe the issue, the affected version, and a reproduction if you have one.

If for some reason that's not available, email the address in the `pyproject.toml` author field. Either way, expect best-effort response — this is a personal project, not a vendor product. There is no SLA.

## Scope

In scope:
- Authenticated and unauthenticated access to the dashboard (`/`, `/explore`, `/ideas/...`, `/thinktank`, `/api/*`)
- The Bearer token middleware in `web/auth.py`
- The dashboard ephemeral token mechanism
- SSRF / URL-ingest validation in `engine/url_ingest.py`
- Subprocess invocation of `gh` CLI and `claude` CLI
- SQL handling in `storage/db.py` (parameterized in current code; report any string-formatted query you find)
- LLM prompt injection paths that could exfiltrate state or invoke unintended actions
- Any path that accepts user input (URL ingest, text ingest, wizard fragment, issue reporter, challenge form)

Out of scope:
- Vulnerabilities in upstream dependencies (Anthropic SDK, FastAPI, etc.) — please report those upstream
- DoS via running the cron scripts at high frequency (cron rate is operator-controlled)
- Security of the host running the dashboard (network exposure, OS hardening, etc.)
- Anything requiring local shell access on the host

## Disclosure

Coordinated disclosure preferred. If a fix is straightforward, expect a same-week patch. If it isn't, or the report turns out to be a duplicate, expect a written reply explaining why.

## What's already in place

- Bearer token gate on all non-read HTTP methods when `FORGE_API_TOKEN` is set (see `web/auth.py`)
- Per-process ephemeral dashboard token, persisted to `/tmp` so it survives uvicorn `--reload` and systemctl restarts within a boot
- CSP + `X-Content-Type-Options: nosniff` + `X-Frame-Options: DENY` on every dashboard response
- SSRF guard on URL ingest: `socket.getaddrinfo` resolution + private/loopback/link-local/reserved IP rejection
- Rate limiting on the issue-reporter endpoint (`web/routes.py`)
- Dependency audit gate in CI
- Schema-lock test in `tests/test_db_integrity.py` to catch unintended migration drift

## What's intentionally absent

- No multi-tenant model. The dashboard assumes a single operator.
- No password auth. Bearer token only.
- No HTTPS termination in-process. Run behind a reverse proxy if exposed.
- No audit log of human approve/reject actions beyond what's in the SQLite tables.
