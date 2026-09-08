# Ulamaify Social Content Manager

Internal Flask application for creating, reviewing, and committing approved social-content packages to the existing Google Drive queue. The preferred production URL is `https://social.ulamaify.org`.

Phases 1–6 are implemented. Phase 7 local verification is complete; live PostgreSQL, Google Drive/Sheets, Traefik, and restore testing must be performed in the target environment before creator rollout.

## What it does

- Two roles only: `admin` and `content_creator`; no public registration.
- Argon2id passwords, server-side sessions, CSRF, login throttling, secure cookies, first-login password changes, account revocation, and admin user management.
- Reads the authoritative Campaign Registry from `'sheet1'!A:G` and lets admins add, activate, or deactivate Sheet rows.
- Stores draft JPEG, PNG, MP4, and PDF files only in UUID staging directories—not Google Drive.
- Creates logical multi-platform submissions with shared or platform-specific media/captions.
- Detects text, image, video, carousel, Story, and internal PDF/document content centrally.
- Supports draft, pending, changes requested, rejection, per-platform approval, Drive error, and approved states with audit history.
- Uses a separate worker to write approved packages, verify them, and upload `post.json` last.
- Allocates monotonic `post-%03d` numbers per campaign/date/platform using PostgreSQL locking and existing Drive folders.
- Retries partial or uncertain Drive commits by reconciling saved IDs and per-attempt Drive properties.

The app never calls n8n, a webhook, Meta, YouTube publishing APIs, or any social publisher. Its responsibility stops after a complete approved package is written to Drive. n8n discovers it later on its own schedule.

## Drive package contract

```text
01_CAMPAIGNS/<campaign folder>/<YYYY-MM-DD>/<platform>/post-%03d/
    media/
    caption.txt
    post.json       # uploaded last
```

`post.json` contains exactly:

```json
{"status":"approved","post_type":"image","published":false}
```

The canonical Drive types remain `text`, `image`, `video`, `carousel`, and `story`. PDF/document is supported in staging and review for LinkedIn but is not released to Drive until the n8n `post_type` and filename contract is explicitly defined. Facebook multi-image content is valid in creation/review; Drive release requires `ENABLE_DRIVE_FACEBOOK_CAROUSEL=true` after its n8n branch is verified.

## Local verification

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/ruff check app tests scripts migrations gunicorn.conf.py
.venv/bin/pytest -q
```

The normal suite uses temporary SQLite databases and fake Google adapters. Run the PostgreSQL migration/locking gate in its isolated Compose stack:

```bash
docker compose -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yml down
```

## Production handoff

Read these before deployment:

- `docs/DEPLOYMENT.md` — secrets, OAuth refresh token, migrations, worker, and Traefik labels.
- `docs/DECISIONS_AND_OPEN_ITEMS.md` — confirmed platform contracts and remaining n8n-specific gates.
- `docs/PHASE_REPORTS.md` — files, commands, and manual checks for every phase.
- `docs/BACKUP_RECOVERY.md` — coordinated PostgreSQL/staging recovery and Drive reconciliation.
- `docs/VALIDATION.md` — evidence from this build and target-environment checks still required.

Never place credentials in `.env`, source, images, logs, or the database. Never run `docker compose down -v` on production. Do not start `drive-worker` until IDs, OAuth access, feature gates, and a duplicate test Drive/Sheet have been verified.
# ulamaify-social-flask-app
