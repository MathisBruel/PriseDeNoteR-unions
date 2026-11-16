"""Session d'un bot dans une réunion : entrée, commandes de chat, enregistrement, transcript, sortie.

Lancé par le gestionnaire comme sous-processus : `python -m bot.session` (réglages dans PDN_PAYLOAD).
"""

import asyncio
import json
import logging
import os
import time

from playwright.async_api import async_playwright

from bot import commands as cmd
from bot import config
from bot.aggregator import CaptionAggregator
from bot.meet import captions, chat
from bot.meet.join import JoinError, join_meeting, leave_meeting, meeting_state

log = logging.getLogger("bot.session")

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--password-store=basic",
    "--window-position=0,0",
    f"--window-size={config.SCREEN[0]},{config.SCREEN[1]}",
    "--kiosk",
    "--autoplay-policy=no-user-gesture-required",
    "--use-fake-ui-for-media-stream",  # accepte la demande micro/caméra sans boîte de dialogue
    "--use-fake-device-for-media-stream",  # périphériques factices (coupés dès l'écran d'entrée)
    "--disable-notifications",
]


def fmt_elapsed(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs:02d} s"


class MeetSession:
    def __init__(self, payload: dict, emit, recorder, check_interval: float = 3.0):
        self.meeting_id = payload["meeting_id"]
        self.url = payload["url"]
        self.cfg = payload.get("bot", {})
        self.emit_raw = emit
        self.recorder = recorder
        self.check_interval = check_interval
        self.queue: asyncio.Queue = asyncio.Queue()
        self.aggregator = CaptionAggregator()
        self.captions_now: dict[str, tuple[str, str]] = {}  # état courant des blocs de sous-titres
        self.msg = cmd.messages(self.cfg)
        self.joined = False
        self.leave_reason: str | None = None
        self.sent: set[str] = set()
        self.started = time.monotonic()
        self.alone_since: float | None = None
        self.not_in_call = 0
        self.had_recording = False

    # ---------- Événements vers le backend ----------

    async def emit(self, kind: str, **data) -> None:
        await self.emit_raw({"type": kind, "meeting_id": self.meeting_id, **data})

    async def status(self, status: str, message: str = "") -> None:
        await self.emit("status", status=status, message=message)

    # ---------- Cycle de vie ----------

    async def run(self, page) -> None:
        """Déroule toute la session sur une page Playwright déjà ouverte."""
        try:
            await self.status("joining")
            await join_meeting(
                page,
                self.url,
                self.cfg.get("admission_timeout_minutes", 10) * 60,
                on_waiting=lambda: self.status("waiting", "En attente d'admission"),
            )
            self.joined = True
            await chat.open_chat(page)
            await chat.start_chat_observer(page, lambda m: self.queue.put_nowait(("chat", m)))
            if not await captions.enable_captions(page):
                await self.emit("log", level="warning", message="Sous-titres Meet introuvables : transcript live absent")
            await captions.start_captions_observer(page, lambda c: self.queue.put_nowait(("caption", c)))
            await self.status("in_meeting")  # le bot est prêt à recevoir des commandes
            if self.cfg.get("announce", True):
                await self.say(page, self.msg["welcome"])
            await self.loop(page)
            await leave_meeting(page)
        except JoinError as exc:
            if exc.session_expired:
                await self.emit_raw({"type": "browser_session", "ok": False, "message": str(exc)})
            await self.emit("error", message=str(exc))
        except Exception as exc:
            log.exception("Session en échec")
            if not self.joined:
                await self.emit("error", message=f"Erreur du bot : {exc}")
            else:
                self.leave_reason = f"Erreur du bot : {exc}"
        finally:
            if self.joined:
                await self.finish()

    async def finish(self) -> None:
        await self.flush_segments(force=True)
        duration = self.recorder.elapsed()
        video, audio = await self.recorder.finalize()
        await self.emit(
            "finished",
            recording=str(video) if video else "",
            audio=str(audio) if audio else "",
            duration=duration,
            reason=self.leave_reason or "",
        )

    async def loop(self, page) -> None:
        last_check = 0.0
        while self.leave_reason is None:
            try:
                kind, data = await asyncio.wait_for(self.queue.get(), 0.5)
            except TimeoutError:
                kind, data = None, None
            if kind == "chat":
                await self.on_chat(page, data)
            elif kind == "caption":
                await self.on_caption(data)
            elif kind == "control":
                await self.on_action(page, data, sender="l'interface d'administration")
            await self.flush_segments()
            if time.monotonic() - last_check >= self.check_interval:
                last_check = time.monotonic()
                await self.check_meeting(page)

    async def check_meeting(self, page) -> None:
        """Détecte la fin de réunion, le bot resté seul et la durée maximale."""
        state = await meeting_state(page)
        self.not_in_call = 0 if state["inCall"] else self.not_in_call + 1
        if state["ended"] or self.not_in_call >= 3:
            self.leave_reason = "Réunion terminée"
            return
        if time.monotonic() - self.started > self.cfg.get("max_duration_minutes", 240) * 60:
            self.leave_reason = "Durée maximale atteinte"
            return
        participants = state["participants"]
        if participants is not None and participants <= 1:
            self.alone_since = self.alone_since or time.monotonic()
            if time.monotonic() - self.alone_since > self.cfg.get("alone_timeout_minutes", 5) * 60:
                self.leave_reason = "Le bot est resté seul dans la réunion"
        else:
            self.alone_since = None

    # ---------- Chat et commandes ----------

    async def say(self, page, text: str) -> None:
        self.sent.add(text)
        try:
            await chat.send_chat(page, text)
        except Exception as exc:
            log.warning("Message de chat non envoyé : %s", exc)

    async def on_chat(self, page, message: dict) -> None:
        text, sender = message.get("text", ""), message.get("sender", "")
        if text in self.sent or cmd.is_self(sender):
            return
        action = cmd.parse_command(text, self.cfg)
        if action is None:
            return
        if not cmd.is_allowed(sender, self.cfg.get("allowed_senders", [])):
            await self.say(page, self.msg["denied"])
            return
        await self.on_action(page, {"action": {"record": "record_start", "stop": "record_stop"}.get(action, action)},
                             sender=sender or "un participant")

    async def on_action(self, page, data: dict, sender: str) -> None:
        action = data.get("action")
        if action == "record_start":
            if self.recorder.is_recording:
                await self.say(page, self.msg["already"])
                return
            try:
                await self.recorder.start()
            except Exception as exc:
                await self.emit("log", level="error", message=f"Enregistrement impossible : {exc}")
                await self.say(page, "⚠️ Impossible de démarrer l'enregistrement.")
                return
            self.had_recording = True
            now = self.recorder.elapsed()
            self.aggregator = CaptionAggregator()
            for block_id, (speaker, text) in self.captions_now.items():
                self.aggregator.prime(block_id, speaker, text, now)
            await self.status("recording", f"Enregistrement demandé par {sender}")
            await self.say(page, self.msg["record"])
        elif action == "record_stop":
            if not self.recorder.is_recording:
                await self.say(page, self.msg["not_recording"])
                return
            await self.flush_segments(force=True)
            await self.recorder.stop()
            await self.status("in_meeting", f"Enregistrement arrêté par {sender}")
            await self.say(page, self.msg["stop"])
        elif action == "leave":
            await self.say(page, self.msg["leave"] if self.had_recording else self.msg["leave_plain"])
            self.leave_reason = f"Départ demandé par {sender}"
        elif action == "status":
            if self.recorder.is_recording:
                await self.say(page, self.msg["status_on"].format(elapsed=fmt_elapsed(self.recorder.elapsed())))
            else:
                await self.say(page, self.msg["status_off"])

    # ---------- Transcript ----------

    async def on_caption(self, caption: dict) -> None:
        block_id, speaker, text = str(caption.get("id")), caption.get("speaker", ""), caption.get("text", "")
        self.captions_now[block_id] = (speaker, text)
        if len(self.captions_now) > 50:
            self.captions_now.pop(next(iter(self.captions_now)))
        if self.recorder.is_recording:
            await self.publish(self.aggregator.update(block_id, speaker, text, self.recorder.elapsed()))

    async def flush_segments(self, force: bool = False) -> None:
        if self.recorder.is_recording:
            await self.publish(self.aggregator.flush(self.recorder.elapsed(), force=force))

    async def publish(self, segments) -> None:
        for seg in segments:
            await self.emit("segment", speaker=seg.speaker, text=seg.text, start=round(seg.start, 2),
                            end=round(seg.end, 2))


# ---------- Point d'entrée du sous-processus ----------


def load_storage_state() -> dict | None:
    if not config.STATE_FILE.exists():
        return None
    return json.loads(config.fernet().decrypt(config.STATE_FILE.read_bytes()))


async def main() -> None:
    import redis.asyncio as aioredis

    from bot.recorder import Recorder

    logging.basicConfig(level=logging.INFO)
    payload = json.loads(os.environ["PDN_PAYLOAD"])
    meeting_id = payload["meeting_id"]
    redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)

    async def emit(event: dict) -> None:
        await redis.xadd(config.BOT_EVENTS, {"data": json.dumps(event)})

    recorder = Recorder(
        config.meeting_dir(meeting_id),
        os.environ["DISPLAY"],
        ["-f", "pulse", "-i", f"{os.environ['PULSE_SINK']}.monitor"],
    )
    session = MeetSession(payload, emit, recorder)

    async def listen_controls() -> None:
        while True:
            item = await redis.blpop(config.control_key(meeting_id), timeout=2)
            if item:
                session.queue.put_nowait(("control", json.loads(item[1])))

    state = load_storage_state()
    if state is None:
        message = "Navigateur du bot non connecté à Google : faites-le depuis les paramètres"
        await emit({"type": "browser_session", "ok": False, "message": message})
        await emit({"type": "error", "meeting_id": meeting_id, "message": message})
        return

    await redis.delete(config.control_key(meeting_id))
    listener = asyncio.create_task(listen_controls())
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False, args=CHROMIUM_ARGS)
            context = await browser.new_context(
                storage_state=state, no_viewport=True, locale="en-US", permissions=["microphone", "camera"]
            )
            page = await context.new_page()
            await session.run(page)
            await browser.close()
    finally:
        listener.cancel()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
