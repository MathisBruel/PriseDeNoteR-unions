"""Chat de la réunion : lecture des messages (commandes) et envoi des réponses du bot."""

from playwright.async_api import Page

from bot.meet import selectors as sel

# Surveille le panneau de chat ; chaque nouveau message est remonté une seule fois à Python.
# L'expéditeur est lu sur l'en-tête du groupe de messages (« Nom  10:32 »).
OBSERVER_JS = """(messageSelector) => {
  if (window.__pdnChatStarted) return;
  window.__pdnChatStarted = true;
  const TIME = /\\s*\\d{1,2}:\\d{2}(\\s?[AP]M)?$/i;
  const senderOf = (el) => {
    const named = el.closest('[data-sender-name]');
    if (named) return named.getAttribute('data-sender-name');
    let node = el;
    for (let i = 0; i < 6 && node.parentElement; i++) {
      node = node.parentElement;
      const lines = (node.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
      if (lines.length > 1 && (TIME.test(lines[0]) || TIME.test(lines[1]))) {
        return lines[0].replace(TIME, '').trim();
      }
    }
    return '';
  };
  let first = true;
  const scan = () => {
    for (const el of document.querySelectorAll(messageSelector)) {
      if (el.dataset.pdnSeen) continue;
      el.dataset.pdnSeen = '1';
      if (first) continue;  // messages antérieurs au démarrage : ignorés
      const text = (el.innerText || '').trim();
      if (text) window.__pdnChat({sender: senderOf(el), text});
    }
    first = false;
  };
  scan();
  setInterval(scan, 400);
}"""


async def open_chat(page: Page) -> None:
    """Le panneau doit rester ouvert : Meet n'insère les messages dans la page que s'il est visible."""
    box = page.locator(sel.CHAT_INPUT).first
    if await box.count() and await box.is_visible():
        return
    await page.locator(sel.CHAT_BUTTON).first.click(timeout=10000)
    await box.wait_for(state="visible", timeout=10000)


async def start_chat_observer(page: Page, on_message) -> None:
    """`on_message` reçoit {sender, text} pour chaque nouveau message."""
    await page.expose_function("__pdnChat", on_message)
    await page.evaluate(OBSERVER_JS, sel.CHAT_MESSAGE)


async def send_chat(page: Page, text: str) -> None:
    await open_chat(page)
    box = page.locator(sel.CHAT_INPUT).first
    await box.fill(text)
    await box.press("Enter")
