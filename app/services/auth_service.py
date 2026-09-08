import hashlib
import time
from datetime import datetime, timezone
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import validate_email
from flask import abort, current_app, request
from flask_login import current_user, login_required
from sqlalchemy import delete, select, text

from app.extensions import db
from app.models import AuthSession, LoginBucket, User
from app.services.audit_service import record

hasher = PasswordHasher()
# Equal-cost verification for unknown emails. This is not an account password.
dummy_hash = hasher.hash("dummy verification input only")


def normalized_email(value: str) -> str:
    return validate_email(value.strip(), check_deliverability=False).normalized.casefold()


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters.")
    return hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def admin_required(function):
    @wraps(function)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return function(*args, **kwargs)

    return wrapped


def lock_admin_management():
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(712164921)"))


def reserve_login_attempt(email: str) -> bool:
    """Atomic shared-database buckets work across all Gunicorn workers."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert_fn = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    allowed = True
    now = int(time.time())
    for dimension, value, period, limit in (
        ("ip", request.remote_addr or "unknown", 60, current_app.config["LOGIN_IP_LIMIT"]),
        ("account", email, 900, current_app.config["LOGIN_ACCOUNT_LIMIT"]),
    ):
        window = now // period
        key = hashlib.sha256(f"{dimension}:{value}:{window}".encode()).hexdigest()
        expires = datetime.fromtimestamp((window + 1) * period, timezone.utc).replace(tzinfo=None)
        statement = insert_fn(LoginBucket).values(key=key, hits=1, expires_at=expires)
        statement = statement.on_conflict_do_update(
            index_elements=[LoginBucket.key], set_={"hits": LoginBucket.hits + 1}
        ).returning(LoginBucket.hits)
        hits = db.session.execute(statement).scalar_one()
        allowed = allowed and hits <= limit
    db.session.commit()
    return allowed


def create_user(*, name, email, role, password, must_change=True, actor=None) -> User:
    if actor is None or not actor.is_active or actor.role != "admin":
        raise PermissionError("Administrator access is required.")
    return _insert_user(name, email, role, password, must_change, actor)


def _insert_user(name, email, role, password, must_change, actor):
    if role not in ("admin", "content_creator"):
        raise ValueError("Select a valid role.")
    if not name.strip() or len(name.strip()) > 120:
        raise ValueError("Enter a name of up to 120 characters.")
    email = normalized_email(email)
    if db.session.scalar(select(User).where(User.email == email)):
        raise ValueError("An account with this email already exists.")
    user = User(
        name=name.strip(),
        email=email,
        role=role,
        password_hash=password_hash(password),
        must_change_password=must_change,
    )
    db.session.add(user)
    db.session.flush()
    record(
        "USER_CREATED", "user", user.id, {"role": role, "must_change_password": must_change}, actor
    )
    return user


def bootstrap_admin(name, email, password):
    lock_admin_management()
    if db.session.scalar(select(User.id).where(User.role == "admin")):
        raise ValueError("An administrator already exists. Create additional accounts in Users.")
    return _insert_user(name, email, "admin", password, False, None)


def invalidate_sessions(user):
    user.auth_version += 1
    db.session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))


def set_active(user_id, enabled: bool, actor):
    if actor.role != "admin" or not actor.is_active:
        raise PermissionError("Administrator access is required.")
    lock_admin_management()
    user = db.session.get(User, user_id)
    if user is None:
        raise ValueError("Account not found.")
    if not enabled and actor.id == user.id:
        raise ValueError("You cannot disable your own account.")
    if not enabled and user.role == "admin":
        active_admins = db.session.scalars(
            select(User.id).where(User.role == "admin", User.is_active.is_(True))
        ).all()
        if user.id in active_admins and len(active_admins) <= 1:
            raise ValueError("The last active administrator cannot be disabled.")
    if user.is_active != enabled:
        user.is_active = enabled
        invalidate_sessions(user)
        record("USER_ENABLED" if enabled else "USER_DISABLED", "user", user.id, actor=actor)


def reset_password(user, password, actor):
    if actor.role != "admin" or not actor.is_active:
        raise PermissionError("Administrator access is required.")
    user.password_hash = password_hash(password)
    user.must_change_password = True
    invalidate_sessions(user)
    record("PASSWORD_RESET", "user", user.id, actor=actor)
