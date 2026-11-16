"""Pilotage des bots via Redis et traitement des événements qu'ils émettent."""

import json
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app import bus
from app.db import utcnow
from app.models import Meeting, MeetingStatus, TranscriptSegment
from app.schemas import SegmentOut
from app.services.settings import load_settings, log_event, set_kv

log = logging.getLogger(__name__)
BROWSER_SESSION_KEY = "browser_session"


async def request_join(db: AsyncSession, meeting: Meeting) -> None:
    settings = await load_settings(db)
    meeting.status = MeetingStatus.JOINING
    meeting.status_message = ""
    await db.commit()
    payload = {
        "type": "join",
        "meeting_id": meeting.id,
        "url": meeting.meet_url,
        "bot": settings.bot.model_dump(),
        "ts": time.time(),
    }
    await bus.get_redis().rpush(bus.BOT_COMMANDS, json.dumps(payload))
    await bus.publish_live(meeting.id, {"type": "status", "status": meeting.status, "message": ""})


async def send_control(meeting_id: int, action: str) -> None:
    """action : record_start | record_stop | leave"""
    await bus.get_redis().rpush(bus.control_key(meeting_id), json.dumps({"action": action}))


async def send_manager_command(command: str) -> None:
    """Commandes sans réunion : login_start | login_finish | login_cancel"""
    await bus.get_redis().rpush(bus.BOT_COMMANDS, json.dumps({"type": command}))


async def handle_bot_event(db: AsyncSession, event: dict) -> None:
    kind = event.get("type")
    meeting_id = event.get("meeting_id")

    if kind == "browser_session":
        state = {"ok": bool(event.get("ok")), "message": event.get("message", ""), "at": utcnow().isoformat()}
        await set_kv(db, BROWSER_SESSION_KEY, state)
        await log_event(db, f"Session navigateur : {state['message']}", "info" if state["ok"] else "warning")
        return
    if kind == "log":
        await log_event(db, event.get("message", ""), event.get("level", "info"), meeting_id)
        return

    meeting = await db.get(Meeting, meeting_id) if meeting_id else None
    if meeting is None:
        log.warning("Événement pour une réunion inconnue : %s", event)
        return

    if kind == "segment":
        segment = TranscriptSegment(
            meeting_id=meeting.id,
            source="captions",
            speaker=event.get("speaker", ""),
            text=event.get("text", ""),
            start_s=float(event.get("start", 0)),
            end_s=float(event.get("end", 0)),
        )
        db.add(segment)
        await db.commit()
        out = SegmentOut.model_validate(segment).model_dump()
        await bus.publish_live(meeting.id, {"type": "segment", "segment": out})
        return

    if kind == "status":
        meeting.status = event["status"]
        meeting.status_message = event.get("message", "")
    elif kind == "error":
        meeting.status = MeetingStatus.FAILED
        meeting.status_message = event.get("message", "Erreur inconnue")
        await log_event(db, f"Bot en échec : {meeting.status_message}", "error", meeting.id)
    elif kind == "finished":
        meeting.recording_path = event.get("recording") or None
        meeting.audio_path = event.get("audio") or None
        meeting.recording_seconds = float(event.get("duration", 0))
        meeting.status_message = event.get("reason", "")
        meeting.status = MeetingStatus.PROCESSING if meeting.audio_path else MeetingStatus.DONE
        if meeting.end_at is None:
            meeting.end_at = utcnow()
    else:
        return
    await db.commit()
    await bus.publish_live(meeting.id, {"type": "status", "status": meeting.status, "message": meeting.status_message})
    if kind == "finished" and meeting.status == MeetingStatus.PROCESSING:
        await bus.enqueue("process_meeting", meeting.id)
