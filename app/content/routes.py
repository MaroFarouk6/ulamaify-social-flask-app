from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.content.forms import ReorderForm, SubmissionForm, SubmitForm, UploadForm
from app.extensions import db
from app.models import MediaAsset, ReviewEvent, Submission
from app.platforms import PLATFORM_CONFIG
from app.services import campaign_service, media_service, submission_service
from app.services.audit_service import record
from app.services.platform_service import refresh_submission

bp = Blueprint("content", __name__, url_prefix="/content")


def get_submission_or_404(submission_id):
    submission = db.session.get(Submission, submission_id)
    if submission is None:
        abort(404)
    try:
        submission_service.ensure_view(submission, current_user)
    except PermissionError:
        abort(403)
    return submission


def active_choices(form):
    campaigns = campaign_service.list_campaigns(active_only=True)
    form.campaign_id.choices = [(item.campaign_id, item.campaign_name) for item in campaigns]
    return campaigns


def form_platform_data():
    selected = request.form.getlist("platforms")
    placements = {key: request.form.get(f"placement_{key}", "standard") for key in selected}
    overrides = {
        key: request.form.get(f"caption_{key}")
        for key in selected
        if request.form.get("customize_captions") == "yes"
    }
    return selected, placements, overrides


@bp.get("")
@login_required
def index():
    status = request.args.get("status", "").upper()
    if status and status not in {
        "DRAFT",
        "PENDING_APPROVAL",
        "CHANGES_REQUESTED",
        "APPROVING",
        "APPROVED",
        "DRIVE_ERROR",
        "REJECTED",
    }:
        abort(400)
    records = db.paginate(
        submission_service.list_for_user(current_user, status or None), per_page=30, max_per_page=30
    )
    return render_template("content/index.html", records=records, status=status)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    form = SubmissionForm()
    try:
        active_choices(form)
    except campaign_service.CampaignError as exc:
        return render_template(
            "content/form.html",
            form=form,
            platforms=PLATFORM_CONFIG,
            error=str(exc),
            submission=None,
        ), 503
    if form.validate_on_submit():
        selected, _, _ = form_platform_data()
        try:
            submission = submission_service.create_submission(
                creator=current_user,
                campaign_id=form.campaign_id.data,
                target_date=form.target_date.data,
                caption=form.default_caption.data or "",
                platforms=selected,
            )
            db.session.commit()
            flash("Draft created. Add media, review compatibility, then submit it.", "success")
            return redirect(url_for("content.edit", submission_id=submission.id))
        except (submission_service.SubmissionError, campaign_service.CampaignError) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template(
        "content/form.html", form=form, platforms=PLATFORM_CONFIG, submission=None
    )


@bp.get("/<submission_id>")
@login_required
def detail(submission_id):
    submission = get_submission_or_404(submission_id)
    events = db.session.scalars(
        select(ReviewEvent)
        .where(ReviewEvent.submission_id == submission.id)
        .order_by(ReviewEvent.created_at.desc())
    ).all()
    return render_template(
        "content/detail.html",
        submission=submission,
        platforms=PLATFORM_CONFIG,
        events=events,
        submit_form=SubmitForm(),
    )


@bp.route("/<submission_id>/edit", methods=["GET", "POST"])
@login_required
def edit(submission_id):
    submission = get_submission_or_404(submission_id)
    try:
        submission_service.ensure_edit(submission, current_user)
    except PermissionError:
        abort(403)
    except submission_service.SubmissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("content.detail", submission_id=submission.id))
    form = SubmissionForm(obj=submission)
    try:
        campaigns = active_choices(form)
    except campaign_service.CampaignError as exc:
        return render_template(
            "content/form.html",
            form=form,
            platforms=PLATFORM_CONFIG,
            error=str(exc),
            submission=submission,
        ), 503
    if request.method == "GET":
        if submission.campaign_id not in {item.campaign_id for item in campaigns}:
            form.campaign_id.choices.append(
                (submission.campaign_id, f"{submission.campaign_name_snapshot} (inactive)")
            )
        form.campaign_id.data = submission.campaign_id
        form.version.data = str(submission.version)
    if form.validate_on_submit():
        selected, placements, overrides = form_platform_data()
        try:
            submission_service.update_submission(
                submission,
                actor=current_user,
                campaign_id=form.campaign_id.data,
                target_date=form.target_date.data,
                caption=form.default_caption.data or "",
                platforms=selected,
                placements=placements,
                caption_overrides=overrides,
                expected_version=int(form.version.data),
            )
            db.session.commit()
            flash("Draft saved.", "success")
            return redirect(url_for("content.edit", submission_id=submission.id))
        except (
            ValueError,
            campaign_service.CampaignError,
            submission_service.SubmissionError,
        ) as exc:
            db.session.rollback()
            flash(str(exc), "error")
    upload_form = UploadForm()
    upload_form.media_scope.choices += [
        (item.platform, f"{PLATFORM_CONFIG[item.platform].label} only")
        for item in submission.platforms
    ]
    return render_template(
        "content/form.html",
        form=form,
        upload_form=upload_form,
        reorder_form=ReorderForm(),
        platforms=PLATFORM_CONFIG,
        submission=submission,
    )


@bp.post("/<submission_id>/media/upload")
@login_required
def upload(submission_id):
    submission = get_submission_or_404(submission_id)
    try:
        submission_service.ensure_edit(submission, current_user)
    except PermissionError:
        abort(403)
    form = UploadForm()
    form.media_scope.choices += [
        (item.platform, f"{PLATFORM_CONFIG[item.platform].label} only")
        for item in submission.platforms
    ]
    if not form.validate_on_submit():
        flash("Select valid media files and a media scope.", "error")
        return redirect(url_for("content.edit", submission_id=submission.id))
    variant = None
    if form.media_scope.data != "shared":
        variant = next(
            (item for item in submission.platforms if item.platform == form.media_scope.data), None
        )
        if variant is None:
            abort(400)
    created = []
    try:
        if len(form.files.data) + len(submission.media_assets) > current_user_app_limit():
            raise media_service.MediaError(
                "This submission would exceed the configured media-file limit."
            )
        for file in form.files.data:
            created.append(media_service.store_upload(submission, file, variant))
        db.session.flush()
        refresh_submission(submission)
        submission.version += 1
        for asset in created:
            record(
                "MEDIA_UPLOADED",
                "media_asset",
                asset.id,
                {
                    "submission_id": submission.id,
                    "mime_type": asset.mime_type,
                    "file_size": asset.file_size,
                },
            )
        db.session.commit()
        flash(f"Uploaded {len(created)} media file(s).", "success")
    except media_service.MediaError as exc:
        db.session.rollback()
        for asset in created:
            try:
                media_service.asset_path(asset).unlink(missing_ok=True)
            except Exception:
                pass
        flash(str(exc), "error")
    return redirect(url_for("content.edit", submission_id=submission.id))


def current_user_app_limit():
    from flask import current_app

    return current_app.config["MAX_MEDIA_FILES"]


@bp.post("/<submission_id>/media/<media_id>/remove")
@login_required
def remove_media(submission_id, media_id):
    submission = get_submission_or_404(submission_id)
    try:
        submission_service.ensure_edit(submission, current_user)
    except PermissionError:
        abort(403)
    form = SubmitForm()
    if not form.validate_on_submit():
        abort(400)
    asset = db.session.get(MediaAsset, media_id)
    if asset is None or asset.submission_id != submission.id:
        abort(404)
    record("MEDIA_REMOVED", "media_asset", asset.id, {"submission_id": submission.id})
    media_service.remove_asset(asset)
    db.session.flush()
    refresh_submission(submission)
    submission.version += 1
    db.session.commit()
    flash("Media removed.", "success")
    return redirect(url_for("content.edit", submission_id=submission.id))


@bp.post("/<submission_id>/media/reorder")
@login_required
def reorder_media(submission_id):
    submission = get_submission_or_404(submission_id)
    try:
        submission_service.ensure_edit(submission, current_user)
    except PermissionError:
        abort(403)
    form = ReorderForm()
    if not form.validate_on_submit():
        abort(400)
    variant = None
    if form.media_scope.data != "shared":
        variant = next(
            (item for item in submission.platforms if item.platform == form.media_scope.data), None
        )
        if variant is None:
            abort(400)
    try:
        media_service.reorder_assets(submission, form.ordered_ids.data.split(","), variant)
        record("MEDIA_REORDERED", "submission", submission.id, {"scope": form.media_scope.data})
        submission.version += 1
        db.session.commit()
        flash("Media order saved.", "success")
    except media_service.MediaError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("content.edit", submission_id=submission.id))


@bp.get("/<submission_id>/media/<media_id>")
@login_required
def media(submission_id, media_id):
    submission = get_submission_or_404(submission_id)
    asset = db.session.get(MediaAsset, media_id)
    if asset is None or asset.submission_id != submission.id:
        abort(404)
    path = media_service.asset_path(asset)
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        mimetype=asset.mime_type,
        download_name=asset.original_filename,
        as_attachment=asset.mime_type == "application/pdf",
        conditional=True,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/<submission_id>/submit")
@login_required
def submit(submission_id):
    submission = get_submission_or_404(submission_id)
    if not SubmitForm().validate_on_submit():
        abort(400)
    try:
        submission_service.submit(submission, current_user)
        db.session.commit()
        flash("Submitted for approval. Nothing has been written to Google Drive.", "success")
    except (
        PermissionError,
        submission_service.SubmissionError,
        campaign_service.CampaignError,
    ) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("content.detail", submission_id=submission.id))
