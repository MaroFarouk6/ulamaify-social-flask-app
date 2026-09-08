from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint

from app.extensions import db


def utcnow() -> datetime:
    """Database timestamps are naive UTC; render explicitly as UTC in this phase."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'content_creator')", name="valid_role"),)
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime)


class AuthSession(db.Model):
    __tablename__ = "auth_sessions"
    id = db.Column(db.String(64), primary_key=True)  # SHA-256 of opaque browser token
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    data = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


class LoginBucket(db.Model):
    __tablename__ = "login_buckets"
    key = db.Column(db.String(64), primary_key=True)
    hits = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    actor_name = db.Column(db.String(120))
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(100))
    details_json = db.Column(db.JSON, nullable=False, default=dict)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(512))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


# Imported after shared User/utcnow definitions to avoid a circular import during model registration.
from app.models.submission import (  # noqa: E402,F401
    DriveCommitAttempt,
    MediaAsset,
    QueueAllocation,
    ReviewEvent,
    Submission,
    SubmissionPlatform,
)
