import secrets

import pytest
from conftest import csrf, login, user_id
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, AuthSession, User


@pytest.mark.parametrize("email", ["admin@example.com", "creator@example.com"])
def test_login_and_last_login(app, client, email):
    assert login(client, app, email).status_code == 302
    assert client.get("/").status_code == 200
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == email))
        assert user.last_login_at is not None
        assert user.password_hash.startswith("$argon2id$")
        assert app.config["TEST_PASSWORD"] not in user.password_hash
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "LOGIN_SUCCESS"))


def test_csrf_required_on_login(app, client):
    assert (
        client.post(
            "/login", data={"email": "admin@example.com", "password": app.config["TEST_PASSWORD"]}
        ).status_code
        == 400
    )


def test_unknown_and_wrong_password_same_error(app, client):
    token = csrf(client, "/login")
    results = [
        client.post(
            "/login", data={"email": email, "password": "incorrect-password", "csrf_token": token}
        )
        for email in ["unknown@example.com", "admin@example.com"]
    ]
    assert [r.status_code for r in results] == [401, 401]
    assert all(b"Email or password is incorrect" in r.data for r in results)


def test_disabled_user_cannot_login(app, client):
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == "creator@example.com"))
        user.is_active = False
        db.session.commit()
    assert login(client, app, "creator@example.com").status_code == 401


@pytest.mark.parametrize(
    "path", ["/admin/users", "/admin/audit", "/admin/campaigns", "/admin/users/new"]
)
def test_creator_cannot_access_admin_pages(app, client, path):
    login(client, app, "creator@example.com")
    assert client.get(path).status_code == 403


def test_creator_cannot_create_or_disable_users(app, client):
    login(client, app, "creator@example.com")
    token = csrf(client, "/")
    assert client.post("/admin/users/new", data={"csrf_token": token}).status_code == 403
    assert client.post("/admin/users/1/disable", data={"csrf_token": token}).status_code == 403


def test_login_rotates_session_and_cookie_is_opaque(app, client):
    csrf(client, "/login")
    old = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    login(client, app)
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    assert cookie.value != old
    assert len(cookie.value) == 43
    assert cookie.http_only
    assert cookie.same_site == "Lax"
    with app.app_context():
        records = db.session.scalars(select(AuthSession)).all()
        assert all(cookie.value not in record.id for record in records)
        assert any(record.data.get("_user_id") for record in records)


def test_logout_revokes_replayed_cookie(app, client):
    login(client, app)
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
    assert client.get("/logout").status_code == 405
    client.post("/logout", data={"csrf_token": csrf(client, "/")})
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    assert client.get("/").status_code == 302


def test_disable_revokes_existing_creator_session(app):
    admin, creator = app.test_client(), app.test_client()
    login(admin, app)
    login(creator, app, "creator@example.com")
    creator_id = user_id(app, "creator@example.com")
    token = csrf(admin, "/admin/users")
    admin.post(f"/admin/users/{creator_id}/disable", data={"csrf_token": token})
    assert creator.get("/").status_code == 302
    with app.app_context():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "USER_DISABLED"))


def test_password_reset_revokes_sessions(app):
    admin, creator = app.test_client(), app.test_client()
    login(admin, app)
    login(creator, app, "creator@example.com")
    creator_id = user_id(app, "creator@example.com")
    path = f"/admin/users/{creator_id}/reset-password"
    new_password = secrets.token_urlsafe(24)
    admin.post(path, data={"csrf_token": csrf(admin, path), "password": new_password})
    assert creator.get("/").status_code == 302
    with app.app_context():
        assert db.session.get(User, creator_id).must_change_password
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "PASSWORD_RESET"))


def test_temporary_password_enforced_and_changed(app, client):
    response = login(client, app, "temporary@example.com")
    assert response.location.endswith("/account/password")
    assert client.get("/campaigns").location.endswith("/account/password")
    new_password = secrets.token_urlsafe(24)
    response = client.post(
        "/account/password",
        data={
            "csrf_token": csrf(client, "/account/password"),
            "current_password": app.config["TEST_PASSWORD"],
            "password": new_password,
            "confirm": new_password,
        },
    )
    assert response.status_code == 302
    assert client.get("/campaigns").status_code == 200


def test_unknown_roles_rejected(app, client):
    login(client, app)
    response = client.post(
        "/admin/users/new",
        data={
            "csrf_token": csrf(client, "/admin/users/new"),
            "name": "Intruder",
            "email": "intruder@example.com",
            "password": secrets.token_urlsafe(24),
            "role": "super_admin",
        },
    )
    assert b"Not a valid choice" in response.data
    with app.app_context():
        assert db.session.scalar(select(User).where(User.email == "intruder@example.com")) is None


def test_user_creation_is_audited_and_password_not_logged(app, client):
    login(client, app)
    password = secrets.token_urlsafe(24)
    response = client.post(
        "/admin/users/new",
        data={
            "csrf_token": csrf(client, "/admin/users/new"),
            "name": "New Creator",
            "email": "New.Creator@example.com",
            "password": password,
            "role": "content_creator",
            "must_change": "y",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == "new.creator@example.com"))
        assert user.must_change_password
        audit = db.session.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == str(user.id), AuditLog.action == "USER_CREATED"
            )
        )
        assert audit.actor_name == "Admin"
        assert password not in str(audit.details_json)


def test_cannot_disable_self(app, client):
    login(client, app)
    response = client.post(
        "/admin/users/1/disable",
        data={"csrf_token": csrf(client, "/admin/users")},
        follow_redirects=True,
    )
    assert b"cannot disable your own" in response.data


def test_login_rate_limit_shared_across_clients(app):
    app.config["LOGIN_ACCOUNT_LIMIT"] = 2
    for index in range(3):
        client = app.test_client()
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf(client, "/login"),
                "email": "admin@example.com",
                "password": "incorrect-password",
            },
            environ_overrides={"REMOTE_ADDR": f"192.0.2.{index + 1}"},
        )
    assert response.status_code == 429


def test_no_registration_or_public_api_routes(client):
    for path in ["/register", "/signup", "/webhook", "/publish"]:
        assert client.get(path).status_code == 404


def test_no_external_redirect_after_login(app, client):
    response = client.post(
        "/login?next=https://attacker.example",
        data={
            "csrf_token": csrf(client, "/login"),
            "email": "admin@example.com",
            "password": app.config["TEST_PASSWORD"],
        },
    )
    assert response.location == "/"


def test_health_and_security_headers(client):
    response = client.get("/health")
    assert response.json == {"status": "ok"}
    assert "Set-Cookie" not in response.headers
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_template_escapes_user_name(app, client):
    with app.app_context():
        user = db.session.get(User, 1)
        user.name = "<script>alert(1)</script>"
        db.session.commit()
    login(client, app)
    response = client.get("/")
    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_get_does_not_change_campaign_or_user(app, client, sheet):
    login(client, app)
    assert client.get("/admin/campaigns/test-001/deactivate").status_code == 405
    assert client.get("/admin/users/2/disable").status_code == 405
    assert sheet.writes == []
