from sqlalchemy import select, text

from app.extensions import db
from app.models import DriveCommitAttempt, QueueAllocation, SubmissionPlatform, utcnow
from app.platforms import publisher_ready
from app.services.audit_service import record
from app.services.campaign_service import validate_selection
from app.services.drive_service import (
    DriveError,
    DriveUncertainError,
    adapter,
    attempt_properties,
    find_attempt_item,
    find_exactly_one_or_create,
    parse_post_numbers,
    post_json_bytes,
    upload_idempotent_bytes,
    upload_idempotent_file,
)
from app.services.media_normalization_service import NormalizedPackage
from app.services.platform_service import refresh_submission
from app.services.submission_service import aggregate_status


def claim_next_attempt():
    statement = (
        select(DriveCommitAttempt)
        .where(DriveCommitAttempt.status == "QUEUED")
        .order_by(DriveCommitAttempt.created_at, DriveCommitAttempt.id)
    )
    if db.engine.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    attempt = db.session.scalar(statement.limit(1))
    if not attempt:
        return None
    attempt.status = "RUNNING"
    attempt.started_at = utcnow()
    attempt.error_code = None
    attempt.error_message = None
    db.session.commit()
    return attempt.id


def ensure_hierarchy(attempt, client):
    variant, submission = attempt.variant, attempt.variant.submission
    properties = attempt_properties(attempt.id)
    campaign, created = find_exactly_one_or_create(
        client,
        current_root_id(),
        submission.campaign_folder_snapshot,
        {**properties, "level": "campaign"},
    )
    attempt.drive_campaign_folder_id = campaign.id
    audit_folder(attempt, campaign, created, "campaign")
    date_folder, created = find_exactly_one_or_create(
        client, campaign.id, submission.target_date.isoformat(), {**properties, "level": "date"}
    )
    attempt.drive_date_folder_id = date_folder.id
    audit_folder(attempt, date_folder, created, "date")
    platform_folder, created = find_exactly_one_or_create(
        client, date_folder.id, variant.platform, {**properties, "level": "platform"}
    )
    attempt.drive_platform_folder_id = platform_folder.id
    audit_folder(attempt, platform_folder, created, "platform")


def lock_queue(attempt):
    submission, variant = attempt.variant.submission, attempt.variant
    key = f"{submission.campaign_folder_snapshot}\0{submission.target_date}\0{variant.platform}"
    if db.engine.dialect.name == "postgresql":
        # Kept in the current transaction through hierarchy and post-folder creation.
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )


def current_root_id():
    from flask import current_app

    root = current_app.config["CAMPAIGNS_ROOT_FOLDER_ID"]
    if not root:
        raise DriveError("The campaigns Drive folder is not configured.")
    return root


def audit_folder(attempt, item, created, level):
    if created:
        record(
            "DRIVE_FOLDER_CREATED",
            "drive_commit_attempt",
            attempt.id,
            {"level": level, "name": item.name, "drive_id": item.id},
        )


def allocate_post_folder(attempt, client):
    if attempt.post_number and attempt.post_folder_name and attempt.drive_post_folder_id:
        client.get_item(attempt.drive_post_folder_id)
        return
    submission, variant = attempt.variant.submission, attempt.variant
    if attempt.post_number and attempt.post_folder_name:
        matches = client.find_children(
            attempt.drive_platform_folder_id, name=attempt.post_folder_name, folders_only=True
        )
        owned = [
            item
            for item in matches
            if (item.app_properties or {}).get("commit_attempt_id") == attempt.id
        ]
        if len(owned) == 1:
            attempt.drive_post_folder_id = owned[0].id
            return
        if matches:
            raise DriveError(
                f"Drive already contains {attempt.post_folder_name}, but it does not belong to this commit attempt."
            )
        next_number = attempt.post_number
        folder_name = attempt.post_folder_name
    else:
        statement = select(QueueAllocation).where(
            QueueAllocation.campaign_folder == submission.campaign_folder_snapshot,
            QueueAllocation.target_date == submission.target_date,
            QueueAllocation.platform == variant.platform,
        )
        if db.engine.dialect.name == "postgresql":
            statement = statement.with_for_update()
        allocation = db.session.scalar(statement)
        if not allocation:
            allocation = QueueAllocation(
                campaign_folder=submission.campaign_folder_snapshot,
                target_date=submission.target_date,
                platform=variant.platform,
                last_number=0,
            )
            db.session.add(allocation)
            db.session.flush()
        existing = client.find_children(attempt.drive_platform_folder_id, folders_only=True)
        next_number = max([allocation.last_number, *parse_post_numbers(existing)]) + 1
        folder_name = f"post-{next_number:03d}"
        if any(item.name == folder_name for item in existing):
            raise DriveError(
                "The next Drive post folder already exists. Retry after refreshing the queue."
            )
        allocation.last_number = next_number
        attempt.post_number = next_number
        attempt.post_folder_name = folder_name
    try:
        folder = client.create_folder(
            attempt.drive_platform_folder_id,
            folder_name,
            {**attempt_properties(attempt.id), "level": "post", "post_number": str(next_number)},
        )
    except DriveUncertainError:
        folder = find_attempt_item(
            client, attempt.drive_platform_folder_id, folder_name, attempt.id
        )
        if folder is None:
            # Keep the number reserved even when Drive cannot confirm folder creation.
            db.session.commit()
            raise
    except Exception:
        # Do not reuse a number already selected for an attempted Drive write.
        db.session.commit()
        raise
    attempt.drive_post_folder_id = folder.id
    record(
        "DRIVE_FOLDER_CREATED",
        "drive_commit_attempt",
        attempt.id,
        {"level": "post", "name": folder_name, "drive_id": folder.id},
    )


def process_attempt(attempt_id):
    from flask import current_app

    attempt = db.session.get(DriveCommitAttempt, attempt_id)
    if not attempt or attempt.status != "RUNNING":
        return False
    variant, submission = attempt.variant, attempt.variant.submission
    try:
        campaign = validate_selection(submission.campaign_id, submission.target_date)
        if campaign.folder_name != submission.campaign_folder_snapshot:
            raise DriveError("Campaign folder changed after approval. Admin review is required.")
        refresh_submission(submission)
        if not variant.is_compatible or not variant.selected_post_type:
            raise DriveError("The approved platform content is no longer compatible.")
        if not publisher_ready(variant.platform, variant.selected_post_type, current_app.config):
            raise DriveError("The n8n publisher is not enabled for this approved content type.")
        client = adapter()
        record("DRIVE_COMMIT_STARTED", "drive_commit_attempt", attempt.id)
        db.session.commit()
        lock_queue(attempt)
        ensure_hierarchy(attempt, client)
        allocate_post_folder(attempt, client)
        db.session.commit()
        media_folder, created = find_exactly_one_or_create(
            client,
            attempt.drive_post_folder_id,
            "media",
            {**attempt_properties(attempt.id), "level": "media"},
        )
        attempt.drive_media_folder_id = media_folder.id
        audit_folder(attempt, media_folder, created, "media")
        db.session.commit()
        with NormalizedPackage(variant) as package:
            for item in package.files:
                uploaded, created = upload_idempotent_file(
                    client,
                    media_folder.id,
                    item.filename,
                    item.path,
                    item.mime_type,
                    item.checksum,
                    attempt.id,
                )
                if created:
                    record(
                        "DRIVE_FILE_UPLOADED",
                        "drive_commit_attempt",
                        attempt.id,
                        {"name": item.filename, "drive_id": uploaded.id, "kind": "media"},
                    )
            caption = variant.effective_caption.encode("utf-8")
            caption_item, created = upload_idempotent_bytes(
                client,
                attempt.drive_post_folder_id,
                "caption.txt",
                caption,
                "text/plain; charset=utf-8",
                attempt.id,
            )
            attempt.drive_caption_file_id = caption_item.id
            if created:
                record(
                    "DRIVE_FILE_UPLOADED",
                    "drive_commit_attempt",
                    attempt.id,
                    {"name": "caption.txt", "drive_id": caption_item.id, "kind": "caption"},
                )
            db.session.commit()
            # Commit marker is deliberately the final Drive write.
            marker = post_json_bytes(variant.selected_post_type)
            marker_item, created = upload_idempotent_bytes(
                client,
                attempt.drive_post_folder_id,
                "post.json",
                marker,
                "application/json; charset=utf-8",
                attempt.id,
            )
            attempt.drive_post_json_file_id = marker_item.id
            if created:
                record(
                    "DRIVE_FILE_UPLOADED",
                    "drive_commit_attempt",
                    attempt.id,
                    {"name": "post.json", "drive_id": marker_item.id, "kind": "commit_marker"},
                )
        attempt.status = "COMPLETED"
        attempt.finished_at = utcnow()
        variant.status = "APPROVED"
        variant.drive_post_folder_id = attempt.drive_post_folder_id
        variant.drive_post_folder_name = attempt.post_folder_name
        variant.drive_post_json_file_id = attempt.drive_post_json_file_id
        variant.committed_at = utcnow()
        submission.status = aggregate_status(submission)
        record(
            "DRIVE_COMMIT_COMPLETED",
            "drive_commit_attempt",
            attempt.id,
            {"post_folder": attempt.post_folder_name, "platform": variant.platform},
        )
        db.session.commit()
        return True
    except DriveUncertainError as exc:
        fail_attempt(attempt, "UNCERTAIN", "DRIVE_RESULT_UNCERTAIN", str(exc))
    except Exception as exc:
        # Expose only application-owned messages. Never persist raw Google credential/request details.
        message = (
            str(exc)
            if isinstance(exc, (DriveError, ValueError))
            else "Drive commit failed unexpectedly."
        )
        fail_attempt(attempt, "FAILED", type(exc).__name__[:80], message[:500])
    return False


def fail_attempt(attempt, status, code, message):
    db.session.rollback()
    attempt = db.session.get(DriveCommitAttempt, attempt.id)
    attempt.status = status
    attempt.error_code = code
    attempt.error_message = message
    attempt.finished_at = utcnow()
    attempt.variant.status = "DRIVE_ERROR"
    attempt.variant.submission.status = aggregate_status(attempt.variant.submission)
    record(
        "DRIVE_COMMIT_FAILED",
        "drive_commit_attempt",
        attempt.id,
        {"status": status, "error_code": code, "message": message},
    )
    db.session.commit()


def retry_attempt(variant_id, actor):
    variant = db.session.get(SubmissionPlatform, variant_id)
    if not variant or variant.status != "DRIVE_ERROR":
        raise DriveError("This platform variant has no retryable Drive error.")
    attempt = db.session.scalar(
        select(DriveCommitAttempt)
        .where(DriveCommitAttempt.submission_platform_id == variant.id)
        .order_by(DriveCommitAttempt.created_at.desc())
    )
    if not attempt or attempt.status not in {"FAILED", "UNCERTAIN"}:
        raise DriveError("No retryable Drive commit attempt was found.")
    attempt.status = "QUEUED"
    attempt.attempt_number += 1
    attempt.error_code = None
    attempt.error_message = None
    attempt.finished_at = None
    variant.status = "APPROVING"
    variant.submission.status = aggregate_status(variant.submission)
    record(
        "DRIVE_COMMIT_RETRIED",
        "drive_commit_attempt",
        attempt.id,
        {"submission_platform_id": variant.id},
        actor,
    )
    return attempt


def process_until_empty(limit=20):
    processed = 0
    while processed < limit and (attempt_id := claim_next_attempt()):
        process_attempt(attempt_id)
        processed += 1
    return processed
