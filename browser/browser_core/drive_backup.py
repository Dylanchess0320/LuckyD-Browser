"""One-command backup of Dylan's stuff to his 2TB Google Drive.

Google AI Pro includes 2TB of Drive storage. This module uploads files or
whole folders there with an OAuth sign-in (his own Google account — nothing
is shared with anyone).

First-time setup (one time, ~2 minutes):
  1. Go to https://console.cloud.google.com/apis/credentials and create an
     "OAuth client ID" of type "Desktop app". Download the JSON.
  2. Save it as ~/.luckyd/drive_client_secrets.json
  3. Run: python -m browser.browser_core.drive_backup auth
     (a browser window opens — sign in with dylanchess03@gmail.com)

After that:
  python -m browser.browser_core.drive_backup backup ~/workspace/your_files

Everything is lazy-imported so machines without the Google client libraries
still import this module fine.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DEFAULT_SECRETS = Path.home() / ".luckyd" / "drive_client_secrets.json"
DEFAULT_TOKEN = Path.home() / ".luckyd" / "drive_token.json"


class DriveBackupError(RuntimeError):
    """Raised when the backup can't proceed, with a human-readable reason."""


def _need(pkg: str, pip_name: str):
    try:
        return __import__(pkg)
    except ImportError as e:
        raise DriveBackupError(
            f"Python package {pip_name!r} is not installed. Run: pip install {pip_name} ({e})"
        ) from e


def get_drive_service(
    client_secrets: str | os.PathLike | None = None,
    token_path: str | os.PathLike | None = None,
):
    """Return an authorized Drive v3 service, running the OAuth flow if needed."""
    secrets = Path(client_secrets or DEFAULT_SECRETS)
    token = Path(token_path or DEFAULT_TOKEN)
    if not secrets.exists():
        raise DriveBackupError(
            f"No OAuth client secrets at {secrets}. Create a 'Desktop app' OAuth client ID at "
            "https://console.cloud.google.com/apis/credentials and save the JSON there."
        )
    _need("googleapiclient", "google-api-python-client")
    oauth_lib = _need("google_auth_oauthlib", "google-auth-oauthlib")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = oauth_lib.flow.InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds)


def ensure_folder(service, name: str) -> str:
    """Return the Drive folder id for ``name``, creating it if needed."""
    result = (
        service.files()
        .list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
    )
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    folder = (
        service.files()
        .create(body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id")
        .execute()
    )
    return folder["id"]


def upload_file(service, folder_id: str, path: str | os.PathLike) -> str:
    """Upload one file into a Drive folder. Returns the Drive file id."""
    _need("googleapiclient", "google-api-python-client")
    from googleapiclient.http import MediaFileUpload

    p = Path(path)
    if not p.is_file():
        raise DriveBackupError(f"Not a file: {p}")
    mime, _ = mimetypes.guess_type(str(p))
    media = MediaFileUpload(str(p), mimetype=mime or "application/octet-stream", resumable=True)
    created = (
        service.files()
        .create(body={"name": p.name, "parents": [folder_id]}, media_body=media, fields="id")
        .execute()
    )
    return created["id"]


def backup(
    paths: list[str | os.PathLike],
    folder_name: str = "LuckyD-Backup",
    service=None,
    client_secrets: str | os.PathLike | None = None,
) -> list[str]:
    """Upload files/directories to Drive. Returns the uploaded file names."""
    svc = service or get_drive_service(client_secrets=client_secrets)
    folder_id = ensure_folder(svc, folder_name)
    uploaded: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_file():
            upload_file(svc, folder_id, p)
            uploaded.append(p.name)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    upload_file(svc, folder_id, child)
                    uploaded.append(str(child.relative_to(p)))
        else:
            raise DriveBackupError(f"Nothing to back up at {p}")
    return uploaded


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Back up files to Dylan's 2TB Google Drive.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="Sign in with Google and cache the token.")
    bp = sub.add_parser("backup", help="Upload files/folders to Drive.")
    bp.add_argument("paths", nargs="+", help="Files or folders to back up.")
    bp.add_argument("--folder", default="LuckyD-Backup", help="Drive folder name.")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "auth":
            get_drive_service()
            print(f"Signed in. Token cached at {DEFAULT_TOKEN}")
        else:
            done = backup(args.paths, folder_name=args.folder)
            print(f"Uploaded {len(done)} file(s) to Drive folder {args.folder!r}.")
    except DriveBackupError as e:
        print(f"Backup failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
