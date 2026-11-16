import json

from app import bus
from app.models import Meeting, MeetingStatus, Report, TranscriptSegment
from app.schemas import MASK
from app.services.settings import load_settings


async def test_setup_login_and_protection(client):
    assert (await client.get("/api/auth/status")).json() == {"needs_setup": True}
    assert (await client.get("/api/meetings")).status_code == 401

    creds = {"email": "Admin@Example.com", "password": "motdepasse123"}
    assert (await client.post("/api/auth/setup", json=creds)).status_code == 200
    assert (await client.post("/api/auth/setup", json=creds)).status_code == 409
    assert (await client.get("/api/auth/me")).json() == {"email": "admin@example.com"}

    await client.post("/api/auth/logout")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401
    bad = await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "mauvais-mdp"})
    assert bad.status_code == 401
    assert (await client.post("/api/auth/login", json=creds)).status_code == 200
    assert (await client.get("/api/auth/check")).status_code == 204


async def test_settings_secrets_are_masked_and_kept(admin, session):
    data = (await admin.get("/api/settings")).json()
    data["llm"]["api_key"] = "sk-secret"
    data["google"].update(client_id="cid", client_secret="gsecret")
    saved = (await admin.put("/api/settings", json=data)).json()
    assert saved["llm"]["api_key"] == MASK and saved["google"]["client_secret"] == MASK

    # Renvoyer la valeur masquée ne doit pas écraser le secret.
    saved["bot"]["cmd_record"] = "rec"
    await admin.put("/api/settings", json=saved)
    settings = await load_settings(session)
    assert settings.llm.api_key == "sk-secret"
    assert settings.google.client_secret == "gsecret"
    assert settings.bot.cmd_record == "rec"

    status = (await admin.get("/api/google/status")).json()
    assert status["configured"] and not status["connected"]
    url = (await admin.get("/api/google/oauth/start")).json()["url"]
    assert "client_id=cid" in url and "calendar.events.readonly" in url


async def test_manual_meeting_sends_bot_and_controls(admin, env):
    assert (await admin.post("/api/meetings", json={"meet_url": "https://example.com/x"})).status_code == 422
    resp = await admin.post("/api/meetings", json={"meet_url": "https://meet.google.com/abc-defg-hij?authuser=0"})
    meeting = resp.json()
    assert meeting["status"] == MeetingStatus.JOINING and meeting["meet_url"].endswith("abc-defg-hij")

    command = json.loads(await env["redis"].lpop(bus.BOT_COMMANDS))
    assert command["type"] == "join" and command["meeting_id"] == meeting["id"]
    assert command["bot"]["cmd_record"] == "enregistrer"

    assert (await admin.post(f"/api/meetings/{meeting['id']}/control/record_start")).status_code == 200
    control = json.loads(await env["redis"].lpop(bus.control_key(meeting["id"])))
    assert control == {"action": "record_start"}
    assert (await admin.delete(f"/api/meetings/{meeting['id']}")).status_code == 409
    assert [m["id"] for m in (await admin.get("/api/meetings")).json()] == [meeting["id"]]


async def test_report_exports(admin, session, env):
    from app.db import utcnow

    meeting = Meeting(
        title="Point projet",
        meet_url="https://meet.google.com/abc-defg-hij",
        start_at=utcnow(),
        status=MeetingStatus.DONE,
    )
    session.add(meeting)
    await session.flush()
    session.add(TranscriptSegment(meeting_id=meeting.id, speaker="Alice", text="Bonjour à tous", start_s=65))
    session.add(
        Report(
            meeting_id=meeting.id,
            data={
                "title": "Point projet",
                "summary": "On avance.",
                "decisions": ["Livrer vendredi"],
                "action_items": [{"task": "Tester", "owner": "Bob"}],
            },
        )
    )
    await session.commit()

    md = await admin.get(f"/api/meetings/{meeting.id}/export/md")
    assert md.status_code == 200 and "Livrer vendredi" in md.text and "| Tester | Bob | — |" in md.text
    assert "Point%20projet.md" in md.headers["content-disposition"]
    docx = await admin.get(f"/api/meetings/{meeting.id}/export/docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    txt = await admin.get(f"/api/meetings/{meeting.id}/export/txt")
    assert txt.text == "[01:05] Alice : Bonjour à tous"

    put = await admin.put(f"/api/meetings/{meeting.id}/report", json={"title": "Modifié", "summary": "Nouveau"})
    assert put.status_code == 200
    assert (await admin.get(f"/api/meetings/{meeting.id}/report")).json()["data"]["title"] == "Modifié"

    assert (await admin.post(f"/api/meetings/{meeting.id}/report/regenerate")).status_code == 200
    assert env["jobs"] == [("process_meeting", meeting.id, True)]
    history = (await admin.get("/api/meetings", params={"scope": "past", "q": "projet"})).json()
    assert history[0]["has_report"] is True
