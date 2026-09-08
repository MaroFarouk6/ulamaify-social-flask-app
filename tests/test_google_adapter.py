from unittest.mock import MagicMock

import pytest

from app.services.campaign_service import CampaignError, SheetsAdapter
from app.services.google_service import GoogleConfigurationError, google_client


def test_sheets_reads_whole_configured_range(app, monkeypatch):
    client = MagicMock()
    client.spreadsheets().values().get().execute.return_value = {"values": [["headers"]]}
    monkeypatch.setattr("app.services.campaign_service.google_client", lambda *args: client)
    app.config["CAMPAIGN_REGISTRY_SHEET_ID"] = "test-sheet-id"
    with app.app_context():
        adapter = SheetsAdapter()
        assert adapter.read_rows() == [["headers"]]
        client.spreadsheets().values().get.assert_called_with(
            spreadsheetId="test-sheet-id", range="'sheet1'!A:G", valueRenderOption="FORMATTED_VALUE"
        )
        adapter.update_status(5, 4, "INACTIVE")
        client.spreadsheets().values().update.assert_called_with(
            spreadsheetId="test-sheet-id",
            range="'sheet1'!E5",
            valueInputOption="RAW",
            body={"values": [["INACTIVE"]]},
        )


def test_google_credentials_not_exposed_on_failure(app):
    with app.app_context(), pytest.raises(GoogleConfigurationError, match="not configured"):
        google_client("sheets", "v4")


def test_remote_google_error_is_sanitized(app, monkeypatch):
    client = MagicMock()
    client.spreadsheets().values().get().execute.side_effect = RuntimeError("access_token=PRIVATE")
    monkeypatch.setattr("app.services.campaign_service.google_client", lambda *args: client)
    app.config["CAMPAIGN_REGISTRY_SHEET_ID"] = "test-sheet-id"
    with app.app_context(), pytest.raises(CampaignError) as caught:
        SheetsAdapter().read_rows()
    assert "PRIVATE" not in str(caught.value)
