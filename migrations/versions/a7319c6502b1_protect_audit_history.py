"""Protect audit history and grant runtime access without schema ownership.

Revision ID: a7319c6502b1
Revises: 97a99721981f
"""

from alembic import op

revision = "a7319c6502b1"
down_revision = "97a99721981f"
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION prevent_audit_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Audit history is append-only';
            END; $$
        """)
        op.execute("""CREATE TRIGGER audit_no_changes BEFORE UPDATE OR DELETE ON audit_logs
                      FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()""")
        op.execute("""CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_logs
                      FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_mutation()""")
        op.execute("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'social_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON users, auth_sessions, login_buckets TO social_app;
                GRANT SELECT, INSERT ON audit_logs TO social_app;
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM social_app;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO social_app;
                GRANT SELECT ON alembic_version TO social_app;
              END IF;
            END $$
        """)
    elif dialect == "sqlite":
        op.execute("""CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_logs
                      BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END""")
        op.execute("""CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_logs
                      BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END""")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER audit_no_changes ON audit_logs")
        op.execute("DROP TRIGGER audit_no_truncate ON audit_logs")
        op.execute("DROP FUNCTION prevent_audit_mutation()")
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER audit_no_update")
        op.execute("DROP TRIGGER audit_no_delete")
