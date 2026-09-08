import json
from pathlib import Path

import google_auth_httplib2
import httplib2
from flask import current_app
from google.oauth2 import credentials, service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]


class GoogleConfigurationError(Exception):
    pass


def google_client(api: str, version: str):
    """Request-scoped client: httplib2 transports must not be shared between threads."""
    config = current_app.config
    try:
        if config["GOOGLE_AUTH_MODE"] == "oauth":
            raw = json.loads(Path(config["GOOGLE_AUTHORIZED_USER_FILE"]).read_text())
            if not isinstance(raw, dict):
                raise ValueError("Invalid credential structure")
            if not all(raw.get(key) for key in ("client_id", "client_secret", "refresh_token")):
                raise ValueError("Incomplete credential file")
            # The credential file cannot choose an arbitrary token endpoint.
            creds = credentials.Credentials(
                token=None,
                refresh_token=raw["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=raw["client_id"],
                client_secret=raw["client_secret"],
                scopes=SCOPES,
            )
        elif config["GOOGLE_AUTH_MODE"] == "service_account":
            creds = service_account.Credentials.from_service_account_file(
                config["GOOGLE_SERVICE_ACCOUNT_FILE"], scopes=SCOPES
            )
        else:
            raise ValueError("Unknown auth mode")
    except (OSError, ValueError, KeyError):
        raise GoogleConfigurationError(
            "Google access is not configured. Contact an administrator."
        ) from None
    transport = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=config["GOOGLE_HTTP_TIMEOUT"])
    )
    return build(api, version, http=transport, cache_discovery=False)
