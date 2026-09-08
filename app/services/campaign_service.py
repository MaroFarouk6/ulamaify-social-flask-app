import hashlib
import re
from dataclasses import dataclass
from datetime import date

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.services.audit_service import record
from app.services.google_service import GoogleConfigurationError, google_client

HEADERS = (
    "campaign_id",
    "campaign_name",
    "folder_name",
    "type",
    "status",
    "start_date",
    "end_date",
)


class CampaignError(ValueError):
    pass


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    campaign_name: str
    folder_name: str
    type: str
    status: str
    start_date: date
    end_date: date
    row_number: int

    @property
    def fingerprint(self):
        parts = (
            self.campaign_id,
            self.campaign_name,
            self.folder_name,
            self.type,
            self.status,
            self.start_date.isoformat(),
            self.end_date.isoformat(),
        )
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class SheetsAdapter:
    def __init__(self):
        self.sheet_id = current_app.config["CAMPAIGN_REGISTRY_SHEET_ID"]
        self.range = current_app.config["CAMPAIGN_REGISTRY_RANGE"]
        # Full-column range ensures new rows are always discovered.
        if not self.sheet_id:
            raise CampaignError("Campaign access is not configured. Contact an administrator.")
        if not re.fullmatch(r"(?:'[^']+'|[A-Za-z0-9_ ]+)!A:G", self.range):
            raise CampaignError("Campaign range must cover columns A:G, such as 'sheet1'!A:G.")
        self.tab = self.range.rsplit("!", 1)[0]
        self.client = google_client("sheets", "v4")

    def read_rows(self):
        try:
            return (
                self.client.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.sheet_id,
                    range=self.range,
                    valueRenderOption="FORMATTED_VALUE",
                )
                .execute(num_retries=2)
                .get("values", [])
            )
        except Exception:
            raise CampaignError(
                "The Campaign Registry could not be reached. Try again later."
            ) from None

    def update_status(self, row_number, column, status):
        cell = f"{self.tab}!{chr(ord('A') + column)}{row_number}"
        try:
            self.client.spreadsheets().values().update(
                spreadsheetId=self.sheet_id,
                range=cell,
                valueInputOption="RAW",
                body={"values": [[status]]},
            ).execute(num_retries=0)
        except Exception:
            # A lost response does not prove that the external write failed.
            raise CampaignError(
                "Campaign update could not be confirmed. Refresh the Sheet and campaign list before retrying."
            ) from None

    def append_row(self, values):
        try:
            self.client.spreadsheets().values().append(
                spreadsheetId=self.sheet_id,
                range=f"{self.tab}!A:G",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [values]},
            ).execute(num_retries=0)
        except Exception:
            # The service reconciles the registry because a lost response may follow a valid append.
            raise CampaignError(
                "Campaign creation could not be confirmed. Refresh the Sheet before retrying."
            ) from None


def adapter():
    factory = current_app.config.get("CAMPAIGN_ADAPTER_FACTORY", SheetsAdapter)
    try:
        return factory()
    except GoogleConfigurationError as exc:
        raise CampaignError(str(exc)) from None


def parse_rows(rows):
    if not rows:
        raise CampaignError("The Campaign Registry is empty or missing its header row.")
    headers = [str(value).strip() for value in rows[0]]
    if len(headers) != len(HEADERS) or set(headers) != set(HEADERS):
        raise CampaignError(
            "The Campaign Registry must contain the seven required, unique headers in columns A:G."
        )
    campaigns = []
    ids, folders = set(), set()
    for row_number, raw in enumerate(rows[1:], start=2):
        if not any(str(value).strip() for value in raw):
            continue
        padded = list(raw) + [""] * (len(headers) - len(raw))
        data = {key: str(value).strip() for key, value in zip(headers, padded)}
        if any(not data[field] for field in HEADERS):
            raise CampaignError(f"Registry row {row_number} has missing values.")
        if data["campaign_id"] in ids or data["folder_name"] in folders:
            raise CampaignError(f"Registry row {row_number} repeats a campaign ID or folder name.")
        if data["status"] not in ("ACTIVE", "INACTIVE"):
            raise CampaignError(f"Registry row {row_number} status must be ACTIVE or INACTIVE.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", data["campaign_id"]):
            raise CampaignError(f"Registry row {row_number} has an invalid campaign ID.")
        folder = data["folder_name"]
        if folder in (".", "..") or any(c in folder for c in ("/", "\\", "\0")):
            raise CampaignError(f"Registry row {row_number} has an invalid folder name.")
        try:
            for key in ("start_date", "end_date"):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data[key]):
                    raise ValueError()
                data[key] = date.fromisoformat(data[key])
        except ValueError:
            raise CampaignError(f"Registry row {row_number} dates must use YYYY-MM-DD.") from None
        if data["start_date"] > data["end_date"]:
            raise CampaignError(f"Registry row {row_number} starts after it ends.")
        ids.add(data["campaign_id"])
        folders.add(folder)
        campaigns.append(Campaign(**data, row_number=row_number))
    return headers, campaigns


def list_campaigns(active_only=False):
    _, campaigns = parse_rows(adapter().read_rows())
    return [c for c in campaigns if c.status == "ACTIVE"] if active_only else campaigns


def validate_date(campaign: Campaign, selected_date: date, require_active=True):
    if require_active and campaign.status != "ACTIVE":
        raise CampaignError(
            "This campaign is inactive. Activate it before approving content for Drive."
        )
    if not campaign.start_date <= selected_date <= campaign.end_date:
        raise CampaignError("Selected date is outside campaign range.")


def validate_selection(campaign_id, selected_date):
    campaign = next((c for c in list_campaigns() if c.campaign_id == campaign_id), None)
    if campaign is None:
        raise CampaignError("This campaign is no longer present in the registry.")
    validate_date(campaign, selected_date)
    return campaign


def set_status(campaign_id, status, expected_fingerprint, actor):
    if actor.role != "admin" or not actor.is_active:
        raise PermissionError("Administrator access is required.")
    if status not in ("ACTIVE", "INACTIVE"):
        raise CampaignError("Choose ACTIVE or INACTIVE.")
    record("CAMPAIGN_STATUS_CHANGE_REQUESTED", "campaign", campaign_id, {"to": status}, actor)
    db.session.commit()  # Durable intent before any remote write.
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text("SELECT pg_advisory_xact_lock(712164922)"))
        source = adapter()
        headers, campaigns = parse_rows(source.read_rows())
        campaign = next((c for c in campaigns if c.campaign_id == campaign_id), None)
        if campaign is None:
            raise CampaignError("Campaign no longer exists. Refresh the list.")
        if campaign.fingerprint != expected_fingerprint:
            raise CampaignError(
                "Campaign changed since this page opened. Refresh and review it before continuing."
            )
        if campaign.status != status:
            source.update_status(campaign.row_number, headers.index("status"), status)
        _, verified = parse_rows(source.read_rows())
        updated = next((c for c in verified if c.campaign_id == campaign_id), None)
        if not updated or updated.status != status:
            raise CampaignError(
                "Campaign update could not be confirmed. Check the Sheet before retrying."
            )
        record(
            "CAMPAIGN_ACTIVATED" if status == "ACTIVE" else "CAMPAIGN_DEACTIVATED",
            "campaign",
            campaign_id,
            {"from": campaign.status, "to": status},
            actor,
        )
        db.session.commit()
    except CampaignError:
        db.session.rollback()
        record(
            "CAMPAIGN_STATUS_CHANGE_UNCONFIRMED",
            "campaign",
            campaign_id,
            {"requested_status": status},
            actor,
        )
        db.session.commit()
        raise


def add_campaign(
    *, campaign_id, campaign_name, folder_name, campaign_type, status, start_date, end_date, actor
):
    if actor.role != "admin" or not actor.is_active:
        raise PermissionError("Administrator access is required.")
    values = [
        str(campaign_id).strip(),
        str(campaign_name).strip(),
        str(folder_name).strip(),
        str(campaign_type).strip(),
        str(status).strip(),
        start_date.isoformat(),
        end_date.isoformat(),
    ]
    _, proposed = parse_rows([list(HEADERS), values])
    candidate = proposed[0]
    record(
        "CAMPAIGN_CREATE_REQUESTED",
        "campaign",
        candidate.campaign_id,
        {"folder_name": candidate.folder_name, "status": candidate.status},
        actor,
    )
    db.session.commit()  # Preserve intent before the external write.
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text("SELECT pg_advisory_xact_lock(712164922)"))
        source = adapter()
        _, campaigns = parse_rows(source.read_rows())
        if any(item.campaign_id == candidate.campaign_id for item in campaigns):
            raise CampaignError("A campaign with this ID already exists.")
        if any(item.folder_name == candidate.folder_name for item in campaigns):
            raise CampaignError("A campaign with this folder name already exists.")
        write_error = None
        try:
            source.append_row(values)
        except CampaignError as exc:
            write_error = exc
        _, verified = parse_rows(source.read_rows())
        matches = [item for item in verified if item.campaign_id == candidate.campaign_id]
        if len(matches) != 1 or matches[0].fingerprint != candidate.fingerprint:
            if write_error:
                raise write_error
            raise CampaignError(
                "Campaign creation could not be verified. Check the Sheet before retrying."
            )
        record(
            "CAMPAIGN_CREATED",
            "campaign",
            candidate.campaign_id,
            {
                "campaign_name": candidate.campaign_name,
                "folder_name": candidate.folder_name,
                "type": candidate.type,
                "status": candidate.status,
                "start_date": candidate.start_date.isoformat(),
                "end_date": candidate.end_date.isoformat(),
                "write_response_lost": bool(write_error),
            },
            actor,
        )
        db.session.commit()
        return matches[0]
    except CampaignError:
        db.session.rollback()
        record(
            "CAMPAIGN_CREATE_UNCONFIRMED",
            "campaign",
            candidate.campaign_id,
            {"folder_name": candidate.folder_name},
            actor,
        )
        db.session.commit()
        raise
