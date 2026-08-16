# Security Policy

## Reporting a vulnerability

If you find a security issue, please **do not open a public GitHub issue.** Use GitHub's private security advisory flow instead:

1. Go to https://github.com/rayketcham-lab/project-forge/security/advisories/new
2. Describe the issue, the affected version, and a reproduction if you have one.

That advisory form is the only reporting channel. The `pyproject.toml` author field is a
`users.noreply.github.com` address, which GitHub does not deliver inbound mail to — mail sent
there is silently dropped, so please don't use it as a fallback. Expect a best-effort response:
this is a personal project, not a vendor product. There is no SLA.

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

- Bearer token gate on all non-read HTTP methods (see `web/auth.py`). With `FORGE_API_TOKEN` set,
  every write needs a token. With it unset, only *loopback* writes are exempt — network callers
  still need the ephemeral dashboard token. Before v0.24.3 an unset token disabled auth for
  everyone, which published every write route on the default `0.0.0.0` bind.
- Per-process ephemeral dashboard token, persisted to `/tmp` (mode `0600`) so it survives uvicorn
  `--reload` and systemctl restarts within a boot
- Fork pull requests never execute on the self-hosted CI runner (`.github/workflows/ci.yml`)
- Hard ceiling on fetched response bodies in `engine/url_ingest.py`, so a hostile endpoint cannot
  stream the single-process app out of memory
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

## Known limitations

Stated plainly rather than left for a reader to discover:

- **The dashboard token is readable by anyone who can load a page.** `GET` is unauthenticated by
  design, and every page embeds the token in `<meta name="forge-token">` so the browser can write.
  So "network callers need the dashboard token" raises the bar; it is not a wall. If the port is
  reachable by anyone you don't trust, set `FORGE_API_TOKEN` and put real authentication in front.
- **A same-host reverse proxy makes every request look like loopback**, which turns the
  loopback write exemption back into "open". Pass a token through the proxy in that setup.
- **`FORGE_ALLOWED_HOSTS` defaults to `*`.** That accepts any `Host` header, which is the
  precondition for DNS rebinding. Narrow it to the names you actually serve on.
- **The self-improvement and Mechanic paths run LLM-authored code**, gated behind kill switches
  that are off by default. Treat enabling them as granting code execution on the host.
