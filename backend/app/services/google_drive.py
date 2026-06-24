from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import settings
from app.models.knowledge_base import DriveFolder
from app.services import google_oauth


class GoogleDriveError(Exception):
    pass


def is_configured() -> bool:
    if google_oauth.is_oauth_configured():
        return True
    path = settings.google_service_account_json
    return bool(path and path.is_file())


def _service():
    if google_oauth.is_oauth_configured():
        try:
            return google_oauth.build_drive_service()
        except google_oauth.GoogleOAuthError as exc:
            raise GoogleDriveError(str(exc)) from exc

    path = settings.google_service_account_json
    if path and path.is_file():
        credentials = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=google_oauth.DRIVE_SCOPES,
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    raise GoogleDriveError(
        "Google Drive is not configured. Set GOOGLE_CLIENT_ID, "
        "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN in backend/.env "
        "(run scripts/google_oauth_setup.py once)."
    )


def find_shared_drive_id(drive_name: str | None = None) -> str | None:
    name = drive_name or settings.google_drive_shared_drive_name
    service = _service()

    page_token: str | None = None
    while True:
        response = (
            service.drives()
            .list(
                pageSize=100,
                pageToken=page_token,
                q=f"name = '{name.replace(chr(39), chr(92) + chr(39))}'",
            )
            .execute()
        )
        drives = response.get("drives", [])
        if drives:
            return drives[0]["id"]

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return None


def _count_child_folders(service: Any, drive_id: str, folder_id: str) -> int:
    return 0


def list_folders_in_shared_drive(
    drive_id: str | None = None,
    *,
    parent_id: str | None = None,
) -> tuple[str, list[DriveFolder]]:
    service = _service()
    resolved_drive_id = drive_id or find_shared_drive_id()
    if not resolved_drive_id:
        raise GoogleDriveError(
            f"Shared drive '{settings.google_drive_shared_drive_name}' was not found"
        )

    parent = parent_id or resolved_drive_id
    query = (
        f"'{parent}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )

    folders: list[DriveFolder] = []
    page_token: str | None = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                corpora="drive",
                driveId=resolved_drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                orderBy="name_natural",
                pageSize=100,
                pageToken=page_token,
                fields=(
                    "nextPageToken, files(id, name, modifiedTime, webViewLink)"
                ),
            )
            .execute()
        )

        for item in response.get("files", []):
            folder_id = item["id"]
            folders.append(
                DriveFolder(
                    id=folder_id,
                    name=item.get("name", "Untitled folder"),
                    modifiedAt=item.get("modifiedTime"),
                    webViewLink=item.get("webViewLink"),
                    childFolderCount=_count_child_folders(
                        service, resolved_drive_id, folder_id
                    ),
                )
            )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return resolved_drive_id, folders


def folders_from_supermemory_documents(
    documents: list[dict[str, Any]],
) -> list[DriveFolder]:
    """Fallback folder list derived from Supermemory document metadata."""
    by_name: dict[str, DriveFolder] = {}

    for doc in documents:
        metadata = doc.get("metadata") or {}
        folder_name = (
            metadata.get("folderName")
            or metadata.get("folder")
            or metadata.get("parentFolder")
            or _folder_from_title(doc.get("title") or doc.get("name") or "")
        )
        if not folder_name:
            continue

        folder_id = (
            metadata.get("folderId")
            or metadata.get("parentFolderId")
            or f"sm-folder-{folder_name.lower().replace(' ', '-')}"
        )
        if folder_name not in by_name:
            by_name[folder_name] = DriveFolder(
                id=folder_id,
                name=folder_name,
                modifiedAt=doc.get("updatedAt") or doc.get("updated_at"),
                webViewLink=doc.get("url") or doc.get("webViewLink"),
                childFolderCount=0,
            )

    return sorted(by_name.values(), key=lambda folder: folder.name.lower())


def _folder_from_title(title: str) -> str | None:
    if "/" in title:
        parts = [part.strip() for part in title.split("/") if part.strip()]
        if len(parts) >= 2:
            return parts[0]
    return None
