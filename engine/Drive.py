"""Google Drive client. Each channel builds its own Drive instance (own
OAuth account / refresh token) and reuses it for the run."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


class Drive:
    """Authenticated Google Drive v3 client for one account."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        """Scope was baked into the refresh token at issue time
        (see get_drive_token.py)."""
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        creds.refresh(Request())
        self.service = build("drive", "v3", credentials=creds)

    def upload_file(self, folder_id: str, file_path: Path, filename: str) -> str:
        """Uploads file_path into folder_id, returns the file's webViewLink."""
        media = MediaFileUpload(str(file_path), resumable=True)
        file = self.service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
        ).execute()
        return file["webViewLink"]

    def list_subfolders(self, parent_folder_id: str) -> list[dict]:
        result = self.service.files().list(
            q=(
                f"'{parent_folder_id}' in parents "
                "and mimeType='application/vnd.google-apps.folder' "
                "and trashed=false"
            ),
            fields="files(id, name)",
        ).execute()
        return result.get("files", [])

    def list_files(self, folder_id: str) -> list[dict]:
        result = self.service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name)",
        ).execute()
        return result.get("files", [])

    def download_file(self, file_id: str, out_path: Path) -> None:
        request = self.service.files().get_media(fileId=file_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    def delete_folder(self, folder_id: str) -> None:
        """Permanently deletes a folder and everything in it."""
        self.service.files().delete(fileId=folder_id).execute()
