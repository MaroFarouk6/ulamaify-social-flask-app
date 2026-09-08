import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.extensions import db
from app.models import utcnow

SUBMISSION_STATES = (
    "DRAFT",
    "PENDING_APPROVAL",
    "CHANGES_REQUESTED",
    "APPROVING",
    "APPROVED",
    "DRIVE_ERROR",
    "REJECTED",
)
VARIANT_STATES = SUBMISSION_STATES
REVIEW_ACTIONS = ("SUBMITTED", "CHANGES_REQUESTED", "REJECTED", "APPROVED")
COMMIT_STATES = ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "UNCERTAIN")


def new_uuid() -> str:
    return str(uuid.uuid4())


class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','CHANGES_REQUESTED','APPROVING','APPROVED','DRIVE_ERROR','REJECTED')",
            name="valid_submission_status",
        ),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_uuid)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = db.Column(db.String(100), nullable=False, index=True)
    campaign_name_snapshot = db.Column(db.String(200), nullable=False)
    campaign_folder_snapshot = db.Column(db.String(200), nullable=False)
    target_date = db.Column(db.Date, nullable=False, index=True)
    default_caption = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(30), nullable=False, default="DRAFT", index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    submitted_at = db.Column(db.DateTime)
    reviewed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    review_note = db.Column(db.Text)

    creator = db.relationship("User", foreign_keys=[creator_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    platforms = db.relationship(
        "SubmissionPlatform",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionPlatform.id",
    )
    media_assets = db.relationship(
        "MediaAsset",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="MediaAsset.sort_order",
    )


class SubmissionPlatform(db.Model):
    __tablename__ = "submission_platforms"
    __table_args__ = (
        UniqueConstraint("submission_id", "platform", name="submission_platform_unique"),
        CheckConstraint(
            "platform IN ('instagram','facebook','linkedin','youtube')", name="valid_platform"
        ),
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','CHANGES_REQUESTED','APPROVING','APPROVED','DRIVE_ERROR','REJECTED')",
            name="valid_variant_status",
        ),
        CheckConstraint(
            "detected_post_type IS NULL OR detected_post_type IN ('text','image','video','carousel','story','document')",
            name="valid_detected_type",
        ),
        CheckConstraint(
            "selected_post_type IS NULL OR selected_post_type IN ('text','image','video','carousel','story','document')",
            name="valid_selected_type",
        ),
        CheckConstraint("placement IN ('standard','story')", name="valid_placement"),
    )
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.String(36), db.ForeignKey("submissions.id"), nullable=False, index=True
    )
    platform = db.Column(db.String(20), nullable=False)
    detected_post_type = db.Column(db.String(20))
    selected_post_type = db.Column(db.String(20))
    placement = db.Column(db.String(20), nullable=False, default="standard")
    caption_override = db.Column(db.Text)
    compatibility_message = db.Column(db.String(500))
    is_compatible = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(30), nullable=False, default="DRAFT", index=True)
    review_note = db.Column(db.Text)
    reviewed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    drive_post_folder_id = db.Column(db.String(200))
    drive_post_folder_name = db.Column(db.String(20))
    drive_post_json_file_id = db.Column(db.String(200))
    committed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    submission = db.relationship("Submission", back_populates="platforms")
    reviewed_by = db.relationship("User")
    media_assets = db.relationship(
        "MediaAsset",
        back_populates="platform_variant",
        cascade="all, delete-orphan",
        order_by="MediaAsset.sort_order",
    )
    commit_attempts = db.relationship(
        "DriveCommitAttempt", back_populates="variant", order_by="DriveCommitAttempt.created_at"
    )

    @property
    def effective_caption(self) -> str:
        return (
            self.caption_override
            if self.caption_override is not None
            else self.submission.default_caption
        )

    @property
    def effective_media(self):
        return self.media_assets or [
            asset for asset in self.submission.media_assets if asset.submission_platform_id is None
        ]


class MediaAsset(db.Model):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("staged_name", name="media_staged_name_unique"),
        CheckConstraint("sort_order >= 1", name="positive_media_order"),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_uuid)
    submission_id = db.Column(
        db.String(36), db.ForeignKey("submissions.id"), nullable=False, index=True
    )
    submission_platform_id = db.Column(
        db.Integer, db.ForeignKey("submission_platforms.id"), index=True
    )
    original_filename = db.Column(db.String(255), nullable=False)
    staged_name = db.Column(db.String(80), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    checksum = db.Column(db.String(64), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    submission = db.relationship("Submission", back_populates="media_assets")
    platform_variant = db.relationship("SubmissionPlatform", back_populates="media_assets")


class ReviewEvent(db.Model):
    __tablename__ = "review_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('SUBMITTED','CHANGES_REQUESTED','REJECTED','APPROVED')",
            name="valid_review_action",
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.String(36), db.ForeignKey("submissions.id"), nullable=False, index=True
    )
    submission_platform_id = db.Column(
        db.Integer, db.ForeignKey("submission_platforms.id"), index=True
    )
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    actor_name = db.Column(db.String(120), nullable=False)
    action = db.Column(db.String(30), nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    actor = db.relationship("User")


class DriveCommitAttempt(db.Model):
    __tablename__ = "drive_commit_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','COMPLETED','FAILED','UNCERTAIN')",
            name="valid_commit_status",
        ),
    )
    id = db.Column(db.String(36), primary_key=True, default=new_uuid)
    submission_platform_id = db.Column(
        db.Integer, db.ForeignKey("submission_platforms.id"), nullable=False, index=True
    )
    status = db.Column(db.String(20), nullable=False, default="QUEUED", index=True)
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    post_number = db.Column(db.Integer)
    post_folder_name = db.Column(db.String(20))
    drive_campaign_folder_id = db.Column(db.String(200))
    drive_date_folder_id = db.Column(db.String(200))
    drive_platform_folder_id = db.Column(db.String(200))
    drive_post_folder_id = db.Column(db.String(200))
    drive_media_folder_id = db.Column(db.String(200))
    drive_caption_file_id = db.Column(db.String(200))
    drive_post_json_file_id = db.Column(db.String(200))
    error_code = db.Column(db.String(80))
    error_message = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)

    variant = db.relationship("SubmissionPlatform", back_populates="commit_attempts")


class QueueAllocation(db.Model):
    __tablename__ = "queue_allocations"
    __table_args__ = (
        UniqueConstraint(
            "campaign_folder", "target_date", "platform", name="queue_allocation_unique"
        ),
        CheckConstraint(
            "platform IN ('instagram','facebook','linkedin','youtube')", name="valid_queue_platform"
        ),
        CheckConstraint("last_number >= 0", name="nonnegative_queue_number"),
    )
    id = db.Column(db.Integer, primary_key=True)
    campaign_folder = db.Column(db.String(200), nullable=False)
    target_date = db.Column(db.Date, nullable=False)
    platform = db.Column(db.String(20), nullable=False)
    last_number = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
