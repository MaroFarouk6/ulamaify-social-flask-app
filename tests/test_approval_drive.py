import hashlib
import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from conftest import csrf, login
from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, DriveCommitAttempt, Submission, User
from app.services.drive_commit_service import (
    _queue_lock_key,
    process_until_empty,
    retry_attempt,
)
from app.services.drive_service import FOLDER_MIME, DriveError, DriveItem


class FakeDrive:
    def __init__(self):
        self.items = {
            "root": DriveItem("root", "01_CAMPAIGNS", FOLDER_MIME, app_properties={})
        }
        self.parents = {}
        self.contents = {}
        self.operations = []
        self.next_id = 1
        self.fail_upload_name = None

    def _id(self):
        value = f"drive-{self.next_id}"
        self.next_id += 1
        return value

    def seed_folder(self, parent_id, name):
        return self.create_folder(parent_id, name, {})

    def find_children(self, parent_id, name=None, folders_only=False):
        matches = [
            item for item_id, item in self.items.items() if self.parents.get(item_id) == parent_id
        ]
        if name is not None:
            matches = [item for item in matches if item.name == name]
        if folders_only:
            matches = [item for item in matches if item.mime_type == FOLDER_MIME]
        return matches

    def get_item(self, file_id):
        if file_id not in self.items:
            raise DriveError("Missing fake Drive item.")
        return self.items[file_id]

    def create_folder(self, parent_id, name, properties):
        item = DriveItem(self._id(), name, FOLDER_MIME, app_properties=dict(properties))
        self.items[item.id] = item
        self.parents[item.id] = parent_id
        self.operations.append(("folder", parent_id, name))
        return item

    def _upload(self, parent_id, name, data, mime_type, properties):
        if self.fail_upload_name == name:
            raise DriveError(f"Injected failure for {name}.")
        item = DriveItem(
            self._id(),
            name,
            mime_type,
            size=len(data),
            md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            app_properties=dict(properties),
        )
        self.items[item.id] = item
        self.parents[item.id] = parent_id
        self.contents[item.id] = data
        self.operations.append(("upload", parent_id, name))
        return item

    def upload_file(self, parent_id, name, path: Path, mime_type, properties):
        return self._upload(parent_id, name, path.read_bytes(), mime_type, properties)

    def upload_bytes(self, parent_id, name, data, mime_type, properties):
        return self._upload(parent_id, name, data, mime_type, properties)

    def download_bytes(self, file_id):
        return self.contents[file_id]

    def child(self, parent_id, name):
        matches = self.find_children(parent_id, name=name)
        assert len(matches) == 1, (parent_id, name, matches)
        return matches[0]

    def content(self, parent_id, name):
        return self.contents[self.child(parent_id, name).id]


def image_file(name, color="blue"):
    buffer = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(buffer, "JPEG")
    buffer.seek(0)
    return buffer, name


def mp4_file(name="video.mp4"):
    return io.BytesIO(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"), name


def test_queue_lock_key_is_postgresql_text_safe():
    submission = SimpleNamespace(
        campaign_folder_snapshot="TEST-CAMPAIGN",
        target_date=date(2026, 9, 8),
    )
    variant = SimpleNamespace(platform="facebook")

    key = _queue_lock_key(submission, variant)

    assert "\0" not in key
    assert json.loads(key) == ["TEST-CAMPAIGN", "2026-09-08", "facebook"]


def create_and_submit(client, platforms, media_count=1, caption="Different interests. One place to grow."):
    response = client.post(
        "/content/new",
        data={
            "csrf_token": csrf(client, "/content/new"),
            "campaign_id": "test-001",
            "target_date": "2026-09-10",
            "default_caption": caption,
            "platforms": platforms,
        },
    )
    submission_id = response.location.split("/")[-2]
    response = client.post(
        f"/content/{submission_id}/media/upload",
        data={
            "csrf_token": csrf(client, f"/content/{submission_id}/edit"),
            "media_scope": "shared",
            "files": [image_file(f"source-{index}.jpg") for index in range(1, media_count + 1)],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    response = client.post(
        f"/content/{submission_id}/submit",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
    )
    assert response.status_code == 302
    return submission_id


def approve_all(admin_client, submission_id):
    response = admin_client.post(
        f"/admin/review/{submission_id}/approve",
        data={"csrf_token": csrf(admin_client, f"/admin/review/{submission_id}")},
    )
    assert response.status_code == 302


def seed_acceptance_hierarchy(drive):
    campaign = drive.seed_folder("root", "TEST-CAMPAIGN")
    date = drive.seed_folder(campaign.id, "2026-09-10")
    instagram = drive.seed_folder(date.id, "instagram")
    facebook = drive.seed_folder(date.id, "facebook")
    drive.seed_folder(instagram.id, "post-001")
    drive.seed_folder(instagram.id, "post-002")
    drive.seed_folder(facebook.id, "post-001")
    drive.operations.clear()
    return campaign, date, instagram, facebook


def test_nine_image_acceptance_creates_separate_ordered_packages_post_json_last(app, client):
    drive = FakeDrive()
    _, _, instagram, facebook = seed_acceptance_hierarchy(drive)
    app.config.update(
        DRIVE_ADAPTER_FACTORY=lambda: drive,
        CAMPAIGNS_ROOT_FOLDER_ID="root",
        ENABLE_DRIVE_FACEBOOK_CAROUSEL=True,
    )
    login(client, app, "creator@example.com")
    submission_id = create_and_submit(client, ["instagram", "facebook"], media_count=9)

    # Submission and creator submission are staging-only.
    assert drive.operations == []

    admin_client = app.test_client()
    login(admin_client, app)
    approve_all(admin_client, submission_id)
    assert drive.operations == []  # Approval request is queued; worker owns the external write.
    with app.app_context():
        assert process_until_empty() == 2
        submission = db.session.get(Submission, submission_id)
        assert submission.status == "APPROVED"

    instagram_post = drive.child(instagram.id, "post-003")
    facebook_post = drive.child(facebook.id, "post-002")
    for post in (instagram_post, facebook_post):
        media = drive.child(post.id, "media")
        assert [item.name for item in drive.find_children(media.id)] == [
            f"slide{index}.jpg" for index in range(1, 10)
        ]
        assert drive.content(post.id, "caption.txt").decode("utf-8") == (
            "Different interests. One place to grow."
        )
        assert json.loads(drive.content(post.id, "post.json")) == {
            "status": "approved",
            "post_type": "carousel",
            "published": False,
        }
        post_operations = [operation for operation in drive.operations if operation[1] == post.id]
        assert post_operations[-1][2] == "post.json"


def test_rejection_never_calls_drive_and_records_reason(app, client):
    calls = []
    app.config.update(
        DRIVE_ADAPTER_FACTORY=lambda: calls.append(True), CAMPAIGNS_ROOT_FOLDER_ID="root"
    )
    login(client, app, "creator@example.com")
    submission_id = create_and_submit(client, ["facebook"])
    admin_client = app.test_client()
    login(admin_client, app)
    response = admin_client.post(
        f"/admin/review/{submission_id}/reject",
        data={
            "csrf_token": csrf(admin_client, f"/admin/review/{submission_id}"),
            "note": "The claim needs a source.",
        },
    )
    assert response.status_code == 302
    assert calls == []
    with app.app_context():
        submission = db.session.get(Submission, submission_id)
        assert submission.status == "REJECTED"
        event = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "SUBMISSION_REJECTED")
        )
        assert event.details_json["note"] == "The claim needs a source."


def test_failure_before_marker_is_not_publishable_and_retry_reuses_package(app, client):
    drive = FakeDrive()
    app.config.update(DRIVE_ADAPTER_FACTORY=lambda: drive, CAMPAIGNS_ROOT_FOLDER_ID="root")
    drive.fail_upload_name = "caption.txt"
    login(client, app, "creator@example.com")
    submission_id = create_and_submit(client, ["facebook"])
    admin_client = app.test_client()
    login(admin_client, app)
    approve_all(admin_client, submission_id)

    with app.app_context():
        assert process_until_empty() == 1
        submission = db.session.get(Submission, submission_id)
        variant = submission.platforms[0]
        assert variant.status == "DRIVE_ERROR"
        attempt = variant.commit_attempts[0]
        assert attempt.post_folder_name == "post-001"
        assert attempt.drive_post_folder_id
        assert not drive.find_children(attempt.drive_post_folder_id, name="post.json")
        staged = Path(app.config["STAGING_PATH"]) / submission_id / "original"
        assert list(staged.iterdir())

        drive.fail_upload_name = None
        retry_attempt(variant.id, db.session.scalar(select(User).where(User.role == "admin")))
        db.session.commit()
        assert process_until_empty() == 1
        db.session.refresh(variant)
        assert variant.status == "APPROVED"
        assert variant.drive_post_folder_name == "post-001"
        assert json.loads(drive.content(variant.drive_post_folder_id, "post.json"))[
            "published"
        ] is False


def test_facebook_carousel_cannot_release_until_publisher_enabled(app, client):
    drive = FakeDrive()
    app.config.update(DRIVE_ADAPTER_FACTORY=lambda: drive, CAMPAIGNS_ROOT_FOLDER_ID="root")
    login(client, app, "creator@example.com")
    submission_id = create_and_submit(client, ["facebook"], media_count=2)
    admin_client = app.test_client()
    login(admin_client, app)
    response = admin_client.post(
        f"/admin/review/{submission_id}/approve",
        data={"csrf_token": csrf(admin_client, f"/admin/review/{submission_id}")},
        follow_redirects=True,
    )
    assert b"No platform variant is ready for Drive release" in response.data
    with app.app_context():
        assert db.session.scalar(select(DriveCommitAttempt)) is None
    assert drive.operations == []


def test_one_video_creates_independently_numbered_instagram_facebook_youtube_packages(
    app, client
):
    drive = FakeDrive()
    campaign = drive.seed_folder("root", "TEST-CAMPAIGN")
    date = drive.seed_folder(campaign.id, "2026-09-10")
    platforms = {}
    for platform, existing_count in (("instagram", 2), ("facebook", 1), ("youtube", 4)):
        folder = drive.seed_folder(date.id, platform)
        platforms[platform] = folder
        for number in range(1, existing_count + 1):
            drive.seed_folder(folder.id, f"post-{number:03d}")
    drive.operations.clear()
    app.config.update(DRIVE_ADAPTER_FACTORY=lambda: drive, CAMPAIGNS_ROOT_FOLDER_ID="root")
    login(client, app, "creator@example.com")
    response = client.post(
        "/content/new",
        data={
            "csrf_token": csrf(client, "/content/new"),
            "campaign_id": "test-001",
            "target_date": "2026-09-10",
            "default_caption": "One video, three queues",
            "platforms": ["instagram", "facebook", "youtube"],
        },
    )
    submission_id = response.location.split("/")[-2]
    assert client.post(
        f"/content/{submission_id}/media/upload",
        data={
            "csrf_token": csrf(client, f"/content/{submission_id}/edit"),
            "media_scope": "shared",
            "files": [mp4_file()],
        },
        content_type="multipart/form-data",
    ).status_code == 302
    assert client.post(
        f"/content/{submission_id}/submit",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
    ).status_code == 302
    admin_client = app.test_client()
    login(admin_client, app)
    approve_all(admin_client, submission_id)
    with app.app_context():
        assert process_until_empty() == 3
    for platform, expected in (("instagram", "post-003"), ("facebook", "post-002"), ("youtube", "post-005")):
        post = drive.child(platforms[platform].id, expected)
        media = drive.child(post.id, "media")
        assert drive.content(media.id, "video.mp4").startswith(b"\x00\x00\x00\x18ftyp")
        assert json.loads(drive.content(post.id, "post.json"))["post_type"] == "video"
