from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import DriveCommitAttempt, ReviewEvent, SubmissionPlatform, utcnow
from app.platforms import PLATFORM_CONFIG, publisher_ready
from app.services.audit_service import record
from app.services.campaign_service import validate_selection
from app.services.platform_service import refresh_submission
from app.services.submission_service import aggregate_status


class ApprovalError(ValueError):
    pass


def lock_variant(variant_id):
    statement = select(SubmissionPlatform).where(SubmissionPlatform.id == variant_id)
    if db.engine.dialect.name == "postgresql":
        statement = statement.with_for_update()
    variant = db.session.scalar(statement)
    if variant is None:
        raise ApprovalError("Platform variant not found.")
    return variant


def _ensure_admin(actor):
    if not actor.is_active or actor.role != "admin":
        raise PermissionError("Administrator access is required.")


def approve_variant(variant_id, actor, note=None):
    _ensure_admin(actor)
    variant = lock_variant(variant_id)
    submission = variant.submission
    allowed = variant.status == "PENDING_APPROVAL" or (
        submission.creator_id == actor.id and variant.status == "DRAFT"
    )
    if not allowed:
        raise ApprovalError("This platform variant is not ready for approval.")
    validate_selection(submission.campaign_id, submission.target_date)
    refresh_submission(submission)
    if not variant.is_compatible or not variant.selected_post_type:
        raise ApprovalError(
            variant.compatibility_message or "This platform variant is incompatible."
        )
    if not publisher_ready(variant.platform, variant.selected_post_type, current_app.config):
        label = PLATFORM_CONFIG[variant.platform].label
        raise ApprovalError(
            f"{label} {variant.selected_post_type} content can be prepared, but its n8n publisher is not enabled for Drive release."
        )
    previous_attempts = db.session.scalar(
        select(func.count(DriveCommitAttempt.id)).where(
            DriveCommitAttempt.submission_platform_id == variant.id
        )
    )
    attempt = DriveCommitAttempt(
        variant=variant, status="QUEUED", attempt_number=(previous_attempts or 0) + 1
    )
    db.session.add(attempt)
    variant.status = "APPROVING"
    variant.reviewed_at = utcnow()
    variant.reviewed_by_id = actor.id
    variant.review_note = note or None
    submission.reviewed_at = utcnow()
    submission.reviewed_by_id = actor.id
    submission.status = aggregate_status(submission)
    db.session.add(
        ReviewEvent(
            submission_id=submission.id,
            submission_platform_id=variant.id,
            actor_id=actor.id,
            actor_name=actor.name,
            action="APPROVED",
            note=note or None,
        )
    )
    record(
        "SUBMISSION_APPROVED",
        "submission_platform",
        variant.id,
        {
            "submission_id": submission.id,
            "platform": variant.platform,
            "commit_attempt_id": attempt.id,
        },
        actor,
    )
    record(
        "DRIVE_COMMIT_QUEUED",
        "drive_commit_attempt",
        attempt.id,
        {"submission_platform_id": variant.id},
        actor,
    )
    return attempt


def approve_all(submission, actor, note=None):
    _ensure_admin(actor)
    queued, blocked = [], []
    for original in list(submission.platforms):
        if original.status not in {"PENDING_APPROVAL", "DRAFT"}:
            continue
        if original.status == "DRAFT" and submission.creator_id != actor.id:
            continue
        if not original.is_compatible or not original.selected_post_type:
            blocked.append(PLATFORM_CONFIG[original.platform].label)
            continue
        if not publisher_ready(original.platform, original.selected_post_type, current_app.config):
            blocked.append(
                f"{PLATFORM_CONFIG[original.platform].label} {original.selected_post_type} (publisher not enabled)"
            )
            continue
        queued.append(approve_variant(original.id, actor, note))
    if not queued:
        raise ApprovalError("No platform variant is ready for Drive release.")
    submission.status = aggregate_status(submission)
    return queued, blocked


def review_variant(variant_id, actor, action, note):
    _ensure_admin(actor)
    if action not in {"CHANGES_REQUESTED", "REJECTED"}:
        raise ApprovalError("Invalid review action.")
    if not note or not note.strip():
        raise ApprovalError("A review note is required.")
    variant = lock_variant(variant_id)
    if variant.status not in {"PENDING_APPROVAL", "DRAFT", "CHANGES_REQUESTED"}:
        raise ApprovalError("This platform variant can no longer be reviewed.")
    variant.status = action
    variant.review_note = note.strip()
    variant.reviewed_at = utcnow()
    variant.reviewed_by_id = actor.id
    submission = variant.submission
    submission.reviewed_at = utcnow()
    submission.reviewed_by_id = actor.id
    submission.review_note = (
        note.strip() if action == "CHANGES_REQUESTED" else submission.review_note
    )
    submission.status = aggregate_status(submission)
    db.session.add(
        ReviewEvent(
            submission_id=submission.id,
            submission_platform_id=variant.id,
            actor_id=actor.id,
            actor_name=actor.name,
            action=action,
            note=note.strip(),
        )
    )
    audit_action = "CHANGES_REQUESTED" if action == "CHANGES_REQUESTED" else "SUBMISSION_REJECTED"
    record(
        audit_action,
        "submission_platform",
        variant.id,
        {"submission_id": submission.id, "platform": variant.platform, "note": note.strip()},
        actor,
    )


def review_all(submission, actor, action, note):
    candidates = [
        variant
        for variant in submission.platforms
        if variant.status in {"PENDING_APPROVAL", "DRAFT", "CHANGES_REQUESTED"}
        and not variant.committed_at
    ]
    if not candidates:
        raise ApprovalError("No platform variants can be reviewed.")
    for variant in candidates:
        review_variant(variant.id, actor, action, note)
    submission.status = aggregate_status(submission)
