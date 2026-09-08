from flask import Blueprint, current_app, flash, redirect, render_template, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from app.auth.forms import ActionForm, ChangePasswordForm, LoginForm
from app.extensions import db
from app.models import User, utcnow
from app.services import auth_service
from app.services.audit_service import record

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        email = auth_service.normalized_email(form.email.data)
        if not auth_service.reserve_login_attempt(email):
            record("LOGIN_FAILED", "authentication", details={"reason": "rate_limited"})
            db.session.commit()
            return render_template(
                "auth/login.html", form=form, error="Too many login attempts. Try again later."
            ), 429
        user = db.session.scalar(select(User).where(User.email == email))
        valid = auth_service.verify_password(
            user.password_hash if user else auth_service.dummy_hash, form.password.data
        )
        if not user or not valid or not user.is_active:
            record(
                "LOGIN_FAILED",
                "user",
                user.id if user else None,
                details={"reason": "invalid_credentials_or_disabled"},
            )
            db.session.commit()
            return render_template(
                "auth/login.html",
                form=form,
                error="Email or password is incorrect, or the account is disabled.",
            ), 401
        if auth_service.hasher.check_needs_rehash(user.password_hash):
            user.password_hash = auth_service.password_hash(form.password.data)
        user.last_login_at = utcnow()
        record("LOGIN_SUCCESS", "user", user.id, actor=user)
        db.session.commit()
        current_app.session_interface.rotate(session)
        session.clear()
        login_user(user, remember=False, fresh=True)
        session["auth_version"] = user.auth_version
        session.permanent = True
        return redirect(
            url_for("auth.change_password" if user.must_change_password else "dashboard")
        )
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    form = ActionForm()
    if not form.validate_on_submit():
        return render_template("error.html", message="Invalid request."), 400
    record("LOGOUT", "user", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not auth_service.verify_password(current_user.password_hash, form.current_password.data):
            record("PASSWORD_CHANGE_FAILED", "user", current_user.id)
            db.session.commit()
            form.current_password.errors.append("Current password is incorrect.")
        else:
            user = current_user._get_current_object()
            user.password_hash = auth_service.password_hash(form.password.data)
            user.must_change_password = False
            auth_service.invalidate_sessions(user)
            record("PASSWORD_CHANGED", "user", user.id)
            db.session.commit()
            current_app.session_interface.rotate(session)
            session.clear()
            login_user(user, fresh=True)
            session["auth_version"] = user.auth_version
            session.permanent = True
            flash("Password changed. Other sessions have been signed out.", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth/password.html", form=form)
