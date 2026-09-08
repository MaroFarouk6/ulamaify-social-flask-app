import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload

from app.services.google_service import google_client

FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


class DriveUncertainError(DriveError):
    pass


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    size: int | None = None
    md5: str | None = None
    app_properties: dict | None = None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveAdapter:
    def __init__(self):
        self.client = google_client("drive", "v3")

    def _list(self, query):
        items, token = [], None
        try:
            while True:
                response = (
                    self.client.files()
                    .list(
                        q=query,
                        fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,appProperties)",
                        pageToken=token,
                        pageSize=1000,
                        spaces="drive",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute(num_retries=2)
                )
                items.extend(self._item(raw) for raw in response.get("files", []))
                token = response.get("nextPageToken")
                if not token:
                    break
        except Exception:
            raise DriveError(
                "Google Drive could not be reached. The content remains staged."
            ) from None
        return items

    @staticmethod
    def _item(raw):
        return DriveItem(
            id=raw["id"],
            name=raw["name"],
            mime_type=raw.get("mimeType", ""),
            size=int(raw["size"]) if raw.get("size") else None,
            md5=raw.get("md5Checksum"),
            app_properties=raw.get("appProperties") or {},
        )

    def find_children(self, parent_id, name=None, folders_only=False):
        query = f"'{_escape(parent_id)}' in parents and trashed = false"
        if name is not None:
            query += f" and name = '{_escape(name)}'"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"
        return self._list(query)

    def get_item(self, file_id):
        try:
            raw = (
                self.client.files()
                .get(
                    fileId=file_id,
                    fields="id,name,mimeType,size,md5Checksum,appProperties",
                    supportsAllDrives=True,
                )
                .execute(num_retries=2)
            )
            return self._item(raw)
        except Exception:
            raise DriveError("A previously created Drive item could not be verified.") from None

    def create_folder(self, parent_id, name, properties):
        try:
            raw = (
                self.client.files()
                .create(
                    body={
                        "name": name,
                        "mimeType": FOLDER_MIME,
                        "parents": [parent_id],
                        "appProperties": properties,
                    },
                    fields="id,name,mimeType,appProperties",
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            return self._item(raw)
        except Exception:
            raise DriveUncertainError("Drive folder creation could not be confirmed.") from None

    def upload_file(self, parent_id, name, path: Path, mime_type, properties):
        try:
            raw = (
                self.client.files()
                .create(
                    body={"name": name, "parents": [parent_id], "appProperties": properties},
                    media_body=MediaFileUpload(str(path), mimetype=mime_type, resumable=True),
                    fields="id,name,mimeType,size,md5Checksum,appProperties",
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            return self._item(raw)
        except Exception:
            raise DriveUncertainError(f"Upload of {name} could not be confirmed.") from None

    def upload_bytes(self, parent_id, name, data: bytes, mime_type, properties):
        try:
            raw = (
                self.client.files()
                .create(
                    body={"name": name, "parents": [parent_id], "appProperties": properties},
                    media_body=MediaIoBaseUpload(
                        io.BytesIO(data), mimetype=mime_type, resumable=False
                    ),
                    fields="id,name,mimeType,size,md5Checksum,appProperties",
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            return self._item(raw)
        except Exception:
            raise DriveUncertainError(f"Upload of {name} could not be confirmed.") from None

    def download_bytes(self, file_id):
        buffer = io.BytesIO()
        try:
            request = self.client.files().get_media(fileId=file_id, supportsAllDrives=True)
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=2)
            return buffer.getvalue()
        except Exception:
            raise DriveError("Drive file contents could not be verified.") from None


def adapter():
    factory = current_app.config.get("DRIVE_ADAPTER_FACTORY", GoogleDriveAdapter)
    return factory()


def find_exactly_one_or_create(client, parent_id, name, properties):
    matches = client.find_children(parent_id, name=name, folders_only=True)
    if len(matches) > 1:
        raise DriveError(f"Multiple Drive folders named {name} exist in the same location.")
    if matches:
        return matches[0], False
    try:
        return client.create_folder(parent_id, name, properties), True
    except DriveUncertainError:
        matches = client.find_children(parent_id, name=name, folders_only=True)
        if len(matches) == 1:
            return matches[0], True
        raise


def attempt_properties(attempt_id):
    return {"created_by": "ulamaify_social_manager", "commit_attempt_id": attempt_id}


def find_attempt_item(client, parent_id, name, attempt_id):
    matches = [
        item
        for item in client.find_children(parent_id, name=name)
        if (item.app_properties or {}).get("commit_attempt_id") == attempt_id
    ]
    if len(matches) > 1:
        raise DriveError(
            f"Multiple partial Drive items named {name} exist for this commit attempt."
        )
    return matches[0] if matches else None


def upload_idempotent_file(client, parent_id, name, path, mime_type, checksum, attempt_id):
    existing = find_attempt_item(client, parent_id, name, attempt_id)
    expected_md5 = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
    if existing:
        if existing.size != path.stat().st_size or (existing.md5 and existing.md5 != expected_md5):
            raise DriveError(f"Partial Drive file {name} does not match staged content.")
        return existing, False
    try:
        item = client.upload_file(
            parent_id, name, path, mime_type, {**attempt_properties(attempt_id), "sha256": checksum}
        )
    except DriveUncertainError:
        item = find_attempt_item(client, parent_id, name, attempt_id)
        if item is None:
            raise
    if item.size != path.stat().st_size or (item.md5 and item.md5 != expected_md5):
        raise DriveError(f"Drive verification failed for {name}.")
    return item, True


def upload_idempotent_bytes(client, parent_id, name, data, mime_type, attempt_id):
    existing = find_attempt_item(client, parent_id, name, attempt_id)
    expected_md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    if existing:
        if existing.size != len(data) or (existing.md5 and existing.md5 != expected_md5):
            raise DriveError(f"Partial Drive file {name} does not match approved content.")
        return existing, False
    try:
        item = client.upload_bytes(
            parent_id,
            name,
            data,
            mime_type,
            {**attempt_properties(attempt_id), "sha256": hashlib.sha256(data).hexdigest()},
        )
    except DriveUncertainError:
        item = find_attempt_item(client, parent_id, name, attempt_id)
        if item is None:
            raise
    if item.size != len(data) or (item.md5 and item.md5 != expected_md5):
        raise DriveError(f"Drive verification failed for {name}.")
    return item, True


def parse_post_numbers(items):
    return [
        int(match.group(1)) for item in items if (match := re.fullmatch(r"post-(\d+)", item.name))
    ]


def post_json_bytes(post_type):
    # Stable compact representation; schema contains exactly these three keys.
    return json.dumps(
        {"status": "approved", "post_type": post_type, "published": False},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
