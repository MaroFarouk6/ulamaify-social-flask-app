import hashlib
import re
import secrets

from flask.sessions import SessionInterface, SessionMixin
from sqlalchemy import delete, insert, select, update
from werkzeug.datastructures import CallbackDict

from app.extensions import db
from app.models import AuthSession, utcnow


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DatabaseSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, sid=None, expires_at=None, new=True):
        super().__init__(initial, lambda obj: setattr(obj, "modified", True))
        self.sid = sid or secrets.token_urlsafe(32)
        self.expires_at = expires_at
        self.new = new
        self.modified = False


class DatabaseSessionInterface(SessionInterface):
    """Only an opaque token reaches the cookie. Session contents live in PostgreSQL."""

    def open_session(self, app, request):
        if request.path == "/health" or request.path.startswith("/static/"):
            return DatabaseSession()
        sid = request.cookies.get(self.get_cookie_name(app), "")
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", sid):
            with db.engine.connect() as conn:
                row = (
                    conn.execute(
                        select(AuthSession.__table__).where(
                            AuthSession.id == digest(sid), AuthSession.expires_at > utcnow()
                        )
                    )
                    .mappings()
                    .first()
                )
            if row:
                return DatabaseSession(row["data"], sid, row["expires_at"], new=False)
        return DatabaseSession()

    def rotate(self, session):
        if not session.new:
            with db.engine.begin() as conn:
                conn.execute(delete(AuthSession).where(AuthSession.id == digest(session.sid)))
        session.sid = secrets.token_urlsafe(32)
        session.new = True
        session.expires_at = None
        session.modified = True

    def save_session(self, app, session, response):
        if not session.modified:
            return
        name = self.get_cookie_name(app)
        if not session:
            if not session.new:
                with db.engine.begin() as conn:
                    conn.execute(delete(AuthSession).where(AuthSession.id == digest(session.sid)))
            response.delete_cookie(
                name, path="/", secure=self.get_cookie_secure(app), httponly=True, samesite="Lax"
            )
            return
        expires = session.expires_at or utcnow() + app.permanent_session_lifetime
        values = {
            "data": dict(session),
            "expires_at": expires,
            "user_id": int(session["_user_id"]) if session.get("_user_id") else None,
        }
        with db.engine.begin() as conn:
            if session.new:
                conn.execute(insert(AuthSession).values(id=digest(session.sid), **values))
            else:
                result = conn.execute(
                    update(AuthSession)
                    .where(AuthSession.id == digest(session.sid))
                    .values(**values)
                )
                if not result.rowcount:
                    # A concurrent logout/reset revoked this session. Never recreate it.
                    response.delete_cookie(name, path="/")
                    return
        response.set_cookie(
            name,
            session.sid,
            expires=expires,
            path="/",
            secure=self.get_cookie_secure(app),
            httponly=True,
            samesite="Lax",
        )
        response.vary.add("Cookie")
