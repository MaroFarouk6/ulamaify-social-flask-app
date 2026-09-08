import os
import secrets

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

from app import create_app
from app.extensions import db


def migration_app(uri):
    return create_app(
        {
            "TESTING": True,
            "PRODUCTION": False,
            "SECRET_KEY": secrets.token_urlsafe(48),
            "SQLALCHEMY_DATABASE_URI": uri,
            "SESSION_COOKIE_SECURE": False,
        }
    )


def test_migrations_and_immutable_audit(tmp_path):
    app = migration_app(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with app.app_context():
        upgrade()
        assert {
            "users",
            "auth_sessions",
            "audit_logs",
            "login_buckets",
            "submissions",
            "submission_platforms",
            "media_assets",
            "review_events",
            "drive_commit_attempts",
            "queue_allocations",
        } <= set(inspect(db.engine).get_table_names())
        db.session.execute(
            text(
                "INSERT INTO audit_logs(action,entity_type,details_json,created_at) VALUES ('TEST','test','{}',CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        for statement in ["UPDATE audit_logs SET action='EDITED'", "DELETE FROM audit_logs"]:
            with pytest.raises(DatabaseError):
                db.session.execute(text(statement))
                db.session.commit()
            db.session.rollback()
        db.session.execute(
            text(
                "INSERT INTO users(name,email,password_hash,role,is_active,must_change_password,auth_version,created_at,updated_at) "
                "VALUES ('A','a@example.com','hash','admin',1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO submissions(id,creator_id,campaign_id,campaign_name_snapshot,campaign_folder_snapshot,target_date,default_caption,status,version,created_at,updated_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001',1,'c','C','C','2026-09-10','','DRAFT',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO review_events(submission_id,actor_id,actor_name,action,created_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001',1,'A','SUBMITTED',CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        for statement in [
            "UPDATE review_events SET action='REJECTED'",
            "DELETE FROM review_events",
        ]:
            with pytest.raises(DatabaseError):
                db.session.execute(text(statement))
                db.session.commit()
            db.session.rollback()
        # Only this disposable test database is downgraded.
        downgrade(revision="base")
        assert "users" not in inspect(db.engine).get_table_names()


@pytest.mark.postgres
def test_postgres_migrations_locks_and_audit():
    uri = os.getenv("TEST_POSTGRES_URL")
    if not uri:
        pytest.skip("Set TEST_POSTGRES_URL to a new disposable PostgreSQL database.")
    app = migration_app(uri)
    with app.app_context():
        if inspect(db.engine).get_table_names():
            pytest.fail(
                "PostgreSQL integration test requires an EMPTY disposable database; nothing was changed."
            )
        upgrade()
        db.session.execute(text("SELECT pg_advisory_xact_lock(712164921)"))
        db.session.commit()
        db.session.execute(
            text(
                "INSERT INTO audit_logs(action,entity_type,details_json,created_at) VALUES ('TEST','test','{}',CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        for statement in [
            "UPDATE audit_logs SET action='EDITED'",
            "DELETE FROM audit_logs",
            "TRUNCATE audit_logs",
        ]:
            with pytest.raises(DatabaseError):
                db.session.execute(text(statement))
                db.session.commit()
            db.session.rollback()
        # Leave the disposable database intact for inspection. Use compose down -v only for this test stack.
