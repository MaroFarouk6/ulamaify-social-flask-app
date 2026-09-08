from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.admin.forms import CreateUserForm, ResetPasswordForm
from app.admin.review_forms import RequiredNoteForm, ReviewActionForm
from app.auth.forms import ActionForm
from app.extensions import db
from app.models import AuditLog, DriveCommitAttempt, Submission, SubmissionPlatform, User
from app.platforms import PLATFORM_CONFIG, publisher_ready
from app.services import approval_service, auth_service, drive_commit_service
from app.services.audit_service import record
from app.services.auth_service import admin_required
from app.services.drive_service import DriveError

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/users")
@admin_required
def users():
    records = db.paginate(
        select(User).order_by(User.created_at.desc()), per_page=30, max_per_page=30
    )
    return render_template("admin/users.html", records=records, action_form=ActionForm())


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        try:
            auth_service.create_user(
                name=form.name.data,
                email=form.email.data,
                role=form.role.data,
                password=form.password.data,
                must_change=form.must_change.data,
                actor=current_user,
            )
            db.session.commit()
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            record("USER_CREATE_FAILED", "user", details={"reason": "validation_or_duplicate"})
            db.session.commit()
            form.email.errors.append(
                str(exc) if isinstance(exc, ValueError) else "This email is already in use."
            )
        else:
            flash("Account created. Share the temporary password securely.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, title="Create user")


@bp.post("/users/<int:user_id>/<action>")
@admin_required
def user_action(user_id, action):
    if action not in ("enable", "disable"):
        abort(404)
    if not ActionForm().validate_on_submit():
        abort(400)
    try:
        auth_service.set_active(user_id, action == "enable", current_user)
        db.session.commit()
        flash("Account updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        record("USER_STATUS_CHANGE_FAILED", "user", user_id, {"reason": str(exc)})
        db.session.commit()
        flash(str(exc), "error")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["GET", "POST"])
@admin_required
def reset_password(user_id):
    user = db.get_or_404(User, user_id)
    form = ResetPasswordForm()
    if form.validate_on_submit():
        auth_service.reset_password(user, form.password.data, current_user)
        db.session.commit()
        flash("Password reset. The user must change it on their next login.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, title=f"Reset password: {user.name}")


@bp.get("/audit")
@admin_required
def audit():
    query = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    action = request.args.get("action", "").strip()[:80]
    if action:
        query = query.where(AuditLog.action == action)
    records = db.paginate(query, per_page=50, max_per_page=50)
    return render_template("admin/audit.html", records=records, action=action)


@bp.get("/pending")
@admin_required
def pending():
    query = (
        select(Submission)
        .where(Submission.status.in_(["PENDING_APPROVAL", "APPROVING", "DRIVE_ERROR"]))
        .order_by(Submission.submitted_at.asc(), Submission.updated_at.asc())
    )
    records = db.paginate(query, per_page=30, max_per_page=30)
    return render_template("admin/pending.html", records=records)


@bp.get("/review/<submission_id>")
@admin_required
def review(submission_id):
    submission = db.get_or_404(Submission, submission_id)
    readiness = {
        variant.id: bool(
            variant.selected_post_type
            and publisher_ready(variant.platform, variant.selected_post_type, current_app.config)
        )
        for variant in submission.platforms
    }
    return render_template(
        "admin/review.html",
        submission=submission,
        platforms=PLATFORM_CONFIG,
        readiness=readiness,
        approve_form=ReviewActionForm(),
        note_form=RequiredNoteForm(),
    )


@bp.post("/review/<submission_id>/approve")
@admin_required
def approve(submission_id):
    submission = db.get_or_404(Submission, submission_id)
    form = ReviewActionForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        if form.platform.data:
            variant = next(
                (item for item in submission.platforms if item.platform == form.platform.data), None
            )
            if not variant:
                abort(400)
            approval_service.approve_variant(variant.id, current_user, form.note.data)
            blocked = []
        else:
            _, blocked = approval_service.approve_all(submission, current_user, form.note.data)
        db.session.commit()
        flash(
            "Approval recorded. Ready platforms are queued for the Drive commit worker.", "success"
        )
        if blocked:
            flash("Not released: " + ", ".join(blocked), "info")
    except (approval_service.ApprovalError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.review", submission_id=submission.id))


@bp.post("/review/<submission_id>/<action>")
@admin_required
def review_action(submission_id, action):
    if action not in ("request-changes", "reject"):
        abort(404)
    submission = db.get_or_404(Submission, submission_id)
    form = RequiredNoteForm()
    if not form.validate_on_submit():
        flash("A review note is required.", "error")
        return redirect(url_for("admin.review", submission_id=submission.id))
    state = "CHANGES_REQUESTED" if action == "request-changes" else "REJECTED"
    try:
        if form.platform.data:
            variant = next(
                (item for item in submission.platforms if item.platform == form.platform.data), None
            )
            if not variant:
                abort(400)
            approval_service.review_variant(variant.id, current_user, state, form.note.data)
        else:
            approval_service.review_all(submission, current_user, state, form.note.data)
        db.session.commit()
        flash("Review decision saved. Nothing was written to Google Drive.", "success")
    except approval_service.ApprovalError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.review", submission_id=submission.id))


@bp.get("/drive-commits")
@admin_required
def drive_commits():
    query = select(DriveCommitAttempt).order_by(
        DriveCommitAttempt.created_at.desc(), DriveCommitAttempt.id.desc()
    )
    records = db.paginate(query, per_page=50, max_per_page=50)
    return render_template("admin/drive_commits.html", records=records, action_form=ActionForm())


@bp.post("/drive-commits/<int:variant_id>/retry")
@admin_required
def retry_drive_commit(variant_id):
    if not ActionForm().validate_on_submit():
        abort(400)
    variant = db.get_or_404(SubmissionPlatform, variant_id)
    try:
        drive_commit_service.retry_attempt(variant.id, current_user)
        db.session.commit()
        flash("Drive commit queued for reconciliation and retry.", "success")
    except DriveError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.drive_commits"))
