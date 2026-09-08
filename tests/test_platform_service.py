from types import SimpleNamespace

import pytest

from app.platforms import publisher_ready
from app.services.platform_service import detect_type, determine_variant


def media(*mimes):
    return [SimpleNamespace(mime_type=value) for value in mimes]


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ((), "text"),
        (("image/jpeg",), "image"),
        (("image/jpeg", "image/png"), "carousel"),
        (("video/mp4",), "video"),
        (("application/pdf",), "document"),
    ],
)
def test_detection(items, expected):
    assert detect_type(media(*items)) == (expected, None)


def test_mixed_media_requires_review():
    result = determine_variant("instagram", media("image/jpeg", "video/mp4"))
    assert not result.compatible
    assert result.post_type is None
    assert "Mixed media" in result.message


def test_story_is_explicit_override():
    normal = determine_variant("instagram", media("image/jpeg"), "standard")
    story = determine_variant("instagram", media("image/jpeg"), "story")
    assert normal.post_type == "image"
    assert story.post_type == "story"
    assert story.compatible


def test_instagram_text_and_youtube_image_rejected():
    assert not determine_variant("instagram", []).compatible
    result = determine_variant("youtube", media("image/jpeg"))
    assert not result.compatible
    assert "requires video" in result.message


def test_facebook_carousel_valid_for_creation_without_guessed_maximum():
    result = determine_variant("facebook", media(*(["image/jpeg"] * 12)))
    assert result.compatible
    assert result.post_type == "carousel"


def test_instagram_carousel_limit():
    assert determine_variant("instagram", media(*(["image/jpeg"] * 10))).compatible
    result = determine_variant("instagram", media(*(["image/jpeg"] * 11)))
    assert not result.compatible
    assert "maximum of 10" in result.message


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ((), "text"),
        (("image/jpeg",), "image"),
        (("application/pdf",), "document"),
    ],
)
def test_linkedin_confirmed_creation_types(items, expected):
    result = determine_variant("linkedin", media(*items))
    assert result.compatible
    assert result.post_type == expected


def test_linkedin_video_and_multiple_images_not_enabled():
    assert not determine_variant("linkedin", media("video/mp4")).compatible
    assert not determine_variant("linkedin", media("image/jpeg", "image/png")).compatible


def test_linkedin_document_is_staged_but_never_invents_drive_post_type():
    assert determine_variant("linkedin", media("application/pdf")).compatible
    assert not publisher_ready(
        "linkedin", "document", {"ENABLE_DRIVE_LINKEDIN_DOCUMENT": True}
    )


def test_blank_text_post_and_linkedin_image_without_text_are_rejected():
    assert not determine_variant("facebook", [], caption="  ").compatible
    assert not determine_variant("linkedin", media("image/jpeg"), caption="").compatible
    assert determine_variant("linkedin", media("image/jpeg"), caption="Launch").compatible
