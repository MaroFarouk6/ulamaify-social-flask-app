from dataclasses import dataclass

from app.platforms import get_platform


@dataclass(frozen=True)
class DetectionResult:
    post_type: str | None
    compatible: bool
    message: str


def detect_type(media) -> tuple[str | None, str | None]:
    types = [asset.mime_type for asset in media]
    if not types:
        return "text", None
    images = sum(item.startswith("image/") for item in types)
    videos = sum(item == "video/mp4" for item in types)
    documents = sum(item == "application/pdf" for item in types)
    if images == 1 and len(types) == 1:
        return "image", None
    if images >= 2 and images == len(types):
        return "carousel", None
    if videos == 1 and len(types) == 1:
        return "video", None
    if documents == 1 and len(types) == 1:
        return "document", None
    return None, "Mixed media requires review. Use one media family for each platform."


def determine_variant(
    platform: str, media, placement: str = "standard", caption: str | None = None
) -> DetectionResult:
    capability = get_platform(platform)
    detected, error = detect_type(media)
    if error:
        return DetectionResult(None, False, error)
    if placement == "story":
        if detected not in capability.story_media:
            return DetectionResult(
                "story", False, f"{capability.label} Story requires one supported image or video."
            )
        return DetectionResult("story", True, "Ready as Story.")
    if detected == "text" and caption is not None and not caption.strip():
        return DetectionResult("text", False, "Text-only content requires a caption.")
    if platform == "linkedin" and detected == "image" and caption is not None and not caption.strip():
        return DetectionResult("image", False, "LinkedIn image content requires accompanying text.")
    if detected not in capability.accepted_types:
        messages = {
            ("instagram", "text"): "Instagram does not support a text-only submission.",
            ("youtube", "text"): "YouTube requires video content in the current automation.",
            ("youtube", "image"): "YouTube requires video content in the current automation.",
            ("youtube", "carousel"): "YouTube requires video content in the current automation.",
            ("youtube", "document"): "YouTube requires video content in the current automation.",
            ("linkedin", "video"): "LinkedIn video is not enabled in the current automation.",
            (
                "linkedin",
                "carousel",
            ): "LinkedIn multi-image posts are not enabled in the current automation.",
            (
                "facebook",
                "document",
            ): "Facebook PDF posts are not enabled in the current automation.",
        }
        return DetectionResult(
            detected,
            False,
            messages.get(
                (platform, detected), f"{capability.label} does not support this media package."
            ),
        )
    if detected == "carousel" and capability.carousel_max and len(media) > capability.carousel_max:
        return DetectionResult(
            detected,
            False,
            f"{capability.label} carousel supports a maximum of {capability.carousel_max} items.",
        )
    return DetectionResult(detected, True, "Compatible.")


def refresh_submission(submission) -> None:
    shared = [asset for asset in submission.media_assets if asset.submission_platform_id is None]
    for variant in submission.platforms:
        effective = list(variant.media_assets) or shared
        result = determine_variant(
            variant.platform, effective, variant.placement, variant.effective_caption
        )
        variant.detected_post_type = result.post_type
        variant.selected_post_type = result.post_type
        variant.is_compatible = result.compatible
        variant.compatibility_message = result.message
