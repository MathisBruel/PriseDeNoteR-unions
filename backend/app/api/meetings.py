import asyncio
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import bus
from app.config import get_config
from app.db import get_db, utcnow
from app.models import Meeting, MeetingStatus, Report
from app.schemas import MeetingCreate, MeetingOut, MeetingPatch, ReportData, ReportOut, SegmentOut
from app.security import current_user, websocket_user_id
from app.services import bot_control, report_render
from app.services.calendar_sync import MEET_RE
from app.services.pipeline import best_segments
from app.services.settings import load_settings

router = APIRouter(prefix="/api/meetings", tags=["meetings"], dependencies=[Depends(current_user)])
ws_router = APIRouter()

UPCOMING = (MeetingStatus.SCHEDULED, *MeetingStatus.ACTIVE)


def _out(meeting: Meeting) -> MeetingOut:
    out = MeetingOut.model_validate(meeting)
    out.has_recording = bool(meeting.recording_path and Path(meeting.recording_path).exists())
    out.has_report = meeting.report is not None
    return out


async def _get(db: AsyncSession, meeting_id: int) -> Meeting:
    meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id).options(selectinload(Meeting.report)))
    if not meeting:
        raise HTTPException(404, "Réunion introuvable")
    return meeting


@router.get("")
async def list_meetings(scope: str = "upcoming", q: str = "", db: AsyncSession = Depends(get_db)):
    query = select(Meeting).options(selectinload(Meeting.report))
    if scope == "upcoming":
        query = query.where(Meeting.status.in_(UPCOMING)).order_by(Meeting.start_at)
    else:
        query = query.where(Meeting.status.not_in(UPCOMING)).order_by(Meeting.start_at.desc())
    if q:
        query = query.where(or_(Meeting.title.ilike(f"%{q}%"), Meeting.organizer.ilike(f"%{q}%")))
    return [_out(m) for m in (await db.scalars(query.limit(200))).all()]


@router.post("")
async def create_meeting(body: MeetingCreate, db: AsyncSession = Depends(get_db)):
    """Ajout manuel d'une réunion à partir d'un lien Meet."""
    match = MEET_RE.search(body.meet_url)
    if not match:
        raise HTTPException(422, "Lien Google Meet invalide (attendu : https://meet.google.com/xxx-xxxx-xxx)")
    meeting = Meeting(title=body.title, meet_url=match.group(0), start_at=utcnow(), auto_join=False)
    db.add(meeting)
    await db.commit()
    if body.join_now:
        await bot_control.request_join(db, meeting)
    return _out(await _get(db, meeting.id))


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: int, db: AsyncSession = Depends(get_db)):
    return _out(await _get(db, meeting_id))


@router.patch("/{meeting_id}")
async def patch_meeting(meeting_id: int, body: MeetingPatch, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(meeting, key, value)
    await db.commit()
    return _out(meeting)


@router.delete("/{meeting_id}")
async def delete_meeting(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if meeting.status in MeetingStatus.ACTIVE:
        raise HTTPException(409, "Le bot est encore dans cette réunion")
    shutil.rmtree(get_config().meeting_dir(meeting.id), ignore_errors=True)
    await db.delete(meeting)
    await db.commit()
    return {"ok": True}


@router.post("/{meeting_id}/join")
async def join(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if meeting.status in MeetingStatus.ACTIVE:
        raise HTTPException(409, "Le bot est déjà dans cette réunion")
    await bot_control.request_join(db, meeting)
    return _out(meeting)


@router.post("/{meeting_id}/control/{action}")
async def control(meeting_id: int, action: str, db: AsyncSession = Depends(get_db)):
    """Pilotage depuis l'admin : record_start | record_stop | leave."""
    if action not in ("record_start", "record_stop", "leave"):
        raise HTTPException(404)
    meeting = await _get(db, meeting_id)
    if meeting.status not in MeetingStatus.ACTIVE:
        raise HTTPException(409, "Le bot n'est pas dans cette réunion")
    await bot_control.send_control(meeting.id, action)
    return {"ok": True}


@router.get("/{meeting_id}/transcript")
async def transcript(meeting_id: int, source: str = "", db: AsyncSession = Depends(get_db)):
    await _get(db, meeting_id)
    return [SegmentOut.model_validate(s) for s in await best_segments(db, meeting_id)]


@router.get("/{meeting_id}/report")
async def get_report(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if not meeting.report:
        raise HTTPException(404, "Aucun compte rendu")
    report = meeting.report
    return ReportOut(data=ReportData.model_validate(report.data), model=report.model, updated_at=report.updated_at)


@router.put("/{meeting_id}/report")
async def put_report(meeting_id: int, body: ReportData, db: AsyncSession = Depends(get_db)):
    """Édition manuelle du compte rendu."""
    meeting = await _get(db, meeting_id)
    if meeting.report:
        meeting.report.data, meeting.report.updated_at = body.model_dump(), utcnow()
    else:
        db.add(Report(meeting_id=meeting.id, data=body.model_dump(), model="manuel"))
    await db.commit()
    return {"ok": True}


@router.post("/{meeting_id}/report/regenerate")
async def regenerate(meeting_id: int, whisper: bool = False, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if meeting.status in MeetingStatus.ACTIVE or meeting.status == MeetingStatus.PROCESSING:
        raise HTTPException(409, "Réunion en cours ou déjà en traitement")
    meeting.status, meeting.status_message = MeetingStatus.PROCESSING, "En file d'attente…"
    await db.commit()
    await bus.enqueue("process_meeting", meeting.id, not whisper)
    return {"ok": True}


@router.post("/{meeting_id}/export/drive")
async def export_drive(meeting_id: int, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if not meeting.report:
        raise HTTPException(404, "Aucun compte rendu à exporter")
    if not (await load_settings(db)).export.folder_id:
        raise HTTPException(400, "Aucun dossier Drive configuré dans les paramètres")
    await bus.enqueue("export_drive", meeting.id)
    return {"ok": True}


def _attachment(name: str) -> dict:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}


@router.get("/{meeting_id}/export/{fmt}")
async def export(meeting_id: int, fmt: str, db: AsyncSession = Depends(get_db)):
    """Téléchargements : md | docx | pdf (compte rendu) et txt (transcript)."""
    meeting = await _get(db, meeting_id)
    settings = await load_settings(db)
    if fmt == "txt":
        text = report_render.transcript_text(await best_segments(db, meeting_id))
        name = report_render.export_name(settings.export.name_pattern, meeting) + " - transcript.txt"
        return Response(text, media_type=report_render.MIME["txt"], headers=_attachment(name))
    if fmt not in ("md", "docx", "pdf"):
        raise HTTPException(404, "Format inconnu")
    if not meeting.report:
        raise HTTPException(404, "Aucun compte rendu")
    data = ReportData.model_validate(meeting.report.data)
    content = await report_render.render(fmt, meeting, data)
    name = f"{report_render.export_name(settings.export.name_pattern, meeting, data)}.{fmt}"
    return Response(content, media_type=report_render.MIME[fmt], headers=_attachment(name))


@router.get("/{meeting_id}/recording")
async def recording(meeting_id: int, download: bool = False, db: AsyncSession = Depends(get_db)):
    meeting = await _get(db, meeting_id)
    if not meeting.recording_path or not Path(meeting.recording_path).exists():
        raise HTTPException(404, "Aucun enregistrement")
    name = report_render.export_name("{date} - {title}", meeting) + ".mp4"
    return FileResponse(meeting.recording_path, media_type="video/mp4", headers=_attachment(name) if download else None)


@ws_router.websocket("/ws/meetings/{meeting_id}")
async def live(ws: WebSocket, meeting_id: int):
    """Diffuse en direct statut, segments de transcript et résumé glissant."""
    if websocket_user_id(ws) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    pubsub = bus.get_redis().pubsub()
    await pubsub.subscribe(bus.live_channel(meeting_id))

    async def forward():
        async for message in pubsub.listen():
            if message["type"] == "message":
                await ws.send_text(message["data"])

    task = asyncio.create_task(forward())
    try:
        while True:
            await ws.receive_text()  # sert uniquement à détecter la déconnexion
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await pubsub.unsubscribe()
        await pubsub.aclose()
