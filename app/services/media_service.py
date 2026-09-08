import hashlib
import os
import shutil
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import MediaAsset

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}


class MediaError(ValueError):
    pass


def submission_root(submission_id: str) -> Path:
    root = Path(current_app.config["STAGING_PATH"]).resolve()
    target = (root / submission_id).resolve()
    if target.parent != root:
        raise MediaError("Invalid staging location.")
    return target


def asset_path(asset: MediaAsset) -> Path:
    root = submission_root(asset.submission_id)
    path = (root / "original" / asset.staged_name).resolve()
    if path.parent != (root / "original").resolve():
        raise MediaError("Invalid staged media location.")
    return path


def _determine_mime(path: Path) -> tuple[str, int | None, int | None]:
    header = path.read_bytes()[:32]
    if header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG\r\n\x1a\n"):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.width * image.height > 100_000_000:
                    raise MediaError("Image dimensions are too large.")
                mime = (
                    "image/jpeg"
                    if image.format == "JPEG"
                    else "image/png"
                    if image.format == "PNG"
                    else None
                )
                if not mime:
                    raise MediaError("Only JPEG and PNG images are allowed.")
                return mime, image.width, image.height
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise MediaError("The image file is invalid or unsafe.") from None
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4", None, None
    if header.startswith(b"%PDF-"):
        try:
            reader = PdfReader(str(path), strict=True)
            if len(reader.pages) == 0:
                raise MediaError("The PDF has no pages.")
        except (PdfReadError, OSError, ValueError):
            raise MediaError("The PDF file is invalid.") from None
        return "application/pdf", None, None
    raise MediaError("Only valid JPEG, PNG, MP4, and PDF files are allowed.")


def store_upload(submission, upload: FileStorage, platform_variant=None) -> MediaAsset:
    if not upload or not upload.filename:
        raise MediaError("Choose a file to upload.")
    all_count = len(submission.media_assets)
    if all_count >= current_app.config["MAX_MEDIA_FILES"]:
        raise MediaError("This submission has reached the configured media-file limit.")
    original = secure_filename(upload.filename)[:255] or "uploaded-file"
    root = submission_root(submission.id) / "original"
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = root / f".{uuid.uuid4()}.upload"
    size = 0
    digest = hashlib.sha256()
    maximum = current_app.config["MAX_CONTENT_LENGTH"]
    try:
        with temporary.open("xb") as target:
            while chunk := upload.stream.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise MediaError("The upload exceeds the configured size limit.")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise MediaError("The uploaded file is empty.")
        mime, width, height = _determine_mime(temporary)
        staged_name = f"{uuid.uuid4()}{MIME_EXTENSIONS[mime]}"
        final = root / staged_name
        os.replace(temporary, final)
        selected = [
            asset
            for asset in submission.media_assets
            if asset.submission_platform_id == (platform_variant.id if platform_variant else None)
        ]
        asset = MediaAsset(
            submission=submission,
            platform_variant=platform_variant,
            original_filename=original,
            staged_name=staged_name,
            mime_type=mime,
            file_size=size,
            checksum=digest.hexdigest(),
            sort_order=max((item.sort_order for item in selected), default=0) + 1,
            width=width,
            height=height,
        )
        db.session.add(asset)
        return asset
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def remove_asset(asset: MediaAsset) -> None:
    asset_path(asset).unlink(missing_ok=True)
    db.session.delete(asset)


def reorder_assets(submission, ordered_ids: list[str], platform_variant=None) -> None:
    current = [
        asset
        for asset in submission.media_assets
        if asset.submission_platform_id == (platform_variant.id if platform_variant else None)
    ]
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != {
        item.id for item in current
    }:
        raise MediaError("Media order is incomplete or invalid. Refresh and try again.")
    by_id = {item.id: item for item in current}
    for order, media_id in enumerate(ordered_ids, start=1):
        by_id[media_id].sort_order = order


def copy_to(source: Path, destination: Path) -> None:
    """Isolated helper for later normalization; never replaces staged originals."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
