import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.services.media_service import asset_path, copy_to


@dataclass(frozen=True)
class NormalizedFile:
    path: Path
    filename: str
    mime_type: str
    size: int
    checksum: str


class NormalizedPackage:
    def __init__(self, variant):
        self.variant = variant
        self.temporary = tempfile.TemporaryDirectory(prefix="social-normalized-")
        self.root = Path(self.temporary.name)
        self.files = self._normalize()

    def close(self):
        self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _add(self, source: Path, filename: str, mime_type: str, convert_jpeg=False):
        destination = self.root / filename
        if convert_jpeg:
            with Image.open(source) as image:
                image = image.convert("RGBA")
                canvas = Image.new("RGB", image.size, "white")
                canvas.paste(image, mask=image.getchannel("A"))
                canvas.save(destination, "JPEG", quality=95, optimize=True)
        else:
            copy_to(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return NormalizedFile(destination, filename, mime_type, destination.stat().st_size, digest)

    def _normalize(self):
        media = sorted(self.variant.effective_media, key=lambda item: item.sort_order)
        post_type = self.variant.selected_post_type
        if post_type == "text":
            return []
        if post_type == "carousel":
            return [
                self._add(asset_path(asset), f"slide{index}.jpg", "image/jpeg", convert_jpeg=True)
                for index, asset in enumerate(media, start=1)
            ]
        asset = media[0]
        if post_type == "image":
            extension = ".jpg" if asset.mime_type == "image/jpeg" else ".png"
            return [self._add(asset_path(asset), f"image{extension}", asset.mime_type)]
        if post_type == "video":
            return [self._add(asset_path(asset), "video.mp4", "video/mp4")]
        if post_type == "document":
            return [self._add(asset_path(asset), "document.pdf", "application/pdf")]
        if post_type == "story":
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "video/mp4": ".mp4"}[
                asset.mime_type
            ]
            return [self._add(asset_path(asset), f"story{extension}", asset.mime_type)]
        raise ValueError("Unsupported post type.")
