import os
from datetime import timedelta
from pathlib import Path


def secret(name: str) -> str:
    path = os.getenv(f"{name}_FILE")
    return Path(path).read_text().strip() if path else os.getenv(name, "")


def settings() -> dict:
    production = os.getenv("APP_ENV", "production") == "production"
    return {
        "PRODUCTION": production,
        "SECRET_KEY": secret("SECRET_KEY"),
        "SQLALCHEMY_DATABASE_URI": secret("DATABASE_URL"),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "SESSION_COOKIE_NAME": "ulamaify_social_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": production,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=int(os.getenv("SESSION_HOURS", "12"))),
        "SESSION_REFRESH_EACH_REQUEST": False,
        "WTF_CSRF_TIME_LIMIT": 3600,
        "MAX_CONTENT_LENGTH": int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024,
        "STAGING_PATH": os.getenv("STAGING_PATH", "/app/data/staging"),
        "TRUSTED_HOSTS": os.getenv(
            "TRUSTED_HOSTS", "social.ulamaify.org,localhost,127.0.0.1"
        ).split(","),
        "PROXY_HOPS": int(os.getenv("PROXY_HOPS", "0")),
        "GOOGLE_AUTH_MODE": os.getenv("GOOGLE_AUTH_MODE", "oauth"),
        "GOOGLE_AUTHORIZED_USER_FILE": os.getenv("GOOGLE_AUTHORIZED_USER_FILE", ""),
        "GOOGLE_SERVICE_ACCOUNT_FILE": os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
        "SOCIAL_ROOT_FOLDER_ID": os.getenv("SOCIAL_ROOT_FOLDER_ID", ""),
        "CAMPAIGNS_ROOT_FOLDER_ID": os.getenv("CAMPAIGNS_ROOT_FOLDER_ID", ""),
        "CAMPAIGN_REGISTRY_SHEET_ID": os.getenv("CAMPAIGN_REGISTRY_SHEET_ID", ""),
        "CAMPAIGN_REGISTRY_RANGE": os.getenv("CAMPAIGN_REGISTRY_RANGE", "'sheet1'!A:G"),
        "GOOGLE_HTTP_TIMEOUT": 20,
        "ENABLE_DRIVE_FACEBOOK_CAROUSEL": os.getenv(
            "ENABLE_DRIVE_FACEBOOK_CAROUSEL", "false"
        ).lower()
        == "true",
        "MAX_MEDIA_FILES": int(os.getenv("MAX_MEDIA_FILES", "20")),
        "MEDIA_RETENTION_DAYS": int(os.getenv("MEDIA_RETENTION_DAYS", "30")),
        "LOGIN_IP_LIMIT": 20,
        "LOGIN_ACCOUNT_LIMIT": 10,
    }


def validate_config(config: dict) -> None:
    if len(config.get("SECRET_KEY", "")) < 32:
        raise RuntimeError("SECRET_KEY must be supplied with at least 32 random characters.")
    uri = config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri:
        raise RuntimeError("DATABASE_URL must be supplied.")
    if config["PRODUCTION"] and not uri.startswith("postgresql+psycopg://"):
        raise RuntimeError("Production requires PostgreSQL with the psycopg driver.")
    if config["PROXY_HOPS"] not in (0, 1):
        raise RuntimeError("Confirm the proxy topology before trusting more than one proxy.")
