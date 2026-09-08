# Backup and recovery

Use the server's existing backup system. Keep encrypted backups outside the Docker VM and test restoration into a separate instance.

## Back up

- PostgreSQL database, including accounts, audit/review events, submissions, queue allocations, and commit records.
- Persistent staging volume; do not treat it as a disposable cache.
- Secret files, Google machine credential, `.env`, and the applicable Traefik configuration through an encrypted secret-backup process.
- Source package and the deployed image/version reference.

Example database backup (creates a new local backup file):

```bash
mkdir -p backups
chmod 700 backups
docker compose exec -T postgres pg_dump -U social_owner -d social_manager -Fc > backups/social-manager.dump
chmod 600 backups/social-manager.dump
```

Do not commit `backups/` or upload unencrypted database/credential backups with source. Session data and password hashes are sensitive even though plaintext passwords are absent.

## Recovery procedure

1. Restore to a separate database/VM first; do not overwrite the running production database.
2. Restore the matching schema and staging backup together. Reapply runtime grants from the migration/deployment instructions.
3. Mount the existing secrets securely and start the same source/image version.
4. Verify `/health`, account login, and a read of the Campaign Registry.
5. Revoke restored login sessions before opening access, so previously logged-out sessions do not become valid again. Use a controlled maintenance command/database session with appropriate authorization.
6. Keep `drive-worker` stopped while comparing saved Drive IDs and attempts with existing packages. Never reset `published`, overwrite `post.json`, or recreate a package blindly after a database restore.
7. Resume the web service first. Start the worker only after reconciliation is complete.

The full database/staging/Drive reconciliation drill is a target-environment Phase 7 gate. The local fake-Drive retry tests are not a substitute for this restore exercise.

## Retention

No automatic cleanup job is enabled in this release. Keep staged originals until every relevant variant is confirmed committed and the configured retention period has elapsed; perform any cleanup only from a reviewed backup-aware procedure. Rejected and changes-requested media must remain available. No cleanup of Drive publishing folders is planned.

Never run `docker compose down -v` against the production stack as ordinary maintenance; that removes persistent volumes. Database downgrades and destructive recovery require explicit review of the target and backup first.
