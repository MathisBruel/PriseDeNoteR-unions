"""Transcription Whisper locale + attribution des intervenants à partir des sous-titres Meet."""

import asyncio
from dataclasses import dataclass


@dataclass
class Seg:
    start: float
    end: float
    text: str
    speaker: str = ""


_models: dict = {}


def _transcribe_sync(audio_path: str, model_name: str, device: str, language: str) -> list[Seg]:
    from faster_whisper import WhisperModel  # import tardif : dépendance lourde

    key = (model_name, device)
    if key not in _models:
        compute = "float16" if device == "cuda" else "int8"
        _models[key] = WhisperModel(model_name, device=device, compute_type=compute)
    segments, _info = _models[key].transcribe(audio_path, language=language or None, vad_filter=True)
    return [Seg(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


async def transcribe(audio_path: str, model_name: str, device: str, language: str) -> list[Seg]:
    return await asyncio.to_thread(_transcribe_sync, audio_path, model_name, device, language)


def assign_speakers(whisper: list[Seg], captions: list[Seg], tolerance: float = 15.0) -> list[Seg]:
    """Attribue à chaque segment Whisper l'intervenant du sous-titre qui le recouvre le plus.

    Sans recouvrement, on prend le sous-titre le plus proche dans la limite de `tolerance` secondes.
    """
    for seg in whisper:
        best, best_overlap = "", 0.0
        for cap in captions:
            overlap = min(seg.end, cap.end) - max(seg.start, cap.start)
            if overlap > best_overlap:
                best, best_overlap = cap.speaker, overlap
        if not best and captions:
            mid = (seg.start + seg.end) / 2
            nearest = min(captions, key=lambda c: abs((c.start + c.end) / 2 - mid))
            if abs((nearest.start + nearest.end) / 2 - mid) <= tolerance:
                best = nearest.speaker
        seg.speaker = best
    return whisper


def merge_consecutive(segments: list[Seg], max_gap: float = 2.0, max_chars: int = 600) -> list[Seg]:
    """Regroupe les segments consécutifs d'un même intervenant en paragraphes."""
    merged: list[Seg] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if (
            last
            and last.speaker == seg.speaker
            and seg.start - last.end <= max_gap
            and len(last.text) + len(seg.text) < max_chars
        ):
            last.text = f"{last.text} {seg.text}"
            last.end = seg.end
        else:
            merged.append(Seg(seg.start, seg.end, seg.text, seg.speaker))
    return merged
