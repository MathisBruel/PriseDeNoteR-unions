"""Post-traitement d'une réunion : Whisper → compte rendu IA → dépôt Drive."""

import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus
from app.db import utcnow
from app.models import Meeting, MeetingStatus, Report, TranscriptSegment
from app.schemas import ReportData
from app.services import drive_export, llm, report_render, transcription
from app.services.google import GoogleNotConnected, google_client
from app.services.settings import load_settings, log_event

log = logging.getLogger(__name__)


async def best_segments(db: AsyncSession, meeting_id: int) -> list[TranscriptSegment]:
    """Transcript de référence : Whisper s'il existe, sinon les sous-titres Meet."""
    for source in ("whisper", "captions"):
        rows = (
            await db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting_id, TranscriptSegment.source == source)
                .order_by(TranscriptSegment.start_s, TranscriptSegment.id)
            )
        ).all()
        if rows:
            return list(rows)
    return []


async def _set_status(db: AsyncSession, meeting: Meeting, status: str, message: str = "") -> None:
    meeting.status, meeting.status_message = status, message
    await db.commit()
    await bus.publish_live(meeting.id, {"type": "status", "status": status, "message": message})


async def run_whisper(db: AsyncSession, meeting: Meeting) -> None:
    settings = (await load_settings(db)).transcription
    if not settings.whisper_enabled or not meeting.audio_path or not Path(meeting.audio_path).exists():
        return
    await _set_status(db, meeting, MeetingStatus.PROCESSING, "Transcription Whisper en cours…")
    segments = await transcription.transcribe(
        meeting.audio_path, settings.whisper_model, settings.device, settings.language
    )
    captions = (
        await db.scalars(
            select(TranscriptSegment).where(
                TranscriptSegment.meeting_id == meeting.id, TranscriptSegment.source == "captions"
            )
        )
    ).all()
    caps = [transcription.Seg(c.start_s, c.end_s, c.text, c.speaker) for c in captions]
    merged = transcription.merge_consecutive(transcription.assign_speakers(segments, caps))
    await db.execute(
        delete(TranscriptSegment).where(
            TranscriptSegment.meeting_id == meeting.id, TranscriptSegment.source == "whisper"
        )
    )
    db.add_all(
        TranscriptSegment(
            meeting_id=meeting.id, source="whisper", speaker=s.speaker, text=s.text, start_s=s.start, end_s=s.end
        )
        for s in merged
    )
    await db.commit()


async def build_report(db: AsyncSession, meeting: Meeting) -> ReportData:
    settings = await load_settings(db)
    segments = await best_segments(db, meeting.id)
    if not segments:
        raise ValueError("Transcript vide : impossible de générer un compte rendu")
    await _set_status(db, meeting, MeetingStatus.PROCESSING, "Rédaction du compte rendu par l'IA…")
    meta = {"title": meeting.title, "date": report_render.local_date(meeting), "attendees": meeting.attendees}
    data = await llm.generate_report(settings.llm, meta, report_render.transcript_text(segments))
    if not data.title:
        data.title = meeting.title
    report = await db.scalar(select(Report).where(Report.meeting_id == meeting.id))
    if report:
        report.data, report.model, report.updated_at = data.model_dump(), llm.model_name(settings.llm), utcnow()
    else:
        db.add(Report(meeting_id=meeting.id, data=data.model_dump(), model=llm.model_name(settings.llm)))
    await db.commit()
    return data


async def export_to_drive(db: AsyncSession, meeting: Meeting) -> list[dict]:
    settings = await load_settings(db)
    report = await db.scalar(select(Report).where(Report.meeting_id == meeting.id))
    if not report:
        raise ValueError("Aucun compte rendu à exporter")
    transcript = report_render.transcript_text(await best_segments(db, meeting.id))
    client = await google_client(db, timeout=120)
    try:
        links = await drive_export.export_meeting(
            client, settings.export, meeting, ReportData.model_validate(report.data), transcript
        )
    finally:
        await client.aclose()
    meeting.drive_links = links
    await db.commit()
    return links


async def process_meeting(db: AsyncSession, meeting_id: int, skip_whisper: bool = False) -> None:
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        return
    warnings: list[str] = []
    try:
        if not skip_whisper:
            try:
                await run_whisper(db, meeting)
            except Exception as exc:  # on retombe sur les sous-titres Meet
                log.exception("Whisper en échec")
                warnings.append(f"Whisper indisponible ({exc}) : transcript issu des sous-titres Meet")
        await build_report(db, meeting)
        if (await load_settings(db)).export.drive_enabled:
            try:
                await _set_status(db, meeting, MeetingStatus.PROCESSING, "Dépôt dans Google Drive…")
                await export_to_drive(db, meeting)
            except (GoogleNotConnected, ValueError, Exception) as exc:
                log.exception("Export Drive en échec")
                warnings.append(f"Export Drive en échec : {exc}")
        await _set_status(db, meeting, MeetingStatus.DONE, " · ".join(warnings))
        for warning in warnings:
            await log_event(db, warning, "warning", meeting.id)
        await log_event(db, "Compte rendu généré", "info", meeting.id)
    except Exception as exc:
        log.exception("Post-traitement en échec")
        await db.rollback()
        await _set_status(db, meeting, MeetingStatus.FAILED, f"Post-traitement en échec : {exc}")
        await log_event(db, f"Post-traitement en échec : {exc}", "error", meeting.id)
