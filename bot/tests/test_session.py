"""Tests de bout en bout du bot sur une page Meet factice (aucun accès à Google requis)."""

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from bot.session import MeetSession

FAKE = Path(__file__).parent / "fake_meet.html"
BOT = {"command_prefix": "!", "cmd_record": "enregistrer", "cmd_stop": "stop", "cmd_leave": "quitter",
       "cmd_status": "statut", "announce": True, "admission_timeout_minutes": 1}


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.starts = self.stops = 0
        self._t0 = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self._t0

    async def start(self):
        self.is_recording, self.starts = True, self.starts + 1

    async def stop(self):
        if self.is_recording:
            self.is_recording, self.stops = False, self.stops + 1

    async def finalize(self):
        await self.stop()
        return (Path("/data/recording.mp4"), Path("/data/audio.ogg")) if self.starts else (None, None)


class Harness:
    def __init__(self, page, query="", **bot):
        self.page = page
        self.events: list[dict] = []
        self.recorder = FakeRecorder()
        payload = {"meeting_id": 7, "url": f"{FAKE.as_uri()}{query}", "bot": {**BOT, **bot}}
        self.session = MeetSession(payload, self._emit, self.recorder, check_interval=0.2)
        self.session.aggregator.idle = 0.4
        self.task = asyncio.create_task(self.session.run(page))

    async def _emit(self, event):
        self.events.append(event)

    async def wait_event(self, timeout=10, **match):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events:
                if all(event.get(k) == v for k, v in match.items()):
                    return event
            await asyncio.sleep(0.05)
        raise AssertionError(f"Événement {match} non reçu ; reçus : {self.events}")

    async def wait_chat(self, fragment, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            texts = await self.page.evaluate("fake.chatTexts ? fake.chatTexts() : []")
            if any(fragment in t for t in texts):
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"« {fragment} » absent du chat : {texts}")

    async def chat(self, sender, text):
        await self.page.evaluate("([s, t]) => fake.chat(s, t)", [sender, text])

    async def caption(self, key, speaker, text):
        await self.page.evaluate("([k, s, t]) => fake.caption(k, s, t)", [key, speaker, text])


@pytest.fixture
async def page():
    executable = os.environ.get("PW_CHROMIUM") or None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=executable)
        yield await browser.new_page()
        await browser.close()


async def test_full_session_driven_by_chat_commands(page):
    h = Harness(page, "?admit=3000")
    await h.wait_event(type="status", status="waiting")
    await h.wait_event(type="status", status="in_meeting")
    assert await page.evaluate("fake.micOff && fake.camOff"), "micro et caméra doivent être coupés avant d'entrer"
    await h.wait_chat("Bonjour, je suis l'assistant de prise de notes")

    # Sous-titre affiché avant l'enregistrement : ne doit pas apparaître dans le transcript.
    await h.caption("a", "Alice Martin", "Phrase dite avant l'enregistrement.")
    await h.chat("Alice Martin", "bonjour tout le monde")
    await asyncio.sleep(0.8)
    assert h.recorder.starts == 0 and not [e for e in h.events if e["type"] == "segment"]

    await h.chat("Alice Martin", "!enregistrer")
    event = await h.wait_event(type="status", status="recording")
    assert "Alice Martin" in event["message"] and h.recorder.is_recording
    await h.wait_chat("Enregistrement démarré")

    # Meet réécrit le bloc au fil de la parole, puis un autre intervenant répond.
    await h.caption("a", "Alice Martin", "Phrase dite avant l'enregistrement. On valide")
    await asyncio.sleep(0.15)
    await h.caption("a", "Alice Martin", "Phrase dite avant l'enregistrement. On valide le budget.")
    await h.caption("b", "Bob Durand", "D'accord pour moi.")
    first = await h.wait_event(type="segment", speaker="Alice Martin")
    second = await h.wait_event(type="segment", speaker="Bob Durand")
    assert first["text"] == "On valide le budget." and second["text"] == "D'accord pour moi."
    assert first["meeting_id"] == 7 and first["end"] >= first["start"] >= 0

    # Deuxième message d'un même groupe (même expéditeur) : l'expéditeur reste identifié.
    await h.chat("Alice Martin", "!statut")
    await h.wait_chat("Enregistrement en cours depuis")
    await h.chat("Bob Durand", "!enregistrer")
    await h.wait_chat("déjà en cours")

    await h.chat("Bob Durand", "!stop")
    await h.wait_chat("Enregistrement arrêté")
    assert not h.recorder.is_recording
    await h.caption("b", "Bob Durand", "D'accord pour moi. Ceci est hors enregistrement.")

    await h.chat("Alice Martin", "!quitter")
    done = await h.wait_event(type="finished")
    await asyncio.wait_for(h.task, 10)
    assert done["reason"] == "Départ demandé par Alice Martin"
    assert done["recording"].endswith("recording.mp4") and done["audio"].endswith("audio.ogg")
    assert await page.evaluate("fake.left"), "le bot doit cliquer sur « Quitter l'appel »"
    segments = [e["text"] for e in h.events if e["type"] == "segment"]
    assert segments == ["On valide le budget.", "D'accord pour moi."]
    # Les réponses du bot ne sont pas réinterprétées comme des commandes.
    assert h.recorder.starts == 1 and h.recorder.stops == 1


async def test_only_allowed_senders_can_drive_the_bot_and_meeting_end_is_detected(page):
    h = Harness(page, allowed_senders=["Alice Martin"], announce=False)
    await h.wait_event(type="status", status="in_meeting")
    await h.chat("Bob Durand", "!enregistrer")
    await h.wait_chat("pas autorisé")
    assert h.recorder.starts == 0

    await page.evaluate("fake.end()")
    done = await h.wait_event(type="finished")
    await asyncio.wait_for(h.task, 10)
    assert done["reason"] == "Réunion terminée" and done["recording"] == ""


async def test_admin_control_and_alone_timeout(page):
    h = Harness(page, "?participants=1", alone_timeout_minutes=0.02, announce=False)
    await h.wait_event(type="status", status="in_meeting")
    h.session.queue.put_nowait(("control", {"action": "record_start"}))
    event = await h.wait_event(type="status", status="recording")
    assert "administration" in event["message"]
    done = await h.wait_event(type="finished")
    assert "seul" in done["reason"] and done["audio"]


async def test_refused_admission_is_reported(page):
    h = Harness(page, "?mode=deny")
    error = await h.wait_event(type="error")
    await asyncio.wait_for(h.task, 10)
    assert "refusée" in error["message"] and "denied your request" in error["message"]
    assert not [e for e in h.events if e["type"] == "finished"]


@pytest.mark.skipif(not (shutil.which("Xvfb") and shutil.which("ffmpeg")), reason="Xvfb et ffmpeg requis")
async def test_recorder_produces_playable_files(tmp_path):
    from bot.recorder import Recorder

    xvfb = await asyncio.create_subprocess_exec("Xvfb", ":93", "-screen", "0", "1280x720x24", "-nolisten", "tcp")
    await asyncio.sleep(1)
    try:
        recorder = Recorder(tmp_path, ":93", ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()
        assert 1.5 < recorder.elapsed() < 4 and not recorder.is_recording
        await recorder.start()  # deuxième partie, comme après un !stop puis !enregistrer
        await asyncio.sleep(1.5)
        video, audio = await recorder.finalize()
        assert video.name == "recording.mp4" and audio.name == "audio.ogg"
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video),
            stdout=asyncio.subprocess.PIPE)
        duration = float((await probe.communicate())[0])
        assert 2.5 < duration < 6
        assert audio.stat().st_size > 0 and not list(tmp_path.glob("part_*"))
    finally:
        xvfb.terminate()
