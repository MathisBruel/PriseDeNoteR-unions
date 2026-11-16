"""Enregistrement de l'écran virtuel et du son de la réunion avec ffmpeg."""

import asyncio
import time
from pathlib import Path

from bot.config import SCREEN


class Recorder:
    """Enregistre par « parties » (une par !enregistrer/!stop) puis les assemble à la fin.

    `elapsed()` donne le temps enregistré cumulé : c'est l'horodatage des segments de transcript,
    aligné sur le fichier final.
    """

    def __init__(self, out_dir: Path, display: str, audio_input: list[str]):
        self.out_dir = out_dir
        self.display = display
        self.audio_input = audio_input  # p. ex. ["-f", "pulse", "-i", "meet_12.monitor"]
        self.parts: list[tuple[Path, Path]] = []
        self.proc: asyncio.subprocess.Process | None = None
        self._done = 0.0
        self._started: float | None = None

    @property
    def is_recording(self) -> bool:
        return self.proc is not None

    def elapsed(self) -> float:
        current = time.monotonic() - self._started if self._started is not None else 0.0
        return self._done + current

    async def start(self) -> None:
        if self.proc:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        index = len(self.parts) + 1
        video, audio = self.out_dir / f"part_{index:03d}.mkv", self.out_dir / f"part_{index:03d}.ogg"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-thread_queue_size", "1024", "-f", "x11grab", "-draw_mouse", "0", "-framerate", "15",
            "-video_size", f"{SCREEN[0]}x{SCREEN[1]}", "-i", self.display,
            "-thread_queue_size", "1024", *self.audio_input,
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(video),
            "-map", "1:a", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(audio),
        ]  # fmt: skip
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.sleep(0.5)
        if self.proc.returncode is not None:
            error = (await self.proc.stderr.read()).decode(errors="replace")[-500:]
            self.proc = None
            raise RuntimeError(f"ffmpeg n'a pas démarré : {error}")
        self.parts.append((video, audio))
        self._started = time.monotonic()

    async def stop(self) -> None:
        if not self.proc:
            return
        proc, self.proc = self.proc, None
        self._done += time.monotonic() - (self._started or time.monotonic())
        self._started = None
        try:
            proc.stdin.write(b"q")
            await proc.stdin.drain()
            await asyncio.wait_for(proc.wait(), 15)
        except (TimeoutError, ConnectionError, OSError):
            proc.terminate()
            await proc.wait()

    async def finalize(self) -> tuple[Path | None, Path | None]:
        """Assemble les parties en `recording.mp4` + `audio.ogg` ; (None, None) si rien n'a été enregistré."""
        await self.stop()
        parts = [(v, a) for v, a in self.parts if v.exists() and v.stat().st_size > 0]
        if not parts:
            return None, None
        video, audio = self.out_dir / "recording.mp4", self.out_dir / "audio.ogg"
        await self._concat([v for v, _ in parts], video, ["-movflags", "+faststart"])
        await self._concat([a for _, a in parts], audio, [])
        if video.exists() and audio.exists():
            for v, a in self.parts:
                v.unlink(missing_ok=True)
                a.unlink(missing_ok=True)
        return (video if video.exists() else None), (audio if audio.exists() else None)

    async def _concat(self, files: list[Path], target: Path, extra: list[str]) -> None:
        listing = self.out_dir / "concat.txt"
        listing.write_text("".join(f"file '{f.name}'\n" for f in files))
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", *extra, str(target),
            cwd=self.out_dir,
        )  # fmt: skip
        await proc.wait()
        listing.unlink(missing_ok=True)
