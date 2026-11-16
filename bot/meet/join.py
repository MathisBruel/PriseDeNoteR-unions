"""Entrée et sortie d'une réunion Google Meet."""

import asyncio
import time

from playwright.async_api import Page

from bot.meet import selectors as sel

STATE_JS = """([leave, tile, endedTexts]) => {
  const body = document.body ? document.body.innerText : '';
  const ids = new Set([...document.querySelectorAll(tile)].map(e => e.getAttribute('data-participant-id')));
  return {
    inCall: !!document.querySelector(leave),
    participants: ids.size || null,
    ended: endedTexts.some(t => body.includes(t)),
  };
}"""


class JoinError(Exception):
    def __init__(self, message: str, session_expired: bool = False):
        super().__init__(message)
        self.session_expired = session_expired


def meeting_url(url: str) -> str:
    if "meet.google.com" in url and "hl=" not in url:
        return url + ("&" if "?" in url else "?") + "hl=en"
    return url


async def _body_contains(page: Page, texts: list[str]) -> str | None:
    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    return next((t for t in texts if t in body), None)


async def _dismiss_dialogs(page: Page) -> None:
    button = page.get_by_role("button", name=sel.DISMISS_BUTTON_NAME)
    for index in range(await button.count()):
        try:
            await button.nth(index).click(timeout=1000)
        except Exception:
            pass


async def _click_if_present(page: Page, selector: str) -> None:
    target = page.locator(selector).first
    try:
        if await target.count() and await target.is_visible():
            await target.click(timeout=2000)
    except Exception:
        pass


async def join_meeting(page: Page, url: str, admission_timeout_s: float, on_waiting=None) -> None:
    """Ouvre la réunion, coupe micro et caméra, demande à entrer et attend d'être admis."""
    await page.goto(meeting_url(url), wait_until="domcontentloaded")

    join = page.get_by_role("button", name=sel.JOIN_BUTTON_NAME).first
    deadline = time.monotonic() + 60
    while True:
        if sel.SIGN_IN_HOST in page.url:
            raise JoinError(
                "Session Google expirée : reconnectez le navigateur du bot dans les paramètres",
                session_expired=True,
            )
        await _dismiss_dialogs(page)
        if await join.count() and await join.is_visible():
            break
        denied = await _body_contains(page, sel.DENIED_TEXTS)
        if denied:
            raise JoinError(f"Entrée impossible : {denied}")
        if time.monotonic() > deadline:
            raise JoinError("Écran d'entrée de la réunion introuvable (lien invalide ou interface Meet modifiée)")
        await asyncio.sleep(1)

    await _click_if_present(page, sel.MIC_OFF)
    await _click_if_present(page, sel.CAM_OFF)
    await join.click()

    leave = page.locator(sel.LEAVE_BUTTON).first
    started = time.monotonic()
    notified = False
    while not (await leave.count() and await leave.is_visible()):
        denied = await _body_contains(page, sel.DENIED_TEXTS)
        if denied:
            raise JoinError(f"Entrée refusée : {denied}")
        waited = time.monotonic() - started
        if waited > admission_timeout_s:
            raise JoinError("Personne n'a admis le bot dans la réunion avant l'expiration du délai")
        if waited > 2 and not notified and on_waiting:
            notified = True
            await on_waiting()
        await _dismiss_dialogs(page)
        await asyncio.sleep(1)


async def meeting_state(page: Page) -> dict:
    return await page.evaluate(STATE_JS, [sel.LEAVE_BUTTON, sel.PARTICIPANT_TILE, sel.ENDED_TEXTS])


async def leave_meeting(page: Page) -> None:
    await _click_if_present(page, sel.LEAVE_BUTTON)
