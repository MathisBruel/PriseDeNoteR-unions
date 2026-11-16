"""Rendus du compte rendu : Markdown, HTML, PDF, Word, transcript texte."""

import asyncio
import io
import re
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from app.config import get_config
from app.models import Meeting
from app.schemas import ReportData

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=lambda name: bool(name and ".html" in name),
    trim_blocks=True,
    lstrip_blocks=True,
)
MIME = {
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}


def format_ts(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def local_date(meeting: Meeting, fmt: str = "%d/%m/%Y à %H:%M") -> str:
    local = meeting.start_at.replace(tzinfo=UTC).astimezone(ZoneInfo(get_config().timezone))
    return local.strftime(fmt)


def export_name(pattern: str, meeting: Meeting, report: ReportData | None = None) -> str:
    title = (report.title if report and report.title else meeting.title) or "Réunion"
    try:
        name = pattern.format(date=local_date(meeting, "%Y-%m-%d"), time=local_date(meeting, "%Hh%M"), title=title)
    except (KeyError, IndexError, ValueError):
        name = f"{local_date(meeting, '%Y-%m-%d')} - {title}"
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name)
    return re.sub(r" {2,}", " ", name).strip()[:150] or "Compte rendu"


def _context(meeting: Meeting, report: ReportData) -> dict:
    return {
        "title": report.title or meeting.title,
        "date": local_date(meeting),
        "duration": format_ts(meeting.recording_seconds) if meeting.recording_seconds else "",
        "report": report,
    }


def render_markdown(meeting: Meeting, report: ReportData) -> str:
    ctx = _context(meeting, report)
    meta = f"**Date :** {ctx['date']}"
    if ctx["duration"]:
        meta += f" · **Durée enregistrée :** {ctx['duration']}"
    out = [f"# {ctx['title']}", "", meta]
    if report.participants:
        out += ["", f"**Participants :** {', '.join(report.participants)}"]
    out += ["", "## Résumé", "", report.summary or "—"]
    if report.topics:
        out += ["", "## Points abordés"]
        for topic in report.topics:
            out += ["", f"### {topic.title}", "", *(f"- {point}" for point in topic.points)]

    def bullets(heading: str, items: list[str]) -> None:
        if items:
            out.extend(["", f"## {heading}", "", *(f"- {item}" for item in items)])

    bullets("Décisions", report.decisions)
    if report.action_items:
        out += ["", "## Actions", "", "| Action | Responsable | Échéance |", "|---|---|---|"]
        out += [f"| {a.task} | {a.owner or '—'} | {a.due or '—'} |" for a in report.action_items]
    bullets("Questions ouvertes", report.open_questions)
    return "\n".join(out) + "\n"


def render_html(meeting: Meeting, report: ReportData) -> str:
    return _env.get_template("report.html.j2").render(**_context(meeting, report))


def _pdf_sync(html: str) -> bytes:
    from weasyprint import HTML  # import tardif : bibliothèques système requises

    return HTML(string=html).write_pdf()


async def render_pdf(meeting: Meeting, report: ReportData) -> bytes:
    return await asyncio.to_thread(_pdf_sync, render_html(meeting, report))


def render_docx(meeting: Meeting, report: ReportData) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    ctx = _context(meeting, report)
    # Un gabarit personnalisé (logo, styles) peut être déposé dans data/templates/report.docx.
    custom = get_config().data_dir / "templates" / "report.docx"
    doc = Document(str(custom)) if custom.exists() else Document()
    doc.styles["Normal"].font.size = Pt(10.5)

    kicker = doc.add_paragraph().add_run("COMPTE RENDU DE RÉUNION")
    kicker.bold, kicker.font.size, kicker.font.color.rgb = True, Pt(8), RGBColor(0x1D, 0x4E, 0xD8)
    doc.add_heading(ctx["title"], level=0)
    meta = ctx["date"] + (f" · Durée enregistrée : {ctx['duration']}" if ctx["duration"] else "")
    doc.add_paragraph(meta)
    if report.participants:
        para = doc.add_paragraph()
        para.add_run("Participants : ").bold = True
        para.add_run(", ".join(report.participants))

    doc.add_heading("Résumé", level=1)
    for block in (report.summary or "—").split("\n"):
        if block.strip():
            doc.add_paragraph(block.strip())

    if report.topics:
        doc.add_heading("Points abordés", level=1)
        for topic in report.topics:
            doc.add_heading(topic.title, level=2)
            for point in topic.points:
                doc.add_paragraph(point, style="List Bullet")

    def bullets(heading: str, items: list[str]) -> None:
        if items:
            doc.add_heading(heading, level=1)
            for item in items:
                doc.add_paragraph(item, style="List Bullet")

    bullets("Décisions", report.decisions)
    if report.action_items:
        doc.add_heading("Actions", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for cell, label in zip(table.rows[0].cells, ("Action", "Responsable", "Échéance"), strict=True):
            cell.text = label
        for item in report.action_items:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = item.task, item.owner or "—", item.due or "—"
    bullets("Questions ouvertes", report.open_questions)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def transcript_text(segments, with_time: bool = True) -> str:
    """Segments (objets avec speaker/text/start_s) → « [mm:ss] Intervenant : texte »."""
    lines = []
    for seg in segments:
        prefix = f"[{format_ts(seg.start_s)}] " if with_time else ""
        speaker = f"{seg.speaker} : " if seg.speaker else ""
        lines.append(f"{prefix}{speaker}{seg.text}")
    return "\n".join(lines)


async def render(fmt: str, meeting: Meeting, report: ReportData) -> bytes:
    if fmt == "md":
        return render_markdown(meeting, report).encode()
    if fmt == "docx":
        return await asyncio.to_thread(render_docx, meeting, report)
    if fmt == "pdf":
        return await render_pdf(meeting, report)
    raise ValueError(f"Format inconnu : {fmt}")
