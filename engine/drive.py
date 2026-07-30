"""Shared Google Drive helpers for both pipelines. NewNova and
RankedbyHetti deliberately use SEPARATE Google accounts (their own OAuth
apps, their own refresh tokens), so every function here takes an
already-built `drive` client rather than assuming one global account --
build_client() once per pipeline run, then reuse it for every call
instead of re-authenticating per file."""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


def build_client(client_id: str, client_secret: str, refresh_token: str):
    """Scope is intentionally NOT specified here -- it was already baked
    into the refresh token when it was issued (see get_drive_token.py,
    which requests drive.file for Nova and full drive access for Hetti,
    since Hetti has to read folders it didn't create). Google's token
    refresh endpoint doesn't re-negotiate scope, so there's nothing to
    configure on this end."""
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def upload_file(drive, folder_id: str, file_path: Path, filename: str) -> str:
    """Uploads file_path into folder_id, returns the file's webViewLink."""
    media = MediaFileUpload(str(file_path), resumable=True)
    file = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["webViewLink"]


def list_subfolders(drive, parent_folder_id: str) -> list[dict]:
    """Returns [{'id':..., 'name':...}, ...] for every subfolder directly
    under parent_folder_id (non-recursive)."""
    result = drive.files().list(
        q=(f"'{parent_folder_id}' in parents "
           "and mimeType='application/vnd.google-apps.folder' "
           "and trashed=false"),
        fields="files(id, name)",
    ).execute()
    return result.get("files", [])


def list_files(drive, folder_id: str) -> list[dict]:
    """Returns [{'id':..., 'name':...}, ...] for every file directly under
    folder_id (non-recursive, excludes subfolders implicitly since we
    only ever call this on leaf intake folders)."""
    result = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name)",
    ).execute()
    return result.get("files", [])


def download_file(drive, file_id: str, out_path: Path) -> None:
    request = drive.files().get_media(fileId=file_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def delete_folder(drive, folder_id: str) -> None:
    """Permanently deletes a folder and everything in it -- not a trash
    move. RankedbyHetti calls this once a folder's video has been fully
    assembled and uploaded, so processed intake folders don't pile up."""
    drive.files().delete(fileId=folder_id).execute()