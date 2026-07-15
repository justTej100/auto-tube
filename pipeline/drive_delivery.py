from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def upload_to_drive(client_id: str, client_secret: str, refresh_token: str,
                     folder_id: str, file_path: Path, filename: str) -> str:
    """Uploads file_path into the given Drive folder, authenticated as your
    real Google account (not a service account -- service accounts have no
    storage quota on personal Drive accounts, so uploads must happen under
    your own account's quota instead). Returns the file's webViewLink."""
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())

    drive = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(str(file_path), resumable=True)
    file = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return file["webViewLink"]
