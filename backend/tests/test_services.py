import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import bus
from app.db import utcnow
from app.models import Meeting, MeetingStatus, Report, TranscriptSegment
from app.schemas import ExportSettings, LLMSettings, ReportData
from app.services import bot_control, calendar_sync, drive_export, llm, pipeline, report_render
from app.services.transcription import Seg, assign_speakers, merge_consecutive
from app.tasks import worker

MEET = "https://meet.google.com/abc-defg-hij"


def event(event_id="e1", start=None, **extra):
    start = start or (utcnow() + timedelta(hours=1))
    base = {
        "id": event_id,
        "summary": "Comité",
        "hangoutLink": MEET,
        "start": {"dateTime": start.isoformat() + "Z"},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat() + "Z"},
        "organizer": {"email": "chef@example.com"},
        "attendees": [
            {"email": "bot@example.com", "self": True, "responseStatus": "needsAction"},
            {"email": "alice@example.com", "displayName": "Alice"},
        ],
    }
    return {**base, **extra}


# ---------- Agenda ----------


def test_extract_meet_url_variants():
    assert calendar_sync.extract_meet_url({"hangoutLink": MEET + "?authuser=1"}) == MEET
    conference = {"conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": MEET}]}}
    assert calendar_sync.extract_meet_url(conference) == MEET
    assert calendar_sync.extract_meet_url({"description": f"Lien : {MEET} à bientôt"}) == MEET
    assert calendar_sync.extract_meet_url({"location": "Salle 3"}) is None


def test_should_attend():
    assert calendar_sync.should_attend(event())
    assert not calendar_sync.should_attend(event(status="cancelled"))
    assert not calendar_sync.should_attend(event(attendees=[{"self": True, "responseStatus": "declined"}]))
    assert calendar_sync.should_attend({"id": "x"})  # organisateur sans invités


def test_parse_time_converts_to_utc_and_skips_all_day():
    assert calendar_sync.parse_time({"dateTime": "2026-03-02T10:00:00+01:00"}).hour == 9
    assert calendar_sync.parse_time({"date": "2026-03-02"}) is None


async def test_apply_events_create_update_cancel(session):
    declined = event("e2", attendees=[{"self": True, "responseStatus": "declined"}])
    assert await calendar_sync.apply_events(session, [event(), declined]) == {
        "created": 1,
        "updated": 0,
        "cancelled": 0,
    }
    meeting = await session.scalar(select(Meeting))
    assert meeting.title == "Comité" and meeting.attendees == ["Alice"] and meeting.meet_url == MEET

    await calendar_sync.apply_events(session, [event(summary="Comité élargi")])
    await session.refresh(meeting)
    assert meeting.title == "Comité élargi"

    assert (await calendar_sync.apply_events(session, []))["cancelled"] == 1
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.CANCELLED


async def test_scheduler_sends_bot_only_when_due(session, env):
    soon = Meeting(title="Bientôt", meet_url=MEET, start_at=utcnow() + timedelta(seconds=30))
    later = Meeting(title="Plus tard", meet_url=MEET, start_at=utcnow() + timedelta(hours=2))
    missed = Meeting(title="Passée", meet_url=MEET, start_at=utcnow() - timedelta(hours=3))
    manual = Meeting(title="Sans auto", meet_url=MEET, start_at=utcnow(), auto_join=False)
    session.add_all([soon, later, missed, manual])
    await session.commit()

    assert await worker.schedule_bots_job() == 1
    for m in (soon, later, missed, manual):
        await session.refresh(m)
    assert soon.status == MeetingStatus.JOINING
    assert later.status == MeetingStatus.SCHEDULED and manual.status == MeetingStatus.SCHEDULED
    assert missed.status == MeetingStatus.CANCELLED
    assert await env["redis"].llen(bus.BOT_COMMANDS) == 1
    assert await worker.schedule_bots_job() == 0  # pas de double envoi


# ---------- Événements des bots ----------


async def test_bot_events_update_meeting(session, env):
    meeting = Meeting(title="M", meet_url=MEET, start_at=utcnow(), status=MeetingStatus.JOINING)
    session.add(meeting)
    await session.commit()
    pubsub = env["redis"].pubsub()
    await pubsub.subscribe(bus.live_channel(meeting.id))

    await bot_control.handle_bot_event(session, {"type": "status", "meeting_id": meeting.id, "status": "recording"})
    await bot_control.handle_bot_event(
        session,
        {"type": "segment", "meeting_id": meeting.id, "speaker": "Alice", "text": "Bonjour", "start": 1, "end": 3},
    )
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.RECORDING
    assert (await session.scalar(select(TranscriptSegment))).text == "Bonjour"

    messages = []
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg:
            messages.append(json.loads(msg["data"])["type"])
        if len(messages) == 2:
            break
    assert messages == ["status", "segment"]

    await bot_control.handle_bot_event(
        session,
        {
            "type": "finished",
            "meeting_id": meeting.id,
            "recording": "/data/r.mp4",
            "audio": "/data/a.ogg",
            "duration": 120,
        },
    )
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.PROCESSING and meeting.recording_seconds == 120
    assert env["jobs"] == [("process_meeting", meeting.id)]

    await bot_control.handle_bot_event(session, {"type": "error", "meeting_id": meeting.id, "message": "Refusé"})
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.FAILED and meeting.status_message == "Refusé"


async def test_finished_without_recording_is_done(session, env):
    meeting = Meeting(title="M", meet_url=MEET, start_at=utcnow(), status=MeetingStatus.IN_MEETING)
    session.add(meeting)
    await session.commit()
    await bot_control.handle_bot_event(session, {"type": "finished", "meeting_id": meeting.id, "reason": "Seul"})
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.DONE and env["jobs"] == []


# ---------- Transcription ----------


def test_assign_speakers_by_overlap_and_proximity():
    captions = [Seg(0, 5, "", "Alice"), Seg(5, 12, "", "Bob")]
    whisper = [Seg(0.5, 4, "a"), Seg(4.5, 9, "b"), Seg(20, 22, "c"), Seg(100, 101, "d")]
    assert [s.speaker for s in assign_speakers(whisper, captions)] == ["Alice", "Bob", "Bob", ""]


def test_merge_consecutive_same_speaker():
    merged = merge_consecutive(
        [Seg(0, 2, "Un.", "A"), Seg(2.5, 4, "Deux.", "A"), Seg(4, 5, "Trois.", "B"), Seg(30, 31, "Quatre.", "B")]
    )
    assert [(s.speaker, s.text) for s in merged] == [("A", "Un. Deux."), ("B", "Trois."), ("B", "Quatre.")]


# ---------- IA ----------


def test_parse_json_object_tolerates_wrapping():
    assert llm.parse_json_object('Voici :\n```json\n{"title": "T"}\n```') == {"title": "T"}
    assert llm.parse_json_object('blabla {"a": {"b": 1}} fin') == {"a": {"b": 1}}
    with pytest.raises(llm.LLMError):
        llm.parse_json_object("pas de json")


def test_model_name_prefix():
    assert llm.model_name(LLMSettings(provider="anthropic", model="claude-sonnet-5-5")) == "anthropic/claude-sonnet-5-5"
    assert llm.model_name(LLMSettings(provider="openai_compatible", model="local")) == "openai/local"
    assert llm.model_name(LLMSettings(provider="gemini", model="gemini/pro")) == "gemini/pro"


async def test_generate_report_map_reduce_for_long_transcripts():
    calls = []

    async def fake(settings, system, user, max_tokens=0):
        calls.append(user)
        if "Réponds UNIQUEMENT" in user:
            return json.dumps(
                {
                    "title": "Budget",
                    "summary": "S",
                    "decisions": ["D1"],
                    "action_items": [{"task": "T", "owner": "Bob", "due": ""}],
                }
            )
        return "notes"

    line = "[00:01] Alice : " + "mot " * 50
    short = await llm.generate_report(LLMSettings(), {"title": "x"}, line, complete_fn=fake)
    assert short.title == "Budget" and short.action_items[0].owner == "Bob" and len(calls) == 1

    calls.clear()
    long_text = "\n".join([line] * 700)  # > 2 blocs
    await llm.generate_report(LLMSettings(), {"title": "x"}, long_text, complete_fn=fake)
    assert len(calls) >= 3 and "Notes détaillées par partie" in calls[-1]


# ---------- Rendus ----------


def sample():
    meeting = Meeting(
        id=1,
        title="Point hebdo",
        meet_url=MEET,
        start_at=utcnow().replace(year=2026, month=1, day=15, hour=9, minute=0),
        recording_seconds=3725,
    )
    report = ReportData(
        title="Point hebdo : budget <2027>",
        summary="Résumé.\nSuite.",
        participants=["Alice", "Bob"],
        topics=[{"title": "Budget", "points": ["Hausse de 5 %", "Arbitrage en mars"]}],
        decisions=["Valider le devis"],
        action_items=[{"task": "Envoyer le devis", "owner": "Bob", "due": "vendredi"}],
        open_questions=["Qui pilote ?"],
    )
    return meeting, report


def test_markdown_render():
    md = report_render.render_markdown(*sample())
    assert md.startswith("# Point hebdo : budget <2027>\n")
    assert "**Date :** 15/01/" in md and "à 10:00" in md  # UTC → Europe/Paris
    assert "**Durée enregistrée :** 1:02:05" in md
    assert "### Budget\n\n- Hausse de 5 %\n- Arbitrage en mars" in md
    assert "| Envoyer le devis | Bob | vendredi |" in md
    assert "\n\n\n" not in md


def test_html_is_escaped_and_docx_contains_content():
    import io

    from docx import Document

    meeting, report = sample()
    assert "budget &lt;2027&gt;" in report_render.render_html(meeting, report)
    doc = Document(io.BytesIO(report_render.render_docx(meeting, report)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Valider le devis" in text and "Qui pilote ?" in text
    assert doc.tables[0].rows[1].cells[1].text == "Bob"


async def test_pdf_render():
    pytest.importorskip("weasyprint")
    pdf = await report_render.render("pdf", *sample())
    assert pdf[:5] == b"%PDF-" and len(pdf) > 3000


def test_export_name_sanitized_and_fallback():
    meeting, report = sample()
    assert report_render.export_name("{date} - {title}", meeting, report) == "2026-01-15 - Point hebdo budget 2027"
    assert "Point hebdo" in report_render.export_name("{inconnu}", meeting)
    assert "/" not in report_render.export_name("{title}", Meeting(title="a/b", meet_url=MEET, start_at=utcnow()))


# ---------- Drive ----------


async def test_drive_export_uploads_selected_formats():
    uploads, folders = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files" and request.method == "POST":
            folders.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "sub1"})
        if request.url.path == "/upload/drive/v3/files":
            meta = json.loads(request.content.split(b"\r\n\r\n")[1].split(b"\r\n--")[0])
            uploads.append(meta)
            return httpx.Response(
                200, json={"id": "f", "name": meta["name"], "webViewLink": "https://d/" + meta["name"]}
            )
        return httpx.Response(404)

    meeting, report = sample()
    settings = ExportSettings(drive_enabled=True, folder_id="root1", formats=["md", "gdoc"], include_transcript=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        links = await drive_export.export_meeting(client, settings, meeting, report, "[00:01] A : b")

    assert folders[0]["parents"] == ["root1"] and folders[0]["mimeType"] == drive_export.FOLDER_MIME
    assert [link["kind"] for link in links] == ["md", "gdoc", "transcript"]
    assert all(u["parents"] == ["sub1"] for u in uploads)
    assert uploads[1]["mimeType"] == drive_export.GDOC_MIME


# ---------- Pipeline ----------


async def test_pipeline_generates_report_from_captions(session, env, monkeypatch):
    meeting = Meeting(title="Revue", meet_url=MEET, start_at=utcnow(), status=MeetingStatus.PROCESSING)
    session.add(meeting)
    await session.flush()
    session.add(TranscriptSegment(meeting_id=meeting.id, speaker="Alice", text="On valide.", start_s=3))
    await session.commit()

    async def fake_report(settings, meta, transcript, **kw):
        assert "[00:03] Alice : On valide." in transcript and meta["title"] == "Revue"
        return ReportData(summary="Validé")

    monkeypatch.setattr(llm, "generate_report", fake_report)
    await pipeline.process_meeting(session, meeting.id)
    await session.refresh(meeting)
    report = await session.scalar(select(Report))
    assert meeting.status == MeetingStatus.DONE
    assert report.data["summary"] == "Validé" and report.data["title"] == "Revue"


async def test_pipeline_failure_is_reported(session, env):
    meeting = Meeting(title="Vide", meet_url=MEET, start_at=utcnow(), status=MeetingStatus.PROCESSING)
    session.add(meeting)
    await session.commit()
    await pipeline.process_meeting(session, meeting.id)
    await session.refresh(meeting)
    assert meeting.status == MeetingStatus.FAILED and "Transcript vide" in meeting.status_message
