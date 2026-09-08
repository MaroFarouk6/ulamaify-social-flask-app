"""Generate new deployment secrets without overwriting existing files."""

import os
import secrets
from pathlib import Path


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as file:
        file.write(value + "\n")


def main():
    root = Path("secrets")
    if root.exists():
        raise SystemExit("secrets/ already exists. Existing secrets were not changed.")
    root.mkdir(mode=0o750)
    app_password, owner_password = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    write_new(root / "secret_key", secrets.token_urlsafe(48))
    write_new(root / "db_app_password", app_password)
    write_new(root / "db_owner_password", owner_password)
    write_new(
        root / "database_url",
        f"postgresql+psycopg://social_app:{app_password}@postgres:5432/social_manager",
    )
    write_new(
        root / "migration_database_url",
        f"postgresql+psycopg://social_owner:{owner_password}@postgres:5432/social_manager",
    )
    # OAuth file is provided separately by the administrator.
    print("Deployment secrets created. No secret values were printed.")


if __name__ == "__main__":
    main()
