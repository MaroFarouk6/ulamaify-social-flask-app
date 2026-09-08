from datetime import date

import pytest
from conftest import csrf, login
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, User
from app.services.campaign_service import (
    CampaignError,
    list_campaigns,
    parse_rows,
    set_status,
    validate_date,
    validate_selection,
)


def test_active_only_for_creator(app, client):
    login(client, app, "creator@example.com")
    response = client.get("/campaigns")
    assert b"Test Campaign" in response.data
    assert b"Previous Campaign" not in response.data
    assert b"TEST-CAMPAIGN" not in response.data


def test_admin_sees_all_campaigns(app, client):
    login(client, app)
    response = client.get("/admin/campaigns")
    assert b"TEST-CAMPAIGN" in response.data
    assert b"PREVIOUS-CAMPAIGN" in response.data


def test_direct_sheet_changes_seen_without_database_sync(app, sheet):
    with app.app_context():
        assert len(list_campaigns(active_only=True)) == 1
        sheet.rows.append(
            [
                "new-003",
                "New Campaign",
                "NEW-CAMPAIGN",
                "Launch",
                "ACTIVE",
                "2026-09-01",
                "2026-10-01",
            ]
        )
        assert len(list_campaigns(active_only=True)) == 2
        sheet.rows[1][4] = "INACTIVE"
        assert [c.campaign_id for c in list_campaigns(active_only=True)] == ["new-003"]
        sheet.rows[1][4] = "ACTIVE"
        assert len(list_campaigns(active_only=True)) == 2


def test_toggle_updates_only_sheet_status_and_audit(app, client, sheet):
    login(client, app)
    with app.app_context():
        campaign = list_campaigns()[0]
    response = client.post(
        "/admin/campaigns/test-001/deactivate",
        data={
            "csrf_token": csrf(client, "/admin/campaigns"),
            "expected_fingerprint": campaign.fingerprint,
        },
    )
    assert response.status_code == 302
    assert sheet.writes == [(2, 4, "INACTIVE")]
    assert sheet.rows[1][2] == "TEST-CAMPAIGN"
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "CAMPAIGN_DEACTIVATED"))
        assert entry.actor_name == "Admin"


@pytest.mark.parametrize("target", [date(2026, 9, 1), date(2026, 9, 30)])
def test_inclusive_dates(app, target):
    with app.app_context():
        validate_selection("test-001", target)


@pytest.mark.parametrize("target", [date(2026, 8, 31), date(2026, 10, 1)])
def test_out_of_range_rejected(app, target):
    with app.app_context(), pytest.raises(CampaignError, match="outside campaign range"):
        validate_selection("test-001", target)


def test_inactive_rejected_at_validation(app):
    with app.app_context(), pytest.raises(CampaignError, match="inactive"):
        validate_date(list_campaigns()[1], date(2026, 8, 15))


def test_deleted_campaign_rejected(app, sheet):
    sheet.rows.pop(1)
    with app.app_context(), pytest.raises(CampaignError, match="no longer present"):
        validate_selection("test-001", date(2026, 9, 10))


def test_stale_form_cannot_overwrite_sheet_edits(app, sheet):
    with app.app_context():
        campaign = list_campaigns()[0]
        sheet.rows[1][1] = "Renamed Campaign"
        with pytest.raises(CampaignError, match="changed since"):
            set_status(
                campaign.campaign_id, "INACTIVE", campaign.fingerprint, db.session.get(User, 1)
            )
    assert sheet.writes == []


def test_row_reordered_before_action_is_resolved_by_id(app, sheet):
    with app.app_context():
        campaign = list_campaigns()[0]
        sheet.rows[1], sheet.rows[2] = sheet.rows[2], sheet.rows[1]
        set_status(campaign.campaign_id, "INACTIVE", campaign.fingerprint, db.session.get(User, 1))
    assert sheet.writes == [(3, 4, "INACTIVE")]


def test_reordered_columns_supported(app, sheet):
    for row in sheet.rows:
        row[0], row[4] = row[4], row[0]
    with app.app_context():
        campaign = list_campaigns()[0]
        set_status(campaign.campaign_id, "INACTIVE", campaign.fingerprint, db.session.get(User, 1))
    assert sheet.writes == [(2, 0, "INACTIVE")]


def test_lost_sheet_response_is_not_reported_as_definite_failure(app, sheet):
    sheet.lose_write_response = True
    with app.app_context():
        campaign = list_campaigns()[0]
        with pytest.raises(CampaignError, match="could not be confirmed"):
            set_status(
                campaign.campaign_id, "INACTIVE", campaign.fingerprint, db.session.get(User, 1)
            )
        assert db.session.scalar(
            select(AuditLog).where(AuditLog.action == "CAMPAIGN_STATUS_CHANGE_UNCONFIRMED")
        )
    assert sheet.rows[1][4] == "INACTIVE"


def test_creator_cannot_toggle_campaign(app, client, sheet):
    login(client, app, "creator@example.com")
    response = client.post(
        "/admin/campaigns/test-001/deactivate", data={"csrf_token": csrf(client, "/")}
    )
    assert response.status_code == 403
    assert sheet.writes == []


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing_header",
        "invalid_status",
        "invalid_date",
        "inverted_range",
        "duplicate_folder",
    ],
)
def test_malformed_registry_fails_closed(sheet, change):
    if change == "duplicate":
        sheet.rows.append(sheet.rows[1].copy())
    elif change == "missing_header":
        sheet.rows[0][0] = "id"
    elif change == "invalid_status":
        sheet.rows[1][4] = "active"
    elif change == "invalid_date":
        sheet.rows[1][5] = "09/01/2026"
    elif change == "inverted_range":
        sheet.rows[1][5] = "2027-09-01"
    elif change == "duplicate_folder":
        sheet.rows[2][2] = sheet.rows[1][2]
    with pytest.raises(CampaignError):
        parse_rows(sheet.rows)


def test_registry_outage_shows_useful_message(app, client, sheet):
    login(client, app)
    sheet.unavailable = True
    response = client.get("/admin/campaigns")
    assert response.status_code == 503
    assert b"could not be reached" in response.data
    assert b"Traceback" not in response.data


def test_headers_only_is_valid_empty_registry(app, sheet):
    sheet.rows = sheet.rows[:1]
    with app.app_context():
        assert list_campaigns() == []


def test_admin_adds_campaign_to_sheet_and_audit(app, client, sheet):
    login(client, app)
    response = client.post(
        "/admin/campaigns/new",
        data={
            "csrf_token": csrf(client, "/admin/campaigns/new"),
            "campaign_id": "launch-003",
            "campaign_name": "Ulamaify Launch",
            "folder_name": "ULAMAIFY-LAUNCH",
            "campaign_type": "Launch",
            "status": "INACTIVE",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
        },
    )
    assert response.status_code == 302
    assert sheet.append_writes == [
        [
            "launch-003",
            "Ulamaify Launch",
            "ULAMAIFY-LAUNCH",
            "Launch",
            "INACTIVE",
            "2026-10-01",
            "2026-10-31",
        ]
    ]
    with app.app_context():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "CAMPAIGN_CREATED"))


def test_campaign_add_reconciles_lost_sheet_response(app, client, sheet):
    sheet.lose_append_response = True
    login(client, app)
    response = client.post(
        "/admin/campaigns/new",
        data={
            "csrf_token": csrf(client, "/admin/campaigns/new"),
            "campaign_id": "safe-004",
            "campaign_name": "Safe Append",
            "folder_name": "SAFE-APPEND",
            "campaign_type": "Test",
            "status": "ACTIVE",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        },
        follow_redirects=True,
    )
    assert b"Campaign added to the registry Sheet" in response.data
    assert len([row for row in sheet.rows if row[0] == "safe-004"]) == 1


def test_campaign_add_rejects_duplicate_id_and_invalid_range(app, client, sheet):
    login(client, app)
    token = csrf(client, "/admin/campaigns/new")
    base = {
        "csrf_token": token,
        "campaign_id": "test-001",
        "campaign_name": "Duplicate",
        "folder_name": "UNIQUE-FOLDER",
        "campaign_type": "Test",
        "status": "ACTIVE",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }
    response = client.post("/admin/campaigns/new", data=base, follow_redirects=True)
    assert b"campaign with this ID already exists" in response.data
    assert sheet.append_writes == []
    base["csrf_token"] = csrf(client, "/admin/campaigns/new")
    base["campaign_id"] = "invalid-range"
    base["end_date"] = "2026-08-01"
    response = client.post("/admin/campaigns/new", data=base, follow_redirects=True)
    assert b"starts after it ends" in response.data
    assert sheet.append_writes == []


def test_creator_cannot_add_campaign(app, client, sheet):
    login(client, app, "creator@example.com")
    assert client.get("/admin/campaigns/new").status_code == 403
    assert sheet.append_writes == []
