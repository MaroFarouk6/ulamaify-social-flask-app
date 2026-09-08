"""One authoritative capability contract for forms, validation, review and Drive commits."""

from dataclasses import dataclass
from typing import FrozenSet

DRIVE_POST_TYPES = frozenset({"text", "image", "video", "carousel", "story"})
# Document is an internal staged-content classification. It is deliberately not a Drive post_type
# until the existing n8n workflow defines that contract.
DETECTED_CONTENT_TYPES = DRIVE_POST_TYPES | {"document"}


@dataclass(frozen=True)
class PlatformCapability:
    key: str
    label: str
    accepted_types: FrozenSet[str]
    story_media: FrozenSet[str] = frozenset()
    carousel_max: int | None = None
    publisher_ready_types: FrozenSet[str] = frozenset()


PLATFORM_CONFIG = {
    "instagram": PlatformCapability(
        key="instagram",
        label="Instagram",
        accepted_types=frozenset({"image", "video", "carousel", "story"}),
        story_media=frozenset({"image", "video"}),
        carousel_max=10,
        publisher_ready_types=frozenset({"image", "video", "carousel", "story"}),
    ),
    "facebook": PlatformCapability(
        key="facebook",
        label="Facebook",
        accepted_types=frozenset({"text", "image", "video", "carousel"}),
        # Multi-photo creation is valid. Drive release is feature-gated until its n8n branch is ready.
        publisher_ready_types=frozenset({"text", "image", "video"}),
    ),
    "linkedin": PlatformCapability(
        key="linkedin",
        label="LinkedIn",
        accepted_types=frozenset({"text", "image", "document"}),
        # Native n8n supports text/image. Document release requires a verified HTTP Request branch.
        publisher_ready_types=frozenset({"text", "image"}),
    ),
    "youtube": PlatformCapability(
        key="youtube",
        label="YouTube",
        accepted_types=frozenset({"video"}),
        publisher_ready_types=frozenset({"video"}),
    ),
}


def get_platform(key: str) -> PlatformCapability:
    try:
        return PLATFORM_CONFIG[key]
    except KeyError:
        raise ValueError("Select a configured platform.") from None


def publisher_ready(platform: str, post_type: str, config: dict) -> bool:
    capability = get_platform(platform)
    if post_type not in DRIVE_POST_TYPES:
        return False
    if post_type in capability.publisher_ready_types:
        return True
    # Enabling is explicit and type-specific so future publishers cannot be enabled accidentally.
    environment_key = f"ENABLE_DRIVE_{platform.upper()}_{post_type.upper()}"
    return bool(config.get(environment_key, False))
