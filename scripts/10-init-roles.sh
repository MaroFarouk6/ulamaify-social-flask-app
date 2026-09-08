#!/bin/sh
set -eu
# PostgreSQL's init entrypoint runs this only on first initialization of its volume.
# psql gets the password from its environment rather than command-line arguments.
SOCIAL_APP_PASSWORD=$(cat /run/secrets/db_app_password)
export SOCIAL_APP_PASSWORD
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_password SOCIAL_APP_PASSWORD
CREATE ROLE social_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE social_manager TO social_app;
GRANT USAGE ON SCHEMA public TO social_app;
SQL
unset SOCIAL_APP_PASSWORD
