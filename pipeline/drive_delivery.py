import base64
import json
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def upload_to_drive(service_account_json_b64: str, folder_id: str, file_path: Path, filename: str) -> str:
    """Uploads file_path into the given Drive folder using a service
    account. Returns the file's webViewLink for review.

    The target folder must already be shared with the service account's
    email (Editor access) — see README for setup. Files uploaded into a
    shared folder inherit that folder's permissions, so anyone with access
    to the folder can open the link without extra sharing steps."""
    info = json.loads(base64.b64decode(service_account_json_b64))
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    drive = build("drive", "v3", credentials=creds)

    media = MediaFileUpload(str(file_path), resumable=True)
    file = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return file["webViewLink"]
