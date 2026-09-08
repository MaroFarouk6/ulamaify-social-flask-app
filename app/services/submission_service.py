from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models import ReviewEvent, Submission, SubmissionPlatform, utcnow
from app.platforms import PLATFORM_CONFIG
from app.services.audit_service import record
from app.services.campaign_service import validate_selection
from app.services.platform_service import refresh_submission


class SubmissionError(ValueError):
    pass


def can_view(submission: Submission, user) -> bool:
    return user.role == "admin" or submission.creator_id == user.id


def can_edit(submission: Submission, user) -> bool:
    if user.role == "admin":
        return submission.status in {"DRAFT", "PENDING_APPROVAL", "CHANGES_REQUESTED", "REJECTED"}
    return submission.creator_id == user.id and submission.status in {"DRAFT", "CHANGES_REQUESTED"}


def ensure_view(submission: Submission, user) -> None:
    if not can_view(submission, user):
        raise PermissionError("You cannot view this submission.")


def ensure_edit(submission: Submission, user) -> None:
    if not can_edit(submission, user):
        raise PermissionError("This submission cannot be edited in its current state.")
    if any(variant.committed_at for variant in submission.platforms):
        raise SubmissionError("Committed content is locked and cannot be edited.")


def create_submission(
    *, creator, campaign_id: str, target_date: date, caption: str, platforms: list[str]
):
    campaign = validate_selection(campaign_id, target_date)
    selected = normalize_platforms(platforms)
    submission = Submission(
        creator=creator,
        campaign_id=campaign.campaign_id,
        campaign_name_snapshot=campaign.campaign_name,
        campaign_folder_snapshot=campaign.folder_name,
        target_date=target_date,
        default_caption=caption,
    )
    submission.platforms = [SubmissionPlatform(platform=platform) for platform in selected]
    db.session.add(submission)
    db.session.flush()
    refresh_submission(submission)
    record(
        "SUBMISSION_CREATED",
        "submission",
        submission.id,
        {"campaign_id": campaign.campaign_id, "platforms": selected},
        creator,
    )
    return submission


def normalize_platforms(platforms: list[str]) -> list[str]:
    selected = list(dict.fromkeys(platforms))
    if not selected:
        raise SubmissionError("Select at least one platform.")
    if any(platform not in PLATFORM_CONFIG for platform in selected):
        raise SubmissionError("Select only configured platforms.")
    return selected


def update_submission(
    submission,
    *,
    actor,
    campaign_id,
    target_date,
    caption,
    platforms,
    placements,
    caption_overrides,
    expected_version,
):
    ensure_edit(submission, actor)
    if expected_version != submission.version:
        raise SubmissionError("This submission changed in another session. Refresh before editing.")
    campaign = validate_selection(campaign_id, target_date)
    selected = normalize_platforms(platforms)
    existing = {variant.platform: variant for variant in submission.platforms}
    for platform, variant in list(existing.items()):
        if platform not in selected:
            if variant.status in {"APPROVING", "APPROVED"} or variant.committed_at:
                raise SubmissionError(
                    f"{PLATFORM_CONFIG[platform].label} can no longer be removed."
                )
            db.session.delete(variant)
    for platform in selected:
        variant = existing.get(platform)
        if not variant:
            variant = SubmissionPlatform(platform=platform, submission=submission)
            db.session.add(variant)
        placement = placements.get(platform, "standard")
        if placement not in ("standard", "story"):
            raise SubmissionError("Select a valid placement.")
        variant.placement = placement
        override = caption_overrides.get(platform)
        variant.caption_override = override if override is not None and override != "" else None
        if submission.status in {"CHANGES_REQUESTED", "REJECTED"}:
            variant.status = "DRAFT"
            variant.review_note = None
    submission.campaign_id = campaign.campaign_id
    submission.campaign_name_snapshot = campaign.campaign_name
    submission.campaign_folder_snapshot = campaign.folder_name
    submission.target_date = target_date
    submission.default_caption = caption
    submission.status = "DRAFT"
    submission.review_note = None
    submission.version += 1
    db.session.flush()
    refresh_submission(submission)
    record("SUBMISSION_EDITED", "submission", submission.id, {"platforms": selected}, actor)


def submit(submission, actor):
    ensure_edit(submission, actor)
    campaign = validate_selection(submission.campaign_id, submission.target_date)
    if campaign.folder_name != submission.campaign_folder_snapshot:
        raise SubmissionError(
            "The campaign folder changed. Save the draft again before submitting."
        )
    refresh_submission(submission)
    incompatible = [variant for variant in submission.platforms if not variant.is_compatible]
    if incompatible:
        labels = ", ".join(PLATFORM_CONFIG[item.platform].label for item in incompatible)
        raise SubmissionError(f"Resolve incompatible content for: {labels}.")
    if not submission.platforms:
        raise SubmissionError("Select at least one platform.")
    submission.status = "PENDING_APPROVAL"
    submission.submitted_at = utcnow()
    submission.version += 1
    for variant in submission.platforms:
        variant.status = "PENDING_APPROVAL"
        variant.review_note = None
    db.session.add(
        ReviewEvent(
            submission_id=submission.id,
            actor_id=actor.id,
            actor_name=actor.name,
            action="SUBMITTED",
        )
    )
    record(
        "SUBMISSION_SUBMITTED",
        "submission",
        submission.id,
        {"platforms": [item.platform for item in submission.platforms]},
        actor,
    )


def list_for_user(user, status=None):
    query = select(Submission).order_by(Submission.updated_at.desc())
    if user.role != "admin":
        query = query.where(Submission.creator_id == user.id)
    if status:
        query = query.where(Submission.status == status)
    return query


def aggregate_status(submission):
    states = {variant.status for variant in submission.platforms}
    if not states:
        return "DRAFT"
    if states == {"APPROVED"}:
        return "APPROVED"
    for state in (
        "APPROVING",
        "DRIVE_ERROR",
        "PENDING_APPROVAL",
        "CHANGES_REQUESTED",
        "DRAFT",
        "REJECTED",
    ):
        if state in states:
            return state
    return "DRAFT"
