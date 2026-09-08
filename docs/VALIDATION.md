# Validation checkpoint — 2026-09-08

## Executed successfully

- Full local suite: **86 passed, 1 skipped**.
- Ruff checks over application, migrations, tests, scripts, and Gunicorn configuration: passed.
- Alembic upgrade/downgrade on disposable SQLite: passed.
- PostgreSQL migration SQL generation: passed, including new-table runtime grants and append-only review triggers. This validates generation, not execution against PostgreSQL.
- SQLite triggers reject update/delete of both audit and review history.
- YAML parsing for production and test Compose files: passed.
- All Google behavior in local tests uses fakes; no live account, Drive, Sheet, n8n, or social API was called.

## Covered behavior

- Authentication, Argon2id, disabled accounts, two-role authorization, CSRF, database sessions, rotation/revocation, shared login throttling, secure headers, password reset/change, and user-management audit.
- Campaign active-only visibility, app row creation, direct Sheet refresh, activation/deactivation, date boundaries, duplicate/malformed rows, stale forms, row/column reorder, outage handling, and lost-response reconciliation.
- UUID staging, JPEG/PNG/MP4/PDF signature checks, upload limits, creator isolation, edit locks, media removal/reorder, shared/platform-specific variants, caption override, and Story override.
- Detection and compatibility for all four platforms, Instagram 10-slide limit, Facebook carousel creation, LinkedIn text/image/PDF staging, and YouTube incompatibility messages.
- Creator submission and rejection produce no Drive writes; per-platform approval queues only publisher-ready variants.
- Exact hierarchy reuse/creation, monotonically increasing per-platform post folders, deterministic carousel filenames, UTF-8 captions, exact three-key `post.json`, and marker-last ordering.
- Nine-image Instagram/Facebook acceptance case, three-platform MP4 case with independent numbering, pre-marker failure, retained staging, safe retry/reconciliation, and audit events.

## Target-environment release gates

- Run the skipped PostgreSQL test against the isolated `compose.test.yml` database to validate real advisory locks, grants, and append-only triggers.
- Build the Docker image and run `docker compose config --quiet` on the deployment host.
- Complete OAuth consent and test refresh, Sheet append/toggle, and Drive folder/file writes in duplicate resources.
- Exercise two concurrent approval workers against the duplicate Drive root.
- Verify Traefik HTTPS, redirect, wildcard certificate, headers, trusted proxy address, and lack of host-published app/database ports.
- Run the four browser acceptance scenarios, including visual/mobile review.
- Back up and restore PostgreSQL plus staging into an isolated stack; reconcile Drive IDs before restarting the worker.

The source is not claimed as production-deployed until these live checks pass.
