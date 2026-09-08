import copy
import re
import secrets

import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import User
from app.services.auth_service import bootstrap_admin, create_user

REGISTRY = [
    ["campaign_id", "campaign_name", "folder_name", "type", "status", "start_date", "end_date"],
    ["test-001", "Test Campaign", "TEST-CAMPAIGN", "Test", "ACTIVE", "2026-09-01", "2026-09-30"],
    [
        "old-002",
        "Previous Campaign",
        "PREVIOUS-CAMPAIGN",
        "Regular",
        "INACTIVE",
        "2026-08-01",
        "2026-08-31",
    ],
]


class FakeSheet:
    def __init__(self):
        self.rows = copy.deepcopy(REGISTRY)
        self.writes = []
        self.unavailable = False
        self.lose_write_response = False
        self.append_writes = []
        self.lose_append_response = False

    def read_rows(self):
        from app.services.campaign_service import CampaignError

        if self.unavailable:
            raise CampaignError("The Campaign Registry could not be reached.")
        return copy.deepcopy(self.rows)

    def update_status(self, row_number, column, status):
        from app.services.campaign_service import CampaignError

        self.writes.append((row_number, column, status))
        self.rows[row_number - 1][column] = status
        if self.lose_write_response:
            raise CampaignError("Campaign update could not be confirmed.")

    def append_row(self, values):
        from app.services.campaign_service import CampaignError

        self.append_writes.append(list(values))
        self.rows.append(list(values))
        if self.lose_append_response:
            raise CampaignError("Campaign creation could not be confirmed.")


@pytest.fixture
def sheet():
    return FakeSheet()


@pytest.fixture
def app(tmp_path, sheet):
    password = secrets.token_urlsafe(24)
    app = create_app(
        {
            "TESTING": True,
            "PRODUCTION": False,
            "SESSION_COOKIE_SECURE": False,
            "SECRET_KEY": secrets.token_urlsafe(48),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.sqlite'}",
            "CAMPAIGN_ADAPTER_FACTORY": lambda: sheet,
            "STAGING_PATH": str(tmp_path / "staging"),
            "TRUSTED_HOSTS": ["localhost", "social.ulamaify.org"],
            "TEST_PASSWORD": password,
        }
    )
    with app.app_context():
        db.create_all()
        admin = bootstrap_admin("Admin", "admin@example.com", password)
        create_user(
            name="Creator",
            email="creator@example.com",
            role="content_creator",
            password=password,
            must_change=False,
            actor=admin,
        )
        create_user(
            name="Temporary",
            email="temporary@example.com",
            role="content_creator",
            password=password,
            must_change=True,
            actor=admin,
        )
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path):
    response = client.get(path)
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match, response.data.decode()
    return match.group(1).decode()


def login(client, app, email="admin@example.com"):
    return client.post(
        "/login",
        data={
            "email": email,
            "password": app.config["TEST_PASSWORD"],
            "csrf_token": csrf(client, "/login"),
        },
    )


def user_id(app, email):
    with app.app_context():
        return db.session.scalar(select(User.id).where(User.email == email))
