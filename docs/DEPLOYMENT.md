# Production deployment

These commands are a handoff for the Docker host. They were not executed against the Ulamaify server. First use duplicate Google resources with no publishing workflow attached.

## 1. Prepare configuration and secrets

```bash
cp .env.example .env
python3 scripts/init_secrets.py
mkdir -p google-secrets
sudo chown -R 10001:10001 secrets google-secrets
sudo chmod 750 secrets google-secrets
sudo chmod 440 secrets/secret_key secrets/database_url secrets/migration_database_url
sudo chmod 444 secrets/db_owner_password secrets/db_app_password
```

Set these non-secret values in `.env`:

```text
SOCIAL_HOST=social.ulamaify.org
TRAEFIK_NETWORK=<the existing external Traefik Docker network>
TRAEFIK_HTTP_ENTRYPOINT=http
TRAEFIK_HTTPS_ENTRYPOINT=https
PROXY_HOPS=1
CAMPAIGN_REGISTRY_SHEET_ID=<sheet ID>
CAMPAIGN_REGISTRY_RANGE='sheet1'!A:G
SOCIAL_ROOT_FOLDER_ID=<Ulamaify Social Media folder ID>
CAMPAIGNS_ROOT_FOLDER_ID=<01_CAMPAIGNS folder ID>
```

Leave `ENABLE_DRIVE_FACEBOOK_CAROUSEL=false` until the Facebook n8n branch is verified. There is intentionally no LinkedIn PDF release flag: its canonical `post.json` contract has not been supplied.

`init_secrets.py` refuses to overwrite an existing `secrets/` directory. Database password rotation requires a coordinated PostgreSQL role update; do not rerun this script on an existing stack.

## 2. Generate the Google machine credential

Client ID and client secret alone are not an authorization grant. Run consent once on a trusted workstation to obtain a refresh token. Register this callback on the Google OAuth client:

```text
http://localhost:8765/
```

Keep the downloaded OAuth client JSON outside the project, then run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python scripts/google_authorize.py \
  --client-file /absolute/private/path/client.json \
  --output /absolute/private/path/google_authorized_user.json
```

Securely transfer the result:

```bash
sudo install -o 10001 -g 10001 -m 400 \
  /private/incoming/google_authorized_user.json \
  google-secrets/google_authorized_user.json
```

The dedicated machine Google account must be granted only the required access to the existing social root, `01_CAMPAIGNS`, and Campaign Registry Sheet. Creators never see this credential. The app stores neither OAuth material nor access tokens in PostgreSQL.

The app requests Drive and Sheets scopes because it must inspect pre-existing folders and edit the registry. OAuth consent in external Testing mode may issue short-lived refresh tokens; confirm the Google Cloud project’s production status.

## 3. Validate and migrate

```bash
docker network inspect "$(grep '^TRAEFIK_NETWORK=' .env | cut -d= -f2-)" >/dev/null
docker compose config --quiet
docker compose build social-manager migrate
docker compose up -d postgres
docker compose --profile tools run --rm migrate
docker compose run --rm social-manager flask --app app:create_app bootstrap-admin
```

The bootstrap password is entered through a hidden prompt and is never a command-line flag. Migrations are explicit; Gunicorn and the worker never migrate automatically. The runtime `social_app` database role cannot update/delete/truncate audit or review history.

## 4. Traefik contract

The Compose labels mirror the supplied n8n hardening setup:

- HTTPS router uses entrypoint `https`, `tls=true`, and `security-headers@file`.
- HTTP router uses entrypoint `http` and `https-redirect@file`.
- No `traefik-auth` is added because the application already authenticates every protected action.
- The service joins the existing external proxy network and advertises container port 8000.
- No application or PostgreSQL port is published on the host.

The wildcard certificate should be selected by the existing Traefik TLS store. No guessed certificate-resolver label is added. If your external network is not named `proxy`, set `TRAEFIK_NETWORK` accordingly.

Set `PROXY_HOPS=1` only when the app is reachable exclusively through this one trusted Traefik hop. The app trusts forwarded client IP and scheme but not forwarded host/port/prefix, and validates the Host header.

## 5. Test before enabling the Drive worker

Run the isolated database suite:

```bash
docker compose -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yml down
```

Start only the web application against a duplicate Sheet and Drive root:

```bash
docker compose up -d social-manager
docker compose ps
docker compose logs --tail=100 social-manager
```

Verify through Traefik:

```bash
SOCIAL_HOST="$(grep '^SOCIAL_HOST=' .env | cut -d= -f2-)"
curl -k -sS --resolve "${SOCIAL_HOST}:443:127.0.0.1" \
  -o /dev/null -w "SOCIAL_HTTPS_CODE=%{http_code}\n" "https://${SOCIAL_HOST}/login"
curl -sSI --resolve "${SOCIAL_HOST}:80:127.0.0.1" "http://${SOCIAL_HOST}/" | sed -n '1,8p'
ss -lnt 2>/dev/null | grep -E '[:.](8000|5432)[[:space:]]' \
  || echo "NO_APP_OR_DATABASE_HOST_PORTS_EXPOSED"
```

Confirm login, user creation, campaign addition/toggle, staging upload, and creator submit. The duplicate Drive root must remain unchanged before approval.

## 6. Start and test the commit worker

Only after the duplicate resources pass:

```bash
docker compose up -d drive-worker
docker compose logs --tail=100 drive-worker
```

Run the four acceptance scenarios. Also force a safe failure before `post.json`, verify n8n cannot see a publishable package, clear the failure, retry in Drive Commits, and confirm completion. Test concurrent approvals for the same campaign/date/platform and inspect monotonic folder numbers.

Then replace duplicate IDs with production IDs, re-check `.env`, migrate if needed, and start:

```bash
docker compose up -d social-manager drive-worker
docker compose ps
```

Approval ends at Drive. No command in this application invokes n8n.

## 7. Maintenance

```bash
docker compose exec social-manager flask --app app:create_app cleanup-sessions
docker compose logs --tail=200 social-manager drive-worker
```

Schedule session cleanup with the host’s maintenance system if desired. Do not schedule publishing here. Do not run `docker compose down -v` in production. Follow `BACKUP_RECOVERY.md` before upgrades or recovery.
