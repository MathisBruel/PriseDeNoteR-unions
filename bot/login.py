"""Connexion manuelle du navigateur du bot à Google, via noVNC.

Google refuse les connexions automatisées : l'administrateur se connecte lui-même une fois, dans
un Chromium ordinaire affiché par noVNC. On exporte ensuite la session (cookies) chiffrée, que
chaque bot réutilise.
"""

import asyncio
import json
import logging

from playwright.async_api import async_playwright

from bot import config

log = logging.getLogger("bot.login")
DISPLAY = ":99"
SIZE = "1280x800"
BROWSER_ARGS = ["--no-sandbox", "--password-store=basic", "--no-first-run", "--no-default-browser-check"]


class LoginFlow:
    def __init__(self, emit):
        self.emit = emit
        self.procs: list[asyncio.subprocess.Process] = []
        self.browser: asyncio.subprocess.Process | None = None

    @property
    def active(self) -> bool:
        return bool(self.procs)

    async def _spawn(self, *cmd: str, env: dict | None = None) -> asyncio.subprocess.Process:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        self.procs.append(proc)
        return proc

    async def start(self) -> None:
        if self.active:
            return
        import os

        config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        await self._spawn("Xvfb", DISPLAY, "-screen", "0", f"{SIZE}x24", "-nolisten", "tcp")
        await asyncio.sleep(1)
        async with async_playwright() as pw:
            chromium = pw.chromium.executable_path
        env = {**os.environ, "DISPLAY": DISPLAY}
        self.browser = await self._spawn(
            chromium, *BROWSER_ARGS, f"--user-data-dir={config.PROFILE_DIR}", "--window-position=0,0",
            f"--window-size={SIZE.replace('x', ',')}", "https://accounts.google.com/", env=env,
        )  # fmt: skip
        await self._spawn("x11vnc", "-display", DISPLAY, "-forever", "-shared", "-nopw", "-localhost",
                          "-rfbport", "5900", "-quiet")  # fmt: skip
        await self._spawn("websockify", "--web=/usr/share/novnc", "6080", "localhost:5900")
        await self.emit({"type": "log", "level": "info", "message": "Fenêtre de connexion Google ouverte (noVNC)"})

    async def finish(self) -> None:
        """Ferme le navigateur, exporte la session du profil et vérifie qu'elle est bien connectée."""
        if not self.active:
            return
        import os

        if self.browser and self.browser.returncode is None:
            self.browser.terminate()
            try:
                await asyncio.wait_for(self.browser.wait(), 10)
            except TimeoutError:
                self.browser.kill()
        try:
            async with async_playwright() as pw:
                context = await pw.chromium.launch_persistent_context(
                    str(config.PROFILE_DIR), headless=False, args=BROWSER_ARGS, env={**os.environ, "DISPLAY": DISPLAY}
                )
                state = await context.storage_state()
                await context.close()
            signed_in = any(
                c["name"] == "SID" and c["domain"].endswith("google.com") for c in state.get("cookies", [])
            )
            if signed_in:
                config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                config.STATE_FILE.write_bytes(config.fernet().encrypt(json.dumps(state).encode()))
                await self.emit({"type": "browser_session", "ok": True, "message": "Navigateur du bot connecté"})
            else:
                await self.emit(
                    {"type": "browser_session", "ok": False, "message": "Aucune session Google détectée"}
                )
        except Exception as exc:
            log.exception("Export de session en échec")
            await self.emit({"type": "browser_session", "ok": False, "message": f"Export de session en échec : {exc}"})
        finally:
            await self.cancel()

    async def cancel(self) -> None:
        for proc in reversed(self.procs):
            if proc.returncode is None:
                proc.terminate()
        for proc in self.procs:
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
        self.procs, self.browser = [], None
