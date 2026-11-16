"""Gestionnaire de bots : lit les commandes Redis et lance une session isolée par réunion.

Chaque session dispose de son propre écran virtuel (Xvfb) et de sa propre sortie audio
(null sink PulseAudio), ce qui permet d'enregistrer plusieurs réunions en parallèle.
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from bot import config
from bot.login import LoginFlow

log = logging.getLogger("bot.manager")
MAX_COMMAND_AGE = 600  # une demande d'entrée plus vieille que 10 min est abandonnée


@dataclass
class Running:
    display: int
    xvfb: asyncio.subprocess.Process
    sink_module: str
    task: asyncio.Task


async def run(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return out.decode().strip()


class Manager:
    def __init__(self, redis):
        self.redis = redis
        self.sessions: dict[int, Running] = {}
        self.login = LoginFlow(self.emit)

    async def emit(self, event: dict) -> None:
        await self.redis.xadd(config.BOT_EVENTS, {"data": json.dumps(event)})

    async def serve(self) -> None:
        log.info("Gestionnaire prêt (%d réunions simultanées max)", config.MAX_CONCURRENT_MEETINGS)
        while True:
            item = await self.redis.blpop(config.BOT_COMMANDS, timeout=5)
            if not item:
                continue
            try:
                await self.handle(json.loads(item[1]))
            except Exception:
                log.exception("Commande non traitée : %s", item[1])

    async def handle(self, command: dict) -> None:
        kind = command.get("type")
        if kind == "join":
            await self.join(command)
        elif kind == "login_start":
            await self.login.start()
        elif kind == "login_finish":
            await self.login.finish()
        elif kind == "login_cancel":
            await self.login.cancel()

    async def join(self, payload: dict) -> None:
        meeting_id = payload["meeting_id"]
        if meeting_id in self.sessions:
            return
        if time.time() - payload.get("ts", time.time()) > MAX_COMMAND_AGE:
            await self.emit({"type": "error", "meeting_id": meeting_id, "message": "Demande d'entrée expirée"})
            return
        if len(self.sessions) >= config.MAX_CONCURRENT_MEETINGS:
            await self.emit({
                "type": "error", "meeting_id": meeting_id,
                "message": f"Nombre maximal de réunions simultanées atteint ({config.MAX_CONCURRENT_MEETINGS})",
            })  # fmt: skip
            return

        used = {s.display for s in self.sessions.values()}
        display = next(n for n in range(100, 200) if n not in used)
        sink = f"meet_{meeting_id}"
        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb", f":{display}", "-screen", "0", f"{config.SCREEN[0]}x{config.SCREEN[1]}x24", "-nolisten", "tcp",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        module = await run("pactl", "load-module", "module-null-sink", f"sink_name={sink}")
        await asyncio.sleep(0.5)
        env = {**os.environ, "DISPLAY": f":{display}", "PULSE_SINK": sink, "PDN_PAYLOAD": json.dumps(payload)}
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "bot.session", env=env)
        task = asyncio.create_task(self.watch(meeting_id, proc))
        self.sessions[meeting_id] = Running(display, xvfb, module, task)
        log.info("Réunion %s : session lancée sur l'écran :%d", meeting_id, display)

    async def watch(self, meeting_id: int, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        running = self.sessions.pop(meeting_id)
        running.xvfb.terminate()
        if running.sink_module:
            await run("pactl", "unload-module", running.sink_module)
        if code != 0:
            await self.emit({
                "type": "error", "meeting_id": meeting_id,
                "message": f"Le bot s'est arrêté de façon inattendue (code {code})",
            })  # fmt: skip
        log.info("Réunion %s : session terminée (code %s)", meeting_id, code)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    await Manager(redis).serve()


if __name__ == "__main__":
    asyncio.run(main())
