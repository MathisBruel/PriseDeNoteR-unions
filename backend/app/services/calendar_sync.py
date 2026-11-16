"""Synchronisation de l'agenda : repère les réunions Meet où le compte est invité."""

import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import Meeting, MeetingStatus

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MEET_RE = re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}")
WINDOW_DAYS = 14


def extract_meet_url(event: dict) -> str | None:
    if MEET_RE.match(event.get("hangoutLink") or ""):
        return MEET_RE.match(event["hangoutLink"]).group(0)
    for entry in (event.get("conferenceData") or {}).get("entryPoints", []):
        if entry.get("entryPointType") == "video" and MEET_RE.match(entry.get("uri") or ""):
            return MEET_RE.match(entry["uri"]).group(0)
    for field in ("location", "description"):
        found = MEET_RE.search(event.get(field) or "")
        if found:
            return found.group(0)
    return None


def should_attend(event: dict) -> bool:
    """Événement non annulé que le compte n'a pas décliné."""
    if event.get("status") == "cancelled":
        return False
    for attendee in event.get("attendees", []):
        if attendee.get("self"):
            return attendee.get("responseStatus") != "declined"
    return True  # pas de liste d'invités : le compte est organisateur


def parse_time(value: dict | None) -> datetime | None:
    if not value or "dateTime" not in value:
        return None  # événement « journée entière » : ignoré
    parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    return parsed.astimezone(UTC).replace(tzinfo=None)


async def fetch_events(client: httpx.AsyncClient) -> list[dict]:
    now = datetime.now(UTC)
    params = {
        "timeMin": (now - timedelta(hours=4)).isoformat(),
        "timeMax": (now + timedelta(days=WINDOW_DAYS)).isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "250",
    }
    events: list[dict] = []
    while True:
        resp = await client.get(EVENTS_URL, params=params)
        resp.raise_for_status()
        body = resp.json()
        events.extend(body.get("items", []))
        if not body.get("nextPageToken"):
            return events
        params["pageToken"] = body["nextPageToken"]


async def apply_events(db: AsyncSession, events: list[dict], default_auto_join: bool = True) -> dict:
    """Crée/actualise les réunions planifiées et annule celles qui ont disparu de l'agenda."""
    created = updated = cancelled = 0
    seen: set[str] = set()
    for event in events:
        url, start = extract_meet_url(event), parse_time(event.get("start"))
        if not url or not start or not should_attend(event):
            continue
        seen.add(event["id"])
        fields = {
            "title": event.get("summary") or "Réunion sans titre",
            "meet_url": url,
            "start_at": start,
            "end_at": parse_time(event.get("end")),
            "organizer": (event.get("organizer") or {}).get("email", ""),
            "attendees": [
                a.get("displayName") or a.get("email", "") for a in event.get("attendees", []) if not a.get("self")
            ],
        }
        meeting = await db.scalar(select(Meeting).where(Meeting.google_event_id == event["id"]))
        if meeting is None:
            db.add(Meeting(google_event_id=event["id"], auto_join=default_auto_join, **fields))
            created += 1
        elif meeting.status in (MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED):
            for key, value in fields.items():
                setattr(meeting, key, value)
            meeting.status = MeetingStatus.SCHEDULED
            updated += 1

    stale = await db.scalars(
        select(Meeting).where(
            Meeting.status == MeetingStatus.SCHEDULED,
            Meeting.google_event_id.is_not(None),
            Meeting.start_at > utcnow(),
            Meeting.start_at < utcnow() + timedelta(days=WINDOW_DAYS - 1),
        )
    )
    for meeting in stale:
        if meeting.google_event_id not in seen:
            meeting.status = MeetingStatus.CANCELLED
            cancelled += 1
    await db.commit()
    return {"created": created, "updated": updated, "cancelled": cancelled}
