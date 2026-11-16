"""Google Drive : navigation dans les Drives partagés et dépôt des documents."""

import json
from pathlib import Path

import httpx

from app.models import Meeting
from app.schemas import ExportSettings, ReportData
from app.services import report_render

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME = "application/vnd.google-apps.document"
ALL_DRIVES = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}


async def list_drives(client: httpx.AsyncClient) -> list[dict]:
    drives, token = [], None
    while True:
        params = {"pageSize": "100", "fields": "nextPageToken,drives(id,name)"}
        if token:
            params["pageToken"] = token
        resp = await client.get(f"{API}/drives", params=params)
        resp.raise_for_status()
        body = resp.json()
        drives.extend(body.get("drives", []))
        token = body.get("nextPageToken")
        if not token:
            return drives


async def list_folders(client: httpx.AsyncClient, parent_id: str, drive_id: str = "") -> list[dict]:
    """Sous-dossiers de `parent_id` (identifiant du Drive partagé pour sa racine, « root » pour Mon Drive)."""
    params = {
        "q": f"'{parent_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
        "fields": "files(id,name)",
        "orderBy": "name",
        "pageSize": "200",
        **ALL_DRIVES,
    }
    if drive_id:
        params.update({"corpora": "drive", "driveId": drive_id})
    resp = await client.get(f"{API}/files", params=params)
    resp.raise_for_status()
    return resp.json().get("files", [])


async def create_folder(client: httpx.AsyncClient, name: str, parent_id: str) -> str:
    resp = await client.post(
        f"{API}/files",
        params={"supportsAllDrives": "true", "fields": "id"},
        json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def upload_bytes(
    client: httpx.AsyncClient, name: str, content: bytes, mime: str, parent_id: str, convert_to: str = ""
) -> dict:
    metadata = {"name": name, "parents": [parent_id]}
    if convert_to:
        metadata["mimeType"] = convert_to
    boundary = "pdn-boundary-7d1f3c"
    body = (
        (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--".encode()
    )
    resp = await client.post(
        UPLOAD,
        params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,name,webViewLink"},
        content=body,
        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
    )
    resp.raise_for_status()
    return resp.json()


async def upload_file(client: httpx.AsyncClient, name: str, path: Path, mime: str, parent_id: str) -> dict:
    """Envoi « resumable » pour les gros fichiers (vidéo), lu en flux."""
    start = await client.post(
        UPLOAD,
        params={"uploadType": "resumable", "supportsAllDrives": "true", "fields": "id,name,webViewLink"},
        json={"name": name, "parents": [parent_id]},
        headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(path.stat().st_size)},
    )
    start.raise_for_status()

    async def stream():
        with path.open("rb") as handle:
            while chunk := handle.read(4 * 1024 * 1024):
                yield chunk

    resp = await client.put(
        start.headers["Location"],
        content=stream(),
        headers={"Content-Type": mime, "Content-Length": str(path.stat().st_size)},
        timeout=None,
    )
    resp.raise_for_status()
    return resp.json()


async def export_meeting(
    client: httpx.AsyncClient, settings: ExportSettings, meeting: Meeting, report: ReportData, transcript: str
) -> list[dict]:
    """Dépose les formats choisis dans le dossier configuré ; renvoie les liens créés."""
    if not settings.folder_id:
        raise ValueError("Aucun dossier Drive de destination configuré")
    name = report_render.export_name(settings.name_pattern, meeting, report)
    parent = settings.folder_id
    if settings.subfolder_per_meeting:
        parent = await create_folder(client, name, parent)

    links: list[dict] = []

    def add(kind: str, uploaded: dict) -> None:
        links.append({"kind": kind, "name": uploaded.get("name", ""), "url": uploaded.get("webViewLink", "")})

    docx: bytes | None = None
    for fmt in settings.formats:
        if fmt in ("docx", "gdoc"):
            docx = docx or await report_render.render("docx", meeting, report)
            if fmt == "gdoc":
                add("gdoc", await upload_bytes(client, name, docx, report_render.MIME["docx"], parent, GDOC_MIME))
            else:
                add("docx", await upload_bytes(client, f"{name}.docx", docx, report_render.MIME["docx"], parent))
        elif fmt in ("pdf", "md"):
            content = await report_render.render(fmt, meeting, report)
            add(fmt, await upload_bytes(client, f"{name}.{fmt}", content, report_render.MIME[fmt], parent))
    if settings.include_transcript and transcript:
        add(
            "transcript",
            await upload_bytes(client, f"{name} - transcript.txt", transcript.encode(), "text/plain", parent),
        )
    if settings.include_video and meeting.recording_path and Path(meeting.recording_path).exists():
        add("video", await upload_file(client, f"{name}.mp4", Path(meeting.recording_path), "video/mp4", parent))
    return links
