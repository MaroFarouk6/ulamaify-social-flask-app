"""Run on an administrator's local workstation; never inside a public web route."""

import argparse
import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]


def main():
    parser = argparse.ArgumentParser(description="Authorize the machine Google account.")
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Output already exists. Choose a new file and rotate the mounted secret separately."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_file), SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        bind_addr="127.0.0.1",
        port=args.port,
        access_type="offline",
        prompt="consent",
        open_browser=True,
        authorization_prompt_message="Complete Google authorization in the browser opened on this workstation.",
        success_message="Authorization completed. You can close this window.",
    )
    if not credentials.refresh_token:
        raise SystemExit("No refresh token returned. Reauthorize with offline access.")
    data = json.loads(credentials.to_json())
    data.pop("token", None)
    data.pop("expiry", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file)
        file.write("\n")
    print("Credential file created with restricted permissions. Copy it securely to the server.")


if __name__ == "__main__":
    main()
