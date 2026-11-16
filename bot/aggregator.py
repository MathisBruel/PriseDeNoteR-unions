"""Transforme le flux de sous-titres Meet (blocs réécrits en continu) en segments stables.

Meet réécrit le même bloc tant que la personne parle : on ne publie un segment que lorsqu'un bloc
est resté inchangé `idle` secondes, ou par phrases entières quand il devient long.
"""

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"[.!?…]\s")


@dataclass
class Segment:
    speaker: str
    text: str
    start: float
    end: float


@dataclass
class _Block:
    speaker: str
    start: float
    last: float
    committed: str = ""  # partie du texte Meet déjà publiée
    pending: str = ""  # partie non encore publiée
    text: str = ""  # dernier texte complet du bloc


def strip_committed(committed: str, text: str) -> str:
    """Retire de `text` la partie déjà publiée, même si Meet a tronqué ou retouché le début du bloc."""
    if not committed:
        return text
    if text.startswith(committed):
        return text[len(committed):]
    # Meet a fait défiler le début : on cherche la fin de la partie publiée dans le nouveau texte.
    for size in (60, 30, 15):
        tail = committed[-size:]
        if len(tail) < 8:
            continue
        index = text.rfind(tail)
        if index != -1:
            return text[index + len(tail):]
    # Retouche de ponctuation/casse dans la partie publiée : on se cale sur la longueur.
    if len(text) >= len(committed):
        cut = text.find(" ", len(committed) - 1)
        return text[cut:] if cut != -1 else ""
    return ""


class CaptionAggregator:
    def __init__(self, idle: float = 2.5, max_chars: int = 320, keep_tail: int = 60):
        self.idle = idle
        self.max_chars = max_chars
        self.keep_tail = keep_tail
        self.blocks: dict[str, _Block] = {}

    def prime(self, block_id: str, speaker: str, text: str, now: float) -> None:
        """Bloc déjà affiché avant le début de l'enregistrement : son texte actuel ne sera pas publié."""
        text = " ".join(text.split())
        self.blocks[block_id] = _Block(speaker=speaker, start=now, last=now, committed=text, text=text)

    def update(self, block_id: str, speaker: str, text: str, now: float) -> list[Segment]:
        """Nouvel état d'un bloc de sous-titres ; renvoie les segments devenus définitifs."""
        text = " ".join(text.split())
        block = self.blocks.get(block_id)
        if block is None:
            block = self.blocks[block_id] = _Block(speaker=speaker, start=now, last=now)
        block.speaker, block.text = speaker or block.speaker, text
        raw = strip_committed(block.committed, text)
        # Meet ajoute souvent la ponctuation après coup (« tous » → « tous. ») : on ne la republie pas.
        stripped = raw.lstrip(" .,;:!?…")
        offset = len(text) - len(stripped)  # position du texte en attente dans le bloc Meet
        pending = stripped.rstrip()
        if pending != block.pending:
            if not block.pending:
                block.start = now
            block.pending, block.last = pending, now
        if len(pending) <= self.max_chars:
            return []
        # Bloc long : on publie jusqu'à la dernière fin de phrase, hors queue encore instable.
        ends = [m.end() for m in SENTENCE_END.finditer(pending[: len(pending) - self.keep_tail])]
        if not ends:
            return []
        cut = ends[-1]
        segment = Segment(block.speaker, pending[:cut].strip(), block.start, now)
        block.committed = text[: offset + cut]
        block.pending = pending[cut:].strip()
        block.start = now
        return [segment]

    def flush(self, now: float, force: bool = False) -> list[Segment]:
        """Publie les blocs inactifs (ou tous si `force`, p. ex. à l'arrêt de l'enregistrement)."""
        out: list[Segment] = []
        for block_id, block in list(self.blocks.items()):
            if not force and now - block.last < self.idle:
                continue
            if block.pending:
                out.append(Segment(block.speaker, block.pending, block.start, block.last))
                block.committed = block.text
                block.pending = ""
                block.start = now
            if force or now - block.last > 120:
                del self.blocks[block_id]
        out.sort(key=lambda s: s.start)
        return out
