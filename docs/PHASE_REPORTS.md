# Phase reports

## Phase 1 — Authentication and user management

Implemented: Flask factory/Blueprints, PostgreSQL-compatible relational schema, Alembic migrations, Argon2id, Flask-Login, server-side sessions, login throttling, CSRF, two-role enforcement, admin-created accounts, enable/disable, password reset/change, first-admin CLI, and append-only audit protections.

Changed files (all new in this build):

- `app/__init__.py`, `app/config.py`, `app/extensions.py`, `app/models/__init__.py`
- `app/auth/forms.py`, `app/auth/routes.py`
- `app/admin/forms.py`, `app/admin/routes.py`
- `app/services/auth_service.py`, `app/services/session_service.py`, `app/services/audit_service.py`
- `app/templates/base.html`, `app/templates/_forms.html`, `app/templates/dashboard.html`, `app/templates/error.html`
- `app/templates/auth/login.html`, `app/templates/auth/password.html`
- `app/templates/admin/users.html`, `app/templates/admin/user_form.html`, `app/templates/admin/audit.html`
- `app/static/app.css`, `app/static/app.js`
- `migrations/env.py`, `migrations/alembic.ini`, `migrations/script.py.mako`, `migrations/README`
- `migrations/versions/97a99721981f_authentication_sessions_and_audit_.py`
- `migrations/versions/a7319c6502b1_protect_audit_history.py`
- `tests/conftest.py`, `tests/test_auth.py`, `tests/test_migrations.py`

Migration command after deployment secret preparation:

```bash
docker compose up -d postgres
docker compose run --rm migrate
```

Docker commands:

```bash
docker compose build social-manager migrate
docker compose run --rm social-manager flask --app app:create_app bootstrap-admin
docker compose up -d social-manager
```

Test commands:

```bash
.venv/bin/pytest -q tests/test_auth.py tests/test_migrations.py
docker compose -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yml down
```

Manual verification (after HTTPS/proxy setup):

1. Bootstrap the first admin through the hidden CLI password prompt. A second bootstrap must refuse.
2. Log in and create a content creator with a temporary password.
3. In a separate browser session, log in as the creator. Password change must be required before opening other pages.
4. Confirm the creator receives 403 on `/admin/users`, `/admin/campaigns`, and `/admin/audit`.
5. Disable the creator from the admin session. The creator's next request must require login; login while disabled must fail.
6. Enable the creator, reset the password, and verify existing sessions are revoked and password change is required.
7. Log out and verify the prior session cookie cannot regain access.
8. Inspect the audit log for account actions. Confirm no passwords or Google credentials appear.

## Phase 2 — Campaign Registry

Implemented: machine Google OAuth adapter; read of `'sheet1'!A:G`; exact headers/status/date validation; creator active-only list; admin historical list; Sheet-backed add/activate/deactivate actions; freshness checks; direct Sheet additions and updates observed on refresh; uncertain append/write reconciliation.

Changed files (new, plus factory integration from Phase 1):

- `app/services/google_service.py`, `app/services/campaign_service.py`
- `app/campaigns/routes.py`, `app/templates/campaigns/list.html`, `app/templates/campaigns/form.html`
- `scripts/google_authorize.py`
- `tests/test_campaigns.py`, `tests/test_google_adapter.py`
- `app/__init__.py`, `app/config.py`, `app/templates/base.html`, `app/templates/dashboard.html`

Migration: no additional campaign table. The Sheet remains authoritative. The same migrations install the foundation:

```bash
docker compose run --rm migrate
```

Docker commands after locally configuring the machine credential and real Sheet ID:

```bash
docker compose build social-manager
docker compose up -d social-manager
docker compose logs --tail=80 social-manager
```

Test commands:

```bash
.venv/bin/pytest -q tests/test_campaigns.py tests/test_google_adapter.py
```

Manual verification using a separate test registry:

1. Set `CAMPAIGN_REGISTRY_SHEET_ID` to the duplicate registry and set the range to `'sheet1'!A:G`.
2. Verify admin sees ACTIVE and INACTIVE rows; creator sees only ACTIVE rows.
3. Add one campaign through the admin page and verify exactly one A:G row plus a `CAMPAIGN_CREATED` audit event.
4. Add a valid campaign directly to the Sheet. Refresh the app and verify it appears.
5. Change a status directly in the Sheet, refresh, and verify the app reflects it.
6. Activate/deactivate through the admin page and verify only the status cell changes, with an audit event.
6. Change a campaign name/status after opening the admin page. Submitting the old form must request a refresh.
7. Remove a required header or use an invalid date/status in the test Sheet. The app must show a useful error and must not silently approve an invalid campaign.
8. Revoke test Google access and confirm the application reports an unavailable registry without exposing upstream errors or secrets.

Note: the date-validation service is tested; the publishing-date form arrives with Phase 3.

## Shared deployment and documentation files

- `requirements.txt`, `requirements-dev.txt`, `requirements.lock`, `requirements-dev.lock`, `pyproject.toml`
- `.gitignore`, `.dockerignore`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `compose.test.yml`, `gunicorn.conf.py`
- `scripts/init_secrets.py`, `scripts/10-init-roles.sh`
- `README.md` and all files in `docs/`

These are scaffolding for later deployment validation, not evidence that Docker or the user's Traefik server was tested. No destructive changes were made to any user infrastructure.

## Phase 3 — Submissions and staging

Implemented: relational submission/platform/media schema and migration, UUID directories, signature validation, configurable upload limits, authenticated previews, drag reorder, shared/platform-specific media, caption variants, Story override, automatic type detection, compatibility messages, drafts, submission confirmation, and creator ownership rules.

Primary files:

- `app/models/submission.py`, migration `3ce7591b41e6_submission_media_review_and_drive_.py`
- `app/content/`, `app/services/submission_service.py`, `media_service.py`, `platform_service.py`
- `app/platforms.py`, `app/templates/content/`, `tests/test_content.py`, `tests/test_platform_service.py`

Migration and tests:

```bash
docker compose --profile tools run --rm migrate
.venv/bin/pytest -q tests/test_content.py tests/test_platform_service.py
```

Manual verification: create a draft, upload/reorder media, inspect per-platform detection, add a platform-specific override, submit it, and confirm the Drive test root is unchanged.

## Phase 4 — Review and audit

Implemented: pending queue, grouped platform review, per-platform/all approval, required change/rejection notes, resubmission, review history, audit actions, and creator-visible status/timeline. `APPROVED` is not set until Drive commit completes.

Primary files: `app/admin/review_forms.py`, `app/services/approval_service.py`, review templates/routes, review models, audit migration protections, and `tests/test_approval_drive.py`.

Test and manual verification:

```bash
.venv/bin/pytest -q tests/test_approval_drive.py
```

Submit as a creator, request changes and resubmit, then reject a separate submission. Confirm required notes, ownership rules, audit events, and zero Drive operations for both submit and reject.

## Phase 5 — Safe Drive commit

Implemented: separate commit worker; campaign/date/platform hierarchy; exact monotonic post numbering; PostgreSQL advisory lock plus allocation record; deterministic media; UTF-8 caption; `post.json` last; size/checksum verification; saved Drive IDs; failure/uncertain states; retry reconciliation; and admin commit status page.

Primary files: `app/services/drive_service.py`, `drive_commit_service.py`, `media_normalization_service.py`, worker CLI, commit/allocation models, admin commit template, and acceptance tests.

Commands:

```bash
docker compose up -d drive-worker
docker compose logs --tail=100 drive-worker
docker compose run --rm social-manager flask --app app:create_app process-drive-queue --limit 20
```

Manual verification must use an isolated Drive root: inject a caption upload failure and verify no `post.json`; retry and verify the same partial package is reconciled without resetting any existing marker.

## Phase 6 — Multi-platform workflow and UI

Implemented: central capability model, combined review, individual platform actions, captions/media overrides, carousel JPG normalization without altering originals, video/image/PDF previews, content/admin dashboards, status filters, and Facebook/LinkedIn feature gates.

Manual verification: run the four supplied acceptance scenarios. Facebook carousel approval must remain disabled unless its n8n branch has been verified. LinkedIn PDFs remain staging/review-only until an exact canonical Drive contract is supplied.

## Phase 7 — Deployment, tests, and operational review

Implemented locally: production/test Docker stages, external Traefik network labels matching `http`/`https`, no host ports, HTTPS enforcement, read-only containers, separate worker, health endpoint, environment examples, backup/recovery documentation, 85-test local suite, linting, and migration checks.

Target commands:

```bash
docker compose config --quiet
docker compose build social-manager migrate
docker compose -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yml down
```

Live Google, PostgreSQL, Traefik, browser, concurrency, and recovery gates remain. See `VALIDATION.md`; do not treat unexecuted target checks as passed.
