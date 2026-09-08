import io
import re

from conftest import csrf, login
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, MediaAsset, Submission


def image_file(name="photo.jpg", format="JPEG", color="blue"):
    buffer = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(buffer, format)
    buffer.seek(0)
    return buffer, name


def pdf_file(name="document.pdf"):
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    writer.write(buffer)
    buffer.seek(0)
    return buffer, name


def mp4_file(name="video.mp4"):
    return io.BytesIO(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"), name


def create_draft(client, campaign="test-001", platforms=("instagram",), caption="Hello 🌟\n#Ulamaify"):
    response = client.post(
        "/content/new",
        data={
            "csrf_token": csrf(client, "/content/new"),
            "campaign_id": campaign,
            "target_date": "2026-09-10",
            "default_caption": caption,
            "platforms": list(platforms),
        },
    )
    assert response.status_code == 302, response.data.decode()
    return response.location.split("/")[-2]


def upload(client, submission_id, files, scope="shared"):
    path = f"/content/{submission_id}/edit"
    return client.post(
        f"/content/{submission_id}/media/upload",
        data={"csrf_token": csrf(client, path), "media_scope": scope, "files": files},
        content_type="multipart/form-data",
    )


def test_create_draft_and_upload_image_stays_local(app, client, sheet):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("instagram", "facebook"))
    response = upload(client, submission_id, [image_file()])
    assert response.status_code == 302
    with app.app_context():
        submission = db.session.get(Submission, submission_id)
        assert submission.status == "DRAFT"
        assert [variant.selected_post_type for variant in submission.platforms] == ["image", "image"]
        asset = submission.media_assets[0]
        assert asset.staged_name != asset.original_filename
        assert re.fullmatch(r"[0-9a-f-]{36}\.jpg", asset.staged_name)
        assert (app.config["STAGING_PATH"] + f"/{submission_id}/original/{asset.staged_name}")
    assert not hasattr(sheet, "drive_writes")


def test_invalid_signature_rejected_and_not_recorded(app, client):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("facebook",))
    response = upload(client, submission_id, [(io.BytesIO(b"not an image"), "fake.jpg")])
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(select(MediaAsset).where(MediaAsset.submission_id == submission_id)) is None


def test_pdf_and_mp4_signature_detection(app, client):
    login(client, app, "creator@example.com")
    pdf_submission = create_draft(client, platforms=("linkedin",))
    assert upload(client, pdf_submission, [pdf_file()]).status_code == 302
    video_submission = create_draft(client, platforms=("youtube",))
    assert upload(client, video_submission, [mp4_file()]).status_code == 302
    with app.app_context():
        assert db.session.get(Submission, pdf_submission).platforms[0].selected_post_type == "document"
        assert db.session.get(Submission, video_submission).platforms[0].selected_post_type == "video"


def test_creator_submit_never_calls_drive(app, client):
    called = []
    app.config["DRIVE_ADAPTER_FACTORY"] = lambda: called.append(True)
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("instagram", "facebook"))
    upload(client, submission_id, [image_file()])
    response = client.post(
        f"/content/{submission_id}/submit",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
        follow_redirects=True,
    )
    assert b"Nothing has been written to Google Drive" in response.data
    assert called == []
    with app.app_context():
        submission = db.session.get(Submission, submission_id)
        assert submission.status == "PENDING_APPROVAL"
        assert {item.status for item in submission.platforms} == {"PENDING_APPROVAL"}
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "SUBMISSION_SUBMITTED"))


def test_incompatible_youtube_blocked_until_custom_video_added(app, client):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("instagram", "youtube"))
    upload(client, submission_id, [image_file(f"slide-{i}.jpg") for i in range(5)])
    response = client.post(
        f"/content/{submission_id}/submit",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
        follow_redirects=True,
    )
    assert b"Resolve incompatible content for: YouTube" in response.data
    upload(client, submission_id, [mp4_file()], scope="youtube")
    response = client.post(
        f"/content/{submission_id}/submit",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
    )
    assert response.status_code == 302
    with app.app_context():
        submission = db.session.get(Submission, submission_id)
        types = {item.platform: item.selected_post_type for item in submission.platforms}
        assert types == {"instagram": "carousel", "youtube": "video"}


def test_creator_cannot_view_or_change_another_creator_submission(app, client):
    login(client, app)
    submission_id = create_draft(client, platforms=("facebook",))
    other = app.test_client()
    login(other, app, "creator@example.com")
    assert other.get(f"/content/{submission_id}").status_code == 403
    assert other.get(f"/content/{submission_id}/edit").status_code == 403
    assert other.post(f"/content/{submission_id}/submit").status_code == 400  # CSRF precedes authorization


def test_pending_creator_cannot_edit_or_remove_media(app, client):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("facebook",))
    upload(client, submission_id, [image_file()])
    client.post(f"/content/{submission_id}/submit", data={"csrf_token": csrf(client, f"/content/{submission_id}")})
    assert client.get(f"/content/{submission_id}/edit").status_code == 403
    with app.app_context():
        media_id = db.session.get(Submission, submission_id).media_assets[0].id
    assert client.post(
        f"/content/{submission_id}/media/{media_id}/remove",
        data={"csrf_token": csrf(client, f"/content/{submission_id}")},
    ).status_code == 403


def test_carousel_reorder_persisted(app, client):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("instagram",))
    upload(client, submission_id, [image_file("one.jpg", color="red"), image_file("two.jpg", color="green")])
    with app.app_context():
        ids = [item.id for item in db.session.get(Submission, submission_id).media_assets]
    response = client.post(
        f"/content/{submission_id}/media/reorder",
        data={
            "csrf_token": csrf(client, f"/content/{submission_id}/edit"),
            "media_scope": "shared",
            "ordered_ids": ",".join(reversed(ids)),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        ordered = db.session.scalars(
            select(MediaAsset).where(MediaAsset.submission_id == submission_id).order_by(MediaAsset.sort_order)
        ).all()
        assert [item.id for item in ordered] == list(reversed(ids))


def test_caption_override_and_story_saved(app, client):
    login(client, app, "creator@example.com")
    submission_id = create_draft(client, platforms=("instagram", "facebook"))
    upload(client, submission_id, [image_file()])
    with app.app_context():
        version = db.session.get(Submission, submission_id).version
    response = client.post(
        f"/content/{submission_id}/edit",
        data={
            "csrf_token": csrf(client, f"/content/{submission_id}/edit"),
            "campaign_id": "test-001",
            "target_date": "2026-09-10",
            "default_caption": "Shared",
            "platforms": ["instagram", "facebook"],
            "customize_captions": "yes",
            "caption_instagram": "Instagram only",
            "placement_instagram": "story",
            "version": str(version),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        submission = db.session.get(Submission, submission_id)
        instagram = next(item for item in submission.platforms if item.platform == "instagram")
        assert instagram.selected_post_type == "story"
        assert instagram.effective_caption == "Instagram only"


def test_text_only_platform_rules(app, client):
    login(client, app, "creator@example.com")
    linkedin = create_draft(client, platforms=("linkedin",))
    facebook = create_draft(client, platforms=("facebook",))
    instagram = create_draft(client, platforms=("instagram",))
    with app.app_context():
        assert db.session.get(Submission, linkedin).platforms[0].is_compatible
        assert db.session.get(Submission, facebook).platforms[0].is_compatible
        assert not db.session.get(Submission, instagram).platforms[0].is_compatible
