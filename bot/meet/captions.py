"""Sous-titres en direct de Meet : activation et lecture."""

from playwright.async_api import Page

from bot.meet import selectors as sel

# Chaque bloc de sous-titres contient l'avatar de l'intervenant, son nom puis le texte, que Meet
# réécrit en continu. On remonte l'état de chaque bloc dès qu'il change.
OBSERVER_JS = """(regionSelector) => {
  if (window.__pdnCaptionsStarted) return;
  window.__pdnCaptionsStarted = true;
  let counter = 0;
  const last = new WeakMap();
  const linesOf = (el) => (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const scan = () => {
    const region = document.querySelector(regionSelector);
    if (!region) return;
    const blocks = new Set();
    for (const img of region.querySelectorAll('img')) {
      let block = img.parentElement;
      for (let i = 0; i < 4 && block && block !== region && linesOf(block).length < 2; i++) {
        block = block.parentElement;
      }
      if (block && block !== region) blocks.add(block);
    }
    for (const block of blocks) {
      const lines = linesOf(block);
      if (lines.length < 2) continue;
      const text = lines.slice(1).join(' ');
      if (last.get(block) === text) continue;
      last.set(block, text);
      if (!block.dataset.pdnId) block.dataset.pdnId = String(++counter);
      window.__pdnCaption({id: block.dataset.pdnId, speaker: lines[0], text});
    }
  };
  setInterval(scan, 300);
}"""


async def enable_captions(page: Page) -> bool:
    region = page.locator(sel.CAPTIONS_REGION).first
    if await region.count():
        return True
    button = page.locator(sel.CAPTIONS_BUTTON).first
    try:
        if await button.count():
            await button.click(timeout=5000)
        else:
            await page.keyboard.press("c")  # raccourci Meet
        await region.wait_for(state="attached", timeout=8000)
        return True
    except Exception:
        return False


async def start_captions_observer(page: Page, on_caption) -> None:
    """`on_caption` reçoit {id, speaker, text} à chaque évolution d'un bloc."""
    await page.expose_function("__pdnCaption", on_caption)
    await page.evaluate(OBSERVER_JS, sel.CAPTIONS_REGION)
