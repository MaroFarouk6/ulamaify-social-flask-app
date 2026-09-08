import logging
import signal
import time

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user
from sqlalchemy import delete, func, select, text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import settings, validate_config
from app.extensions import csrf, db, login_manager, migrate
from app.models import AuditLog, AuthSession, LoginBucket, User, utcnow
from app.services.session_service import DatabaseSessionInterface


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(settings())
    if test_config:
        app.config.update(test_config)
    validate_config(app.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"
    csrf.init_app(app)
    app.session_interface = DatabaseSessionInterface()
    if app.config["PROXY_HOPS"]:
        # The upstream must be trusted and the application port inaccessible to the public.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    from app.admin.routes import bp as admin_bp
    from app.auth.routes import bp as auth_bp
    from app.campaigns.routes import bp as campaigns_bp
    from app.content.routes import bp as content_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(content_bp)

    @login_manager.user_loader
    def load_user(user_id):
        if not user_id.isdigit():
            return None
        user = db.session.get(User, int(user_id))
        if not user or not user.is_active or session.get("auth_version") != user.auth_version:
            return None
        return user

    @app.before_request
    def enforce_session_and_transport():
        if request.endpoint == "health" or request.endpoint == "static":
            return None
        if app.config["PRODUCTION"] and not request.is_secure:
            return render_template(
                "error.html", message="Use the secure HTTPS address to access this application."
            ), 400
        if current_user.is_authenticated and current_user.must_change_password:
            if request.endpoint not in ("auth.change_password", "auth.logout"):
                flash("Change your temporary password before continuing.", "info")
                return redirect(url_for("auth.change_password"))
        elif session.get("_user_id") and not current_user.is_authenticated:
            logout_user()
            session.clear()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if app.config["PRODUCTION"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.context_processor
    def forms():
        from app.auth.forms import ActionForm

        return {"logout_form": ActionForm()}

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            db.session.rollback()
            return jsonify(status="unavailable"), 503
        return jsonify(status="ok")

    @app.get("/")
    @login_required
    def dashboard():
        recent = []
        counts = {}
        if current_user.role == "admin":
            recent = db.session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)
            ).all()
            from app.models import Submission
            from app.services.campaign_service import CampaignError, list_campaigns

            for state in ("PENDING_APPROVAL", "DRAFT", "CHANGES_REQUESTED", "DRIVE_ERROR"):
                counts[state] = db.session.scalar(
                    select(func.count(Submission.id)).where(Submission.status == state)
                )
            today = utcnow().date()
            counts["APPROVED_TODAY"] = db.session.scalar(
                select(func.count(Submission.id)).where(
                    Submission.status == "APPROVED", func.date(Submission.reviewed_at) == today
                )
            )
            try:
                counts["ACTIVE_CAMPAIGNS"] = len(list_campaigns(active_only=True))
            except CampaignError:
                counts["ACTIVE_CAMPAIGNS"] = "Unavailable"
        else:
            from app.models import Submission

            for state in (
                "DRAFT",
                "PENDING_APPROVAL",
                "CHANGES_REQUESTED",
                "APPROVED",
                "REJECTED",
            ):
                counts[state] = db.session.scalar(
                    select(func.count(Submission.id)).where(
                        Submission.creator_id == current_user.id, Submission.status == state
                    )
                )
        return render_template("dashboard.html", recent=recent, counts=counts)

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: "The request could not be verified. Refresh the page and try again.",
            403: "You do not have permission to access this page.",
            404: "This page could not be found.",
            413: "The upload exceeds the configured size limit.",
            429: "Too many attempts. Try again later.",
        }
        return render_template(
            "error.html", message=messages.get(error.code, "The request could not be completed.")
        ), error.code

    @app.errorhandler(Exception)
    def server_error(error):
        db.session.rollback()
        if app.testing:
            raise error
        # Do not log exception text: upstream Google errors may contain sensitive request details.
        app.logger.error("Unhandled application error: %s", type(error).__name__)
        return render_template("error.html", message="Something went wrong. Please try again."), 500

    @app.cli.command("bootstrap-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True)
    def bootstrap_admin(email, name):
        """Create the first admin; password is prompted, never passed as a CLI argument."""
        from app.services.auth_service import bootstrap_admin as create_admin

        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        try:
            create_admin(name, email, password)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc)) from None
        click.echo("First administrator created.")

    @app.cli.command("cleanup-sessions")
    def cleanup_sessions():
        """Remove expired authentication records; never removes audit history or media."""
        db.session.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        db.session.execute(delete(LoginBucket).where(LoginBucket.expires_at < utcnow()))
        db.session.commit()
        click.echo("Expired authentication records removed.")

    @app.cli.command("process-drive-queue")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    def process_drive_queue(limit):
        """Process a bounded batch of approved Drive packages, then exit."""
        from app.services.drive_commit_service import process_until_empty

        count = process_until_empty(limit)
        click.echo(f"Processed {count} Drive commit attempt(s).")

    @app.cli.command("run-drive-worker")
    @click.option("--poll-seconds", type=click.IntRange(2, 60), default=5, show_default=True)
    def run_drive_worker(poll_seconds):
        """Run the approval-to-Drive worker. This never calls n8n or social APIs."""
        from app.services.drive_commit_service import process_until_empty

        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        click.echo("Drive commit worker started.")
        while not stopping:
            processed = process_until_empty(20)
            if not processed:
                time.sleep(poll_seconds)

    return app
