"""Worker arq : synchro agenda, planification des bots, résumés en direct, post-traitement."""

import asyncio
import json
import logging
import shutil
from datetime import timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app import bus
from app.config import get_config
from app.db import get_sessionmaker, utcnow
from app.models import EventLog, Meeting, MeetingStatus, TranscriptSegment
from app.services import bot_control, calendar_sync, llm, pipeline, report_render
from app.services.google import GoogleNotConnected, google_client
from app.services.settings import load_settings, log_event

log = logging.getLogger(__name__)


async def sync_calendar_job(ctx=None) -> dict | None:
    async with get_sessionmaker()() as db:
        try:
            client = await google_client(db)
        except GoogleNotConnected:
            return None
        try:
            events = await calendar_sync.fetch_events(client)
        except Exception as exc:
            await log_event(db, f"Synchronisation de l'agenda en échec : {exc}", "error")
            return None
        finally:
            await client.aclose()
        settings = await load_settings(db)
        return await calendar_sync.apply_events(db, events, settings.bot.auto_join)


async def schedule_bots_job(ctx=None) -> int:
    """Envoie un bot dans chaque réunion planifiée dont l'heure est arrivée."""
    async with get_sessionmaker()() as db:
        settings = await load_settings(db)
        now = utcnow()
        due = await db.scalars(
            select(Meeting).where(
                Meeting.status == MeetingStatus.SCHEDULED,
                Meeting.auto_join.is_(True),
                Meeting.start_at <= now + timedelta(minutes=settings.bot.join_minutes_before),
            )
        )
        count = 0
        for meeting in due:
            end = meeting.end_at or meeting.start_at + timedelta(hours=1)
            if end <= now:
                meeting.status, meeting.status_message = MeetingStatus.CANCELLED, "Réunion manquée"
                await db.commit()
                continue
            await bot_control.request_join(db, meeting)
            count += 1
        return count


async def live_summary_job(ctx=None) -> None:
    """Met à jour le résumé glissant des réunions en cours d'enregistrement."""
    async with get_sessionmaker()() as db:
        settings = await load_settings(db)
        interval = settings.llm.live_summary_minutes
        if interval <= 0:
            return
        meetings = await db.scalars(select(Meeting).where(Meeting.status == MeetingStatus.RECORDING))
        for meeting in meetings:
            if meeting.live_summary_at and utcnow() - meeting.live_summary_at < timedelta(minutes=interval):
                continue
            new = (
                await db.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.meeting_id == meeting.id,
                        TranscriptSegment.source == "captions",
                        TranscriptSegment.id > meeting.live_summary_segment_id,
                    )
                    .order_by(TranscriptSegment.id)
                )
            ).all()
            if not new:
                continue
            try:
                summary = await llm.update_live_summary(
                    settings.llm, meeting.live_summary, report_render.transcript_text(new, with_time=False)
                )
            except llm.LLMError as exc:
                await log_event(db, f"Résumé en direct en échec : {exc}", "warning", meeting.id)
                meeting.live_summary_at = utcnow()
                await db.commit()
                continue
            meeting.live_summary = summary
            meeting.live_summary_at = utcnow()
            meeting.live_summary_segment_id = new[-1].id
            await db.commit()
            await bus.publish_live(meeting.id, {"type": "summary", "summary": summary})


async def retention_job(ctx=None) -> int:
    """Supprime les réunions (et leurs fichiers) plus anciennes que la durée de rétention."""
    async with get_sessionmaker()() as db:
        days = (await load_settings(db)).retention.days
        if days <= 0:
            return 0
        limit = utcnow() - timedelta(days=days)
        old = (
            await db.scalars(
                select(Meeting).where(
                    Meeting.start_at < limit,
                    Meeting.status.in_([MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED]),
                )
            )
        ).all()
        for meeting in old:
            shutil.rmtree(get_config().meeting_dir(meeting.id), ignore_errors=True)
            await db.delete(meeting)
        for entry in await db.scalars(select(EventLog).where(EventLog.created_at < limit)):
            await db.delete(entry)
        await db.commit()
        return len(old)


async def process_meeting(ctx, meeting_id: int, skip_whisper: bool = False) -> None:
    async with get_sessionmaker()() as db:
        await pipeline.process_meeting(db, meeting_id, skip_whisper)


async def export_drive(ctx, meeting_id: int) -> None:
    async with get_sessionmaker()() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            return
        try:
            await pipeline.export_to_drive(db, meeting)
            await log_event(db, "Documents déposés dans Google Drive", "info", meeting_id)
        except Exception as exc:
            await log_event(db, f"Export Drive en échec : {exc}", "error", meeting_id)
        await bus.publish_live(meeting_id, {"type": "refresh"})


async def consume_bot_events() -> None:
    """Lit en continu le flux d'événements des bots (reprise au dernier événement traité)."""
    redis = bus.get_redis()
    while True:
        try:
            cursor = await redis.get(bus.BOT_EVENTS_CURSOR) or "0"
            batches = await redis.xread({bus.BOT_EVENTS: cursor}, count=50, block=5000)
            for _stream, entries in batches or []:
                for entry_id, fields in entries:
                    try:
                        async with get_sessionmaker()() as db:
                            await bot_control.handle_bot_event(db, json.loads(fields["data"]))
                    except Exception:
                        log.exception("Événement de bot non traité : %s", fields)
                    await redis.set(bus.BOT_EVENTS_CURSOR, entry_id)
            await redis.xtrim(bus.BOT_EVENTS, maxlen=5000, approximate=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Erreur de lecture du flux d'événements")
            await asyncio.sleep(2)


async def startup(ctx) -> None:
    logging.basicConfig(level=logging.INFO)
    ctx["consumer"] = asyncio.create_task(consume_bot_events())


async def shutdown(ctx) -> None:
    ctx["consumer"].cancel()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_config().redis_url)
    functions = [process_meeting, export_drive]
    cron_jobs = [
        cron(sync_calendar_job, minute=set(range(0, 60, 2)), second=10),
        cron(schedule_bots_job, second={0, 30}),
        cron(live_summary_job, second=45),
        cron(retention_job, hour=3, minute=17),
    ]
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 3 * 3600
    max_jobs = 4
